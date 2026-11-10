"""Worktree home (D10): superlite cuts worktrees under <hub>/.claude/worktrees; old-home rows keep working."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import ident  # noqa: E402
import worktree_home as wh  # noqa: E402


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def run(script, *args, cwd):
    e = dict(os.environ, PYTHONIOENCODING="utf-8")
    e.pop("SUPERLITE_NO_AUTO_SESSION", None)
    cp = subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, args)], capture_output=True, cwd=cwd,
                        env=e, timeout=120)
    cp.out, cp.err = cp.stdout.decode("utf-8"), cp.stderr.decode("utf-8")
    cp.both = cp.out + cp.err
    return cp


@pytest.fixture
def hub(tmp_path):
    r = tmp_path.resolve() / "app"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "t")
    (r / "a.txt").write_text("a\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    return r


def registry(r):
    return json.loads((r / ".superlite" / "sessions.json").read_text(encoding="utf-8"))


def test_session_dir(hub):
    assert wh.session_dir(hub, "a") == hub / ".claude" / "worktrees" / "sessions-a"


def test_proto_dir(hub):
    assert wh.proto_dir(hub, "x") == hub / ".claude" / "worktrees" / "x-proto"


def test_merge_dir(hub):
    assert wh.merge_dir(hub, "main") == hub / ".claude" / "worktrees" / "merge-main"
    assert wh.merge_dir(hub, "feat/x").name == "merge-feat-x"


def test_home_created_on_first_use(hub):
    assert not (hub / ".claude").exists()
    assert wh.home(hub) == hub / ".claude" / "worktrees" and wh.home(hub).is_dir()


def test_name_collision_suffix(hub):
    git(hub, "worktree", "add", "-q", "-b", "k1", str(wh.session_dir(hub, "a")))
    second = wh.session_dir(hub, "a")
    assert second.name == "sessions-a-2"
    git(hub, "worktree", "add", "-q", "-b", "k2", str(second))
    assert wh.session_dir(hub, "a").name == "sessions-a-3"


def test_hide_once(hub):
    wh.hide(hub)
    wh.hide(hub)
    text = (hub / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert text.splitlines().count(".claude/worktrees/") == 1


def test_hub_status_clean(hub):
    wh.hide_all(hub)
    wt = wh.session_dir(hub, "a")
    git(hub, "worktree", "add", "-q", "-b", "superlite/sessions/a", str(wt))
    (wt / "new.txt").write_text("x\n")
    assert git(hub, "status", "--porcelain").strip() == ""


def test_stash_u_skips_home(hub):
    wh.hide_all(hub)
    wt = wh.session_dir(hub, "a")
    git(hub, "worktree", "add", "-q", "-b", "superlite/sessions/a", str(wt))
    (wt / "new.txt").write_text("x\n")
    (hub / "mine.txt").write_text("m\n")
    git(hub, "stash", "-u")
    assert (wt / "new.txt").read_text() == "x\n" and not (hub / "mine.txt").exists()


def test_superlite_cut_old_and_new(hub):
    assert wh.superlite_cut(hub, hub / ".superlite" / "worktrees" / "sessions" / "a")
    assert wh.superlite_cut(hub, hub / ".superlite" / "sandboxes" / "x-proto")
    home = hub / ".claude" / "worktrees"
    assert wh.superlite_cut(hub, home / "sessions-a")
    assert wh.superlite_cut(hub, home / "x-proto") and wh.superlite_cut(hub, home / "merge-main")
    assert not wh.superlite_cut(hub, home / "agent-1")
    assert not wh.superlite_cut(hub, home / "mine")  # unknown name, no owned row
    assert not wh.superlite_cut(hub, hub)


def test_superlite_cut_owned_row_and_branch(hub):
    home = hub / ".claude" / "worktrees"
    git(hub, "worktree", "add", "-q", "-b", "worktree-sessions-b", str(home / "sessions-b"))
    assert not wh.superlite_cut(hub, home / "sessions-b")  # harness branch wins over a superlite name
    (hub / ".superlite").mkdir()
    (hub / ".superlite" / "sessions.json").write_text(json.dumps(
        {"v": 2, "sessions": [{"slug": "q", "worktree": (home / "custom").as_posix(), "owned": True}]}))
    assert wh.superlite_cut(hub, home / "custom")


def test_harness_worktree_never_owned(hub):
    import session_registry as R
    home = hub / ".claude" / "worktrees"
    git(hub, "worktree", "add", "-q", "-b", "worktree-agent-1", str(home / "agent-1"))
    git(hub, "worktree", "add", "-q", "-b", "worktree-w", str(home / "w"))
    for name, br in (("agent-1", "worktree-agent-1"), ("w", "worktree-w")):
        row = {"slug": name, "worktree": (home / name).as_posix(), "branch": br, "owned": True}
        assert R.owned_worktree(hub, row) is False
    assert R.owned_worktree(hub, {"slug": "a", "worktree": (home / "sessions-a").as_posix(),
                                  "branch": "superlite/sessions/a", "owned": True}) is True


def test_create_worktree_in_new_home(hub):
    cp = run("session.py", "create", "--slug", "a", "--worktree", cwd=hub)
    assert cp.returncode == 0, cp.both
    wt = hub / ".claude" / "worktrees" / "sessions-a"
    assert wt.is_dir() and "superlite/sessions/a" in git(hub, "branch", "--list")
    row = next(s for s in registry(hub)["sessions"] if s["slug"] == "a")
    assert row["owned"] is True and Path(row["worktree"]).as_posix() == wt.as_posix()
    assert git(hub, "status", "--porcelain").strip() == ""


def test_proto_start_default_home(hub):
    (hub / "docs" / "plans").mkdir(parents=True)
    cp = run("proto.py", "start", "--topic", "x", cwd=hub)
    assert cp.returncode == 0, cp.both
    wt = hub / ".claude" / "worktrees" / "x-proto"
    assert wt.is_dir() and "superlite/x-proto" in git(hub, "branch", "--list")
    assert git(hub, "status", "--porcelain").strip() == ""


def test_old_home_rows_still_close(hub):
    old = hub / ".superlite" / "worktrees" / "sessions" / "a"
    old.parent.mkdir(parents=True)
    git(hub, "worktree", "add", "-q", "-b", "superlite/sessions/a", str(old))
    row = {"slug": "a", "branch": "superlite/sessions/a", "worktree": old.as_posix(), "owned": True,
           "status": "parallel", "run": "open", "base": "main"}
    (hub / ".superlite" / "sessions.json").write_text(json.dumps(
        {"v": 2, "base": "main", "active": None, "host_worktree": hub.as_posix(), "sessions": [row]}))
    cp = run("session.py", "close", "--slug", "a", cwd=hub)
    assert cp.returncode == 0, cp.both
    assert not old.exists() and "a" not in [s["slug"] for s in registry(hub)["sessions"]]


def test_git_path_windows_reads_wsl_form(monkeypatch):
    monkeypatch.setattr(ident.os, "name", "nt")
    assert ident.git_path("/mnt/c/Users/x/app") == "C:/Users/x/app"
    assert ident.git_path("/mnt/d") == "D:/"
    assert ident.git_path("C:/Users/x") == "C:/Users/x"
    assert ident.git_path("/home/me/app") == "/home/me/app"
    monkeypatch.setattr(ident.os, "name", "posix")
    assert ident.git_path("/gd/worktrees/n/C:/x") == "C:/x"
