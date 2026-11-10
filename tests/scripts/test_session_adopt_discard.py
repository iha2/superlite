"""discard and status on adopted (harness) rows: no worktree removed, no branch deleted."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SESSION = ROOT / "skills" / "swarm" / "scripts" / "session.py"
HINT = "session: x lives in a harness worktree; leave it with ExitWorktree"


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def run(repo, *args):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, str(SESSION), *args, "--root", str(repo)], capture_output=True,
                          text=True, encoding="utf-8", env=env, timeout=120)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERLITE_HOME", str(tmp_path / "_home"))
    r = tmp_path / "app"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "t")
    (r / ".gitignore").write_text(".superlite/\n.claude/\n")
    (r / "app.py").write_text("x\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    git(r, "worktree", "add", "-q", "-b", "worktree-x", str(r / ".claude" / "worktrees" / "x"))
    return r


def write_rows(repo, *rows, active=None):
    d = repo / ".superlite"
    d.mkdir(exist_ok=True)
    (d / "sessions.json").write_text(json.dumps({"v": 2, "base": "main", "active": active, "sessions": list(rows),
                                                 "host_worktree": repo.as_posix()}), encoding="utf-8")


def adopted_row(repo, **kw):
    row = {"slug": "x", "branch": "worktree-x", "worktree": (repo / ".claude" / "worktrees" / "x").as_posix(),
           "owned": False, "adopted_branch": True, "harness": True, "has_stash": False, "status": "parallel",
           "run": "open", "store": (repo / ".superlite" / "session-store" / "x").as_posix()}
    return {**row, **kw}


def reg(repo):
    return json.loads((repo / ".superlite" / "sessions.json").read_text(encoding="utf-8"))


def state_of(repo):
    return git(repo, "worktree", "list", "--porcelain"), git(repo, "branch", "--list")


def test_discard_adopted_keeps_worktree_and_branch(repo):
    write_rows(repo, adopted_row(repo))
    store = repo / ".superlite" / "session-store" / "x"
    store.mkdir(parents=True)
    (store / "note.txt").write_text("n")
    before = state_of(repo)
    cp = run(repo, "discard", "--slug", "x")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert HINT in cp.stdout.splitlines()
    assert (repo / ".claude" / "worktrees" / "x").is_dir() and "worktree-x" in git(repo, "branch", "--list")
    assert state_of(repo) == before
    assert reg(repo)["sessions"] == [] and not store.exists()


def test_discard_archived_adopted_keeps_branch(repo):
    write_rows(repo)
    store = repo / ".superlite" / "session-store" / "x"
    store.mkdir(parents=True)
    (store / "session.json").write_text(json.dumps({**adopted_row(repo), "closed_at": 1, "wip_ref": None}),
                                        encoding="utf-8")
    before = state_of(repo)
    cp = run(repo, "discard", "--slug", "x")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert HINT in cp.stdout.splitlines()
    assert state_of(repo) == before and not store.exists()


def test_discard_adopted_detached(repo):
    head = git(repo, "rev-parse", "HEAD").strip()
    wt = repo.parent / "outside"
    git(repo, "worktree", "add", "-q", "--detach", str(wt))
    write_rows(repo, adopted_row(repo, slug="d", branch=None, detached=True, head=head, harness=False,
                                 worktree=wt.as_posix(), store=(repo / ".superlite" / "session-store" / "d").as_posix()))
    before = state_of(repo)
    cp = run(repo, "discard", "--slug", "d")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert "ExitWorktree" not in cp.stdout and "Traceback" not in cp.stderr
    assert wt.is_dir() and state_of(repo) == before and reg(repo)["sessions"] == []


def test_owned_discard_unchanged(repo):
    assert run(repo, "create", "--slug", "o", "--worktree").returncode == 0
    wt = Path(reg(repo)["sessions"][0]["worktree"])
    assert wt.is_dir()
    cp = run(repo, "discard", "--slug", "o")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert not wt.exists() and "superlite/sessions/o" not in git(repo, "branch", "--list")
    assert "ExitWorktree" not in cp.stdout and reg(repo)["sessions"] == []


def test_status_marks_adopted(repo):
    write_rows(repo, adopted_row(repo),
               adopted_row(repo, slug="y", branch="worktree-y", harness=False))
    out = run(repo, "status").stdout
    lines = {ln.split()[2]: ln for ln in out.splitlines() if ln.startswith("session: parallel")}
    assert lines["x"].endswith("| harness") and lines["y"].endswith("| adopted")


def run_in(cwd, *args):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, str(SESSION), *args], capture_output=True, text=True,
                          encoding="utf-8", env=env, timeout=120, cwd=cwd)


def test_use_archived_adopted_in_place(repo):
    wt = repo / ".claude" / "worktrees" / "x"
    cp = run_in(wt, "ensure", "--slug", "x")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    cp = run_in(wt, "close", "--slug", "x")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert reg(repo)["sessions"] == []
    before = state_of(repo)
    cp = run_in(wt, "use", "--slug", "x")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert "REDIRECT" not in cp.stdout
    assert any(ln.startswith("SESSION worktree=") and "slug=x" in ln for ln in cp.stdout.splitlines())
    row = reg(repo)["sessions"][0]
    assert row["slug"] == "x" and row["status"] == "parallel" and row["run"] == "open"
    assert row["worktree"].replace("\\", "/").endswith(".claude/worktrees/x")
    assert state_of(repo) == before
