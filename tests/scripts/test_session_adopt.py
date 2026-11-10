"""Adopting a harness (or any linked) worktree in place, and closing it (home B). Real git, no stubs."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SESSION = ROOT / "skills" / "swarm" / "scripts" / "session.py"


def run(*args, cwd=None):
    e = dict(os.environ, PYTHONIOENCODING="utf-8")
    e.pop("SUPERLITE_NO_AUTO_SESSION", None)
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
def harness(repo):
    """<hub>/.claude/worktrees/x on worktree-x: cut from main, then one commit."""
    wt = repo / ".claude" / "worktrees" / "x"
    git(repo, "worktree", "add", "-q", "-b", "worktree-x", str(wt), "main")
    (wt / "f.txt").write_text("one\n")
    git(wt, "add", "f.txt")
    git(wt, "commit", "-q", "-m", "work")
    return wt


def reg_of(r):
    return json.loads((r / ".superlite" / "sessions.json").read_text(encoding="utf-8"))


def row_of(r, slug):
    return next((s for s in reg_of(r)["sessions"] if s["slug"] == slug), None)


def snapshot(r):
    return {"worktrees": git(r, "worktree", "list", "--porcelain"), "branches": git(r, "branch", "-a", "-v"),
            "refs": git(r, "for-each-ref"), "stash": git(r, "stash", "list")}


def test_adopt_harness_row(repo, harness):
    cp = run("ensure", "--slug", "x", cwd=harness)
    assert cp.returncode == 0, cp.both
    row = row_of(repo, "x")
    assert row["owned"] is False and row["adopted_branch"] is True and row["harness"] is True
    assert row["branch"] == "worktree-x" and row["base"] == "main"
    assert row["base_sha"] == git(repo, "rev-parse", "main").strip()
    assert row["worktree"] == harness.as_posix()
    assert f"SESSION worktree={harness.as_posix()} slug=x branch=worktree-x" in cp.out


def test_adopt_detached(repo, tmp_path):
    wt = repo / ".claude" / "worktrees" / "d"
    git(repo, "worktree", "add", "-q", "--detach", str(wt), "main")
    head = git(wt, "rev-parse", "HEAD").strip()
    assert run("ensure", "--slug", "d", cwd=wt).returncode == 0
    row = row_of(repo, "d")
    assert row["branch"] is None and row["detached"] is True and row["head"] == head
    assert row["adopted_branch"] is True and row["owned"] is False
    assert run("close", "--slug", "d", cwd=wt).returncode == 0
    assert row_of(repo, "d") is None and wt.is_dir()


def test_adopt_out_of_tree(repo, tmp_path):
    wt = tmp_path.resolve() / "elsewhere"
    git(repo, "worktree", "add", "-q", "-b", "feat-o", str(wt), "main")
    assert run("ensure", "--slug", "o", cwd=wt).returncode == 0
    row = row_of(repo, "o")
    assert row["harness"] is False and row["adopted_branch"] is True and row["branch"] == "feat-o"


def test_adopt_never_changes_git(repo, harness):
    (harness / "dirty.txt").write_text("wip\n")
    before = snapshot(repo)
    assert run("ensure", "--slug", "x", cwd=harness).returncode == 0
    assert run("close", "--slug", "x", cwd=harness).returncode == 0
    assert snapshot(repo) == before
    assert (harness / "dirty.txt").read_text() == "wip\n"


def test_close_adopted_archives_and_unregisters(repo, harness):
    run("ensure", "--slug", "x", cwd=harness)
    (harness / ".superlite").mkdir(exist_ok=True)
    (harness / ".superlite" / "progress.md").write_text("- x\n")
    cp = run("close", "--slug", "x", cwd=harness)
    assert cp.returncode == 0, cp.both
    assert "session: x lives in a harness worktree; leave it with ExitWorktree" in cp.out
    store = repo / ".superlite" / "session-store" / "x"
    assert (store / "progress.md").read_text() == "- x\n"
    manifest = json.loads((store / "session.json").read_text(encoding="utf-8"))
    assert manifest["slug"] == "x" and manifest["wip_ref"] is None
    assert row_of(repo, "x") is None
    assert not (harness / ".superlite" / "progress.md").exists()


def test_close_adopted_keeps_dirty_tree(repo, harness):
    run("ensure", "--slug", "x", cwd=harness)
    (harness / "f.txt").write_text("edited\n")
    (harness / "new.txt").write_text("untracked\n")
    assert run("close", "--slug", "x", cwd=harness).returncode == 0
    assert harness.is_dir()
    assert (harness / "f.txt").read_text() == "edited\n" and (harness / "new.txt").is_file()
    assert git(repo, "branch", "--list", "worktree-x").strip()
    assert git(repo, "for-each-ref", "refs/superlite/wip").strip() == ""
    assert git(harness, "status", "--porcelain").strip()


def test_hub_busy_still_adopts_in_harness_worktree(repo, harness):
    assert run("ensure", "--slug", "hubwork", cwd=repo).returncode == 0
    assert reg_of(repo)["active"] == "hubwork"
    cp = run("ensure", "--slug", "y", cwd=harness)
    assert cp.returncode == 0, cp.both
    row = row_of(repo, "y")
    assert row["worktree"] == harness.as_posix() and row["branch"] == "worktree-x"
    assert git(repo, "branch", "--list", "superlite/sessions/y").strip() == ""
    assert not (repo / ".superlite" / "worktrees" / "sessions" / "y").exists()
    assert reg_of(repo)["active"] == "hubwork"


def test_use_restores_adopted_row_in_place(repo, harness):
    run("ensure", "--slug", "x", cwd=harness)
    (harness / ".superlite").mkdir(exist_ok=True)
    (harness / ".superlite" / "progress.md").write_text("- x\n")
    run("close", "--slug", "x", cwd=harness)
    before = git(repo, "worktree", "list", "--porcelain")
    cp = run("ensure", "--slug", "x", cwd=harness)
    assert cp.returncode == 0, cp.both
    assert "REDIRECT" not in cp.out
    row = row_of(repo, "x")
    assert row["worktree"] == harness.as_posix() and row["owned"] is False and row["branch"] == "worktree-x"
    assert (harness / ".superlite" / "progress.md").read_text() == "- x\n"
    assert git(repo, "worktree", "list", "--porcelain") == before
    assert git(repo, "branch", "--list", "superlite/sessions/x").strip() == ""


def test_use_archived_detached_gone_refuses_without_made_up_branch(repo, tmp_path):
    wt = repo / ".claude" / "worktrees" / "d"
    git(repo, "worktree", "add", "-q", "--detach", str(wt), "main")
    assert run("ensure", "--slug", "d", cwd=wt).returncode == 0
    assert run("close", "--slug", "d", cwd=wt).returncode == 0
    git(repo, "worktree", "remove", "--force", str(wt))
    before = snapshot(repo)
    cp = run("use", "--slug", "d", "--root", repo)
    assert cp.returncode == 2, cp.both
    assert snapshot(repo) == before
    assert git(repo, "branch", "--list", "superlite/sessions/d").strip() == ""


def test_ensure_restores_archived_detached_in_place(repo):
    wt = repo / ".claude" / "worktrees" / "d"
    git(repo, "worktree", "add", "-q", "--detach", str(wt), "main")
    assert run("ensure", "--slug", "d", cwd=wt).returncode == 0
    assert run("close", "--slug", "d", cwd=wt).returncode == 0
    before = git(repo, "worktree", "list", "--porcelain")
    cp = run("ensure", "--slug", "d", cwd=wt)
    assert cp.returncode == 0, cp.both
    row = row_of(repo, "d")
    assert row["branch"] is None and row["detached"] is True
    assert git(repo, "worktree", "list", "--porcelain") == before
    assert git(repo, "branch", "--list", "superlite/sessions/d").strip() == ""
