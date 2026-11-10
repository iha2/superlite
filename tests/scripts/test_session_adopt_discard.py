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


def ok(cp):
    assert cp.returncode == 0, cp.stdout + cp.stderr
    return cp


def detached_row(repo, wt):
    head = git(repo, "rev-parse", "HEAD").strip()
    return adopted_row(repo, slug="d", branch=None, detached=True, head=head, harness=False,
                       worktree=wt.as_posix(), store=(repo / ".superlite" / "session-store" / "d").as_posix())


def test_use_registered_detached_in_place_or_redirect(repo):
    wt = repo.parent / "outside"
    git(repo, "worktree", "add", "-q", "--detach", str(wt))
    write_rows(repo, detached_row(repo, wt))
    cp = run_in(wt, "use", "--slug", "d")
    assert cp.returncode == 0 and "Traceback" not in cp.stderr, cp.stdout + cp.stderr
    assert any(ln.startswith("SESSION worktree=") and "slug=d" in ln for ln in cp.stdout.splitlines())
    cp = run(repo, "use", "--slug", "d")
    assert "Traceback" not in cp.stderr and any(ln.startswith("REDIRECT worktree=") for ln in cp.stdout.splitlines())
    assert reg(repo)["sessions"][0]["slug"] == "d"


def test_use_registered_detached_missing_checkout_refused(repo):
    write_rows(repo, detached_row(repo, repo.parent / "gone"))
    cp = run(repo, "use", "--slug", "d")
    assert cp.returncode == 2 and "Traceback" not in cp.stderr, cp.stdout + cp.stderr
    assert reg(repo)["sessions"][0]["slug"] == "d"


def test_reopen_archived_parallel_leaves_hub_session_alone(repo):
    ok(run(repo, "create", "--slug", "h"))
    ok(run(repo, "create", "--slug", "o", "--worktree"))
    ok(run(repo, "close", "--slug", "o"))
    (repo / "app.py").write_text("hub work\n")
    cp = run(repo, "use", "--slug", "o")
    assert any(ln.startswith("REDIRECT worktree=") for ln in cp.stdout.splitlines()), cp.stdout + cp.stderr
    r = reg(repo)
    assert r["active"] == "h" and {s["slug"] for s in r["sessions"]} == {"h", "o"}
    assert next(s for s in r["sessions"] if s["slug"] == "h")["status"] == "active"
    assert (repo / "app.py").read_text() == "hub work\n" and git(repo, "stash", "list") == ""
    assert git(repo, "branch", "--show-current").strip() == "superlite/sessions/h"


def test_discard_removes_owned_checkout_of_existing_branch(repo):
    ok(run(repo, "create", "--slug", "o", "--worktree"))
    r = reg(repo)
    wt = Path(r["sessions"][0]["worktree"])
    r["sessions"][0]["adopted_branch"] = True  # what `ensure --branch <existing>` records for a cut worktree
    (repo / ".superlite" / "sessions.json").write_text(json.dumps(r), encoding="utf-8")
    ok(run(repo, "discard", "--slug", "o"))
    assert not wt.exists() and "superlite/sessions/o" in git(repo, "branch", "--list")
    assert str(wt.name) not in git(repo, "worktree", "list")


def test_discard_registered_drops_leftover_wip_ref(repo):
    ok(run(repo, "create", "--slug", "s"))
    git(repo, "update-ref", "refs/superlite/wip/s", git(repo, "rev-parse", "HEAD").strip())
    ok(run(repo, "discard", "--slug", "s"))
    assert git(repo, "for-each-ref", "refs/superlite/wip/s").strip() == ""


def test_close_parked_host_session_archives_and_unregisters(repo):
    ok(run(repo, "create", "--slug", "a"))
    (repo / "app.py").write_text("a work\n")
    ok(run(repo, "create", "--slug", "b"))
    before = git(repo, "branch", "--show-current")
    ok(run(repo, "close", "--slug", "a"))
    r = reg(repo)
    assert [s["slug"] for s in r["sessions"]] == ["b"] and r["active"] == "b"
    assert (repo / ".superlite" / "session-store" / "a" / "session.json").is_file()
    assert git(repo, "for-each-ref", "refs/superlite/wip/a").strip() and git(repo, "stash", "list") == ""
    assert git(repo, "branch", "--show-current") == before
    ok(run(repo, "use", "--slug", "a"))
    assert (repo / "app.py").read_text() == "a work\n"


def test_close_owned_session_with_worktree_gone_archives(repo):
    ok(run(repo, "create", "--slug", "o", "--worktree"))
    wt = Path(reg(repo)["sessions"][0]["worktree"])
    git(repo, "worktree", "remove", "--force", str(wt))
    ok(run(repo, "close", "--slug", "o"))
    assert reg(repo)["sessions"] == []
    assert (repo / ".superlite" / "session-store" / "o" / "session.json").is_file()
