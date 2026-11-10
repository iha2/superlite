"""Claude Code creates the session worktree, superlite adopts it (D5 revised). Real git in temp repos."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "swarm" / "scripts"
SESSION = SCRIPTS / "session.py"
sys.path.insert(0, str(SCRIPTS))

import claude_create  # noqa: E402
import host_harness as harness  # noqa: E402


def run(*args, cwd=None, claude=True):
    e = dict(os.environ, PYTHONIOENCODING="utf-8")
    e.pop("SUPERLITE_NO_AUTO_SESSION", None)
    e.pop("CLAUDECODE", None)
    if claude:
        e["CLAUDECODE"] = "1"
    cp = subprocess.run([sys.executable, str(SESSION), *map(str, args)], capture_output=True, cwd=cwd, env=e,
                        timeout=120)
    cp.out, cp.err = cp.stdout.decode("utf-8"), cp.stderr.decode("utf-8")
    cp.both = cp.out + cp.err
    return cp


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path):
    r = tmp_path.resolve() / "app"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "t")
    (r / ".gitignore").write_text(".superlite/\n.claude/\n")
    (r / "app.py").write_text("print('hi')\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    return r


@pytest.fixture
def busy(repo):
    """The hub has an open session, so the next ensure would cut a parallel worktree."""
    assert run("ensure", "--slug", "first", cwd=repo).returncode == 0
    return repo


def enter_worktree(repo, name):
    """What Claude's EnterWorktree name=<n> does with worktree.baseRef head."""
    wt = repo / ".claude" / "worktrees" / name
    git(repo, "worktree", "add", "-q", str(wt.relative_to(repo)), "-b", f"worktree-{name}", "HEAD")
    return wt


def row_of(r, slug):
    reg = json.loads((r / ".superlite" / "sessions.json").read_text(encoding="utf-8"))
    return next((s for s in reg["sessions"] if s["slug"] == slug), None)


def snapshot(r):
    return {"worktrees": git(r, "worktree", "list", "--porcelain"), "branches": git(r, "branch", "-a", "-v"),
            "refs": git(r, "for-each-ref")}


def set_pref(repo, value):
    assert run("branch-name", value, cwd=repo).returncode == 0


def test_create_line_shape():
    assert claude_create.create_line("a", "main") == "session: create EnterWorktree name=a base=main"


def test_ask_branch_name_when_unset(busy):
    before = snapshot(busy)
    cp = run("ensure", "--slug", "a", cwd=busy)
    assert cp.returncode == 4, cp.both
    assert "ASK branch-name slug=a: " in cp.out and "\nask: " in cp.out.replace("\r\n", "\n") and "--branch-name" in cp.out
    assert snapshot(busy) == before and row_of(busy, "a") is None


def test_branch_name_flag_saves_and_prints_create_line(busy, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    a = SimpleNamespace(branch_name="superlite")
    assert harness.branch_pref() is None
    assert claude_create.gate(busy, "a", "main", a) == 0
    assert harness.branch_pref() == "superlite"


def test_gate_rejects_bad_pattern(busy, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    assert claude_create.gate(busy, "a", "main", SimpleNamespace(branch_name="nope")) == 2
    assert harness.branch_pref() is None


def test_create_line_changes_nothing(busy):
    set_pref(busy, "superlite")
    before = snapshot(busy)
    cp = run("ensure", "--slug", "a", cwd=busy)
    assert cp.returncode == 0, cp.both
    assert "session: create EnterWorktree name=a base=main\n" in cp.out.replace("\r\n", "\n") and "SESSION worktree=" not in cp.out
    assert snapshot(busy) == before and row_of(busy, "a") is None
    cp = run("create", "--slug", "b", "--worktree", cwd=busy)
    assert cp.returncode == 0 and "session: create EnterWorktree name=b base=main" in cp.out
    assert snapshot(busy) == before and row_of(busy, "b") is None


def test_hub_off_base_falls_back(repo):
    set_pref(repo, "worktree")
    git(repo, "switch", "-q", "-c", "side")
    (repo / "x.txt").write_text("x\n")
    git(repo, "add", "x.txt")
    git(repo, "commit", "-q", "-m", "side")
    assert claude_create.hub_on_base(repo, "main") is False
    cp = run("create", "--slug", "a", "--worktree", "--base", "main", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert "session: note hub on side, not main; superlite cut the worktree" in cp.out
    assert "EnterWorktree name=" not in cp.out
    assert (repo / ".claude" / "worktrees" / "sessions-a").is_dir() and row_of(repo, "a")["owned"] is True


def test_adopt_applies_superlite_branch(busy):
    set_pref(busy, "superlite")
    wt = enter_worktree(busy, "a")
    cp = run("ensure", "--slug", "a", cwd=wt)
    assert cp.returncode == 0, cp.both
    row = row_of(busy, "a")
    assert row["branch"] == "superlite/sessions/a" and row["harness"] is True
    assert row["owned"] is False and row["adopted_branch"] is True
    assert git(wt, "branch", "--show-current").strip() == "superlite/sessions/a"
    assert "SESSION worktree=" in cp.out and "branch=superlite/sessions/a" in cp.out


def test_adopt_applies_pattern(busy):
    set_pref(busy, "feat/{slug}")
    wt = enter_worktree(busy, "a")
    assert run("ensure", "--slug", "a", cwd=wt).returncode == 0
    assert row_of(busy, "a")["branch"] == "feat/a"


def test_adopt_keeps_worktree_branch(busy):
    set_pref(busy, "worktree")
    wt = enter_worktree(busy, "a")
    assert run("ensure", "--slug", "a", cwd=wt).returncode == 0
    assert row_of(busy, "a")["branch"] == "worktree-a"
    assert git(wt, "branch", "--show-current").strip() == "worktree-a"


def test_adopt_leaves_harness_branch(busy):
    set_pref(busy, "superlite")
    wt = enter_worktree(busy, "a")
    assert run("ensure", "--slug", "a", cwd=wt).returncode == 0
    assert "worktree-a" in git(busy, "branch", "--list", "worktree-a")


def test_close_adopted_unregisters_only(busy):
    set_pref(busy, "superlite")
    wt = enter_worktree(busy, "a")
    assert run("ensure", "--slug", "a", cwd=wt).returncode == 0
    cp = run("close", "--slug", "a", cwd=wt)
    assert cp.returncode == 0, cp.both
    assert row_of(busy, "a") is None and wt.is_dir()
    assert "session: a lives in a harness worktree; leave it with ExitWorktree" in cp.both
    assert "superlite/sessions/a" in git(busy, "branch", "--list")


def test_off_claude_code_unchanged(busy):
    cp = run("ensure", "--slug", "a", "--move", "yes", cwd=busy, claude=False)
    assert cp.returncode == 0, cp.both
    assert "ASK branch-name" not in cp.both and "EnterWorktree" not in cp.both and "session: note" not in cp.both
    assert (busy / ".claude" / "worktrees" / "sessions-a").is_dir()
    assert row_of(busy, "a")["branch"] == "superlite/sessions/a"
    assert harness.branch_pref() is None


def test_off_claude_code_adopt_keeps_branch(busy):
    set_pref(busy, "superlite")
    wt = enter_worktree(busy, "b")
    assert run("ensure", "--slug", "b", cwd=wt, claude=False).returncode == 0
    assert row_of(busy, "b")["branch"] == "worktree-b"
