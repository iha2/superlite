"""session close archives and unregisters; use / ensure restore it; per-row base (T5).

T2 (session_wip) and T3 (session_archive) are swapped for small fakes unless SUPERLITE_REAL=1.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import session_archive  # noqa: E402
import session_ensure as E  # noqa: E402
import session_registry as R  # noqa: E402
import session_switch as S  # noqa: E402
import session_wip  # noqa: E402

_REAL_WIP = (session_wip.ref_for, session_wip.save, session_wip.restore)


def git(cwd, *args, check=True):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=check).stdout


def _ref(slug):
    return f"refs/superlite/wip/{slug}"


def _fake_save(cwd, slug, untracked=True):
    if not git(cwd, "status", "--porcelain", *([] if untracked else ["--untracked-files=no"])).strip():
        return None
    git(cwd, "stash", "push", "-q", *(["-u"] if untracked else []), "-m", f"wip-{slug}")
    oid = git(cwd, "rev-parse", "refs/stash").strip()
    git(cwd, "update-ref", _ref(slug), oid)
    git(cwd, "stash", "drop", "-q")
    return oid


def _fake_restore(cwd, slug):
    oid = git(cwd, "rev-parse", "-q", "--verify", _ref(slug), check=False).strip()
    if not oid:
        return True
    if git(cwd, "status", "--porcelain").strip():
        return False
    git(cwd, "stash", "apply", "-q", oid)
    git(cwd, "update-ref", "-d", _ref(slug))
    return True


def _fake_path(root, slug):
    return Path(root) / ".superlite" / "session-store" / slug / "session.json"


def _fake_write(root, row, wip_ref, now_ms):
    p = _fake_path(root, row["slug"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({**row, "closed_at": now_ms, "wip_ref": wip_ref}), encoding="utf-8")
    return p


def _fake_read(root, slug):
    try:
        return json.loads(_fake_path(root, slug).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@pytest.fixture(autouse=True)
def seam(monkeypatch):
    monkeypatch.delenv("SUPERLITE_NO_AUTO_SESSION", raising=False)
    if os.environ.get("SUPERLITE_REAL") == "1":
        return
    monkeypatch.setattr(session_wip, "ref_for", _ref, raising=False)
    monkeypatch.setattr(session_wip, "save", _fake_save, raising=False)
    monkeypatch.setattr(session_wip, "restore", _fake_restore, raising=False)
    monkeypatch.setattr(session_archive, "path_for", _fake_path, raising=False)
    monkeypatch.setattr(session_archive, "write", _fake_write, raising=False)
    monkeypatch.setattr(session_archive, "read", _fake_read, raising=False)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path.resolve() / "app"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "t")
    (r / ".gitignore").write_text(".superlite/\n")
    (r / "app.py").write_text("print('hi')\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    return r


def reg_of(r):
    return json.loads((r / ".superlite" / "sessions.json").read_text(encoding="utf-8"))


def row_of(r, slug):
    return next((s for s in reg_of(r)["sessions"] if s["slug"] == slug), None)


def ensure(r, slug, **kw):
    return E.cmd_ensure(Namespace(slug=slug, root=str(r), branch=None, move=None, untracked=None, **kw))


def create(r, slug, base=None, worktree=False):
    return E.cmd_create(Namespace(slug=slug, root=str(r), base=base, worktree=worktree, untracked=None))


def close(r, slug, keep_branch=False):
    return E.cmd_close(Namespace(slug=slug, root=str(r), keep_branch=keep_branch, untracked=None))


def use(r, slug):
    return S.cmd_use(Namespace(slug=slug, root=str(r), continue_apply=False, untracked=None))


def test_close_hub_archives_and_unregisters(repo, capsys):
    assert ensure(repo, "a") == 0
    (repo / "app.py").write_text("print('wip')\n")
    stashes = git(repo, "stash", "list")
    capsys.readouterr()
    assert close(repo, "a") == 0
    out = capsys.readouterr().out
    assert "closed a; archived to" in out and "wip refs/superlite/wip/a" in out
    assert row_of(repo, "a") is None and reg_of(repo)["active"] is None
    assert git(repo, "branch", "--show-current").strip() == "main"
    assert (repo / "app.py").read_text() == "print('hi')\n"
    assert git(repo, "stash", "list") == stashes
    assert git(repo, "rev-parse", "--verify", _ref("a")).strip()
    m = session_archive.read(repo, "a")
    assert m["slug"] == "a" and m["wip_ref"] == _ref("a")


def test_close_clean_no_wip_ref(repo, capsys):
    ensure(repo, "a")
    capsys.readouterr()
    assert close(repo, "a") == 0
    assert "(wip" not in capsys.readouterr().out
    assert git(repo, "rev-parse", "-q", "--verify", _ref("a"), check=False).strip() == ""
    assert session_archive.read(repo, "a")["wip_ref"] is None
    assert row_of(repo, "a") is None


def test_use_restores_archived(repo, capsys):
    ensure(repo, "a")
    (repo / ".superlite" / "progress.md").write_text("- a\n")
    (repo / "app.py").write_text("print('wip')\n")
    close(repo, "a")
    capsys.readouterr()
    assert use(repo, "a") == 0
    out = capsys.readouterr().out
    assert f"SESSION worktree={repo.as_posix()} slug=a branch=superlite/sessions/a" in out
    assert git(repo, "branch", "--show-current").strip() == "superlite/sessions/a"
    assert (repo / "app.py").read_text() == "print('wip')\n"
    assert (repo / ".superlite" / "progress.md").read_text() == "- a\n"
    assert not (repo / ".superlite" / "progress.md").is_dir()
    assert not (repo / ".superlite" / "session.json").exists()
    assert git(repo, "rev-parse", "-q", "--verify", _ref("a"), check=False).strip() == ""
    row = row_of(repo, "a")
    assert row["status"] == "active" and reg_of(repo)["active"] == "a"


def test_ensure_restores_archived(repo, capsys):
    ensure(repo, "a")
    (repo / "app.py").write_text("print('wip')\n")
    close(repo, "a")
    capsys.readouterr()
    assert ensure(repo, "a") == 0
    out = capsys.readouterr().out
    assert out.count("SESSION worktree=") == 1 and "slug=a branch=superlite/sessions/a" in out
    assert (repo / "app.py").read_text() == "print('wip')\n"
    assert row_of(repo, "a")["status"] == "active"
    assert row_of(repo, "a-2") is None


def test_close_journal_undo(repo, monkeypatch, capsys):
    ensure(repo, "a")
    (repo / "app.py").write_text("print('wip')\n")
    (repo / ".superlite" / "progress.md").write_text("- a\n")

    def boom(*args, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(session_archive, "write", boom)
    assert close(repo, "a") == 2
    assert "could not close a" in capsys.readouterr().err
    assert (repo / "app.py").read_text() == "print('wip')\n"  # WIP is back
    assert git(repo, "rev-parse", "-q", "--verify", _ref("a"), check=False).strip() == ""
    assert git(repo, "branch", "--show-current").strip() == "superlite/sessions/a"
    assert (repo / ".superlite" / "progress.md").read_text() == "- a\n"
    assert row_of(repo, "a") is not None and reg_of(repo)["active"] == "a"


def test_old_stash_rows_still_restore(repo):
    ensure(repo, "a")
    (repo / "app.py").write_text("print('old')\n")
    oid = R.stash_push(repo, R.stash_msg("a"))
    reg = reg_of(repo)
    reg["sessions"][0].update(status="parked", has_stash=True, stash_oid=oid, stash_message=R.stash_msg("a"))
    reg["active"] = None
    R.write(repo, reg)
    git(repo, "switch", "-q", "main")
    assert use(repo, "a") == 0
    assert (repo / "app.py").read_text() == "print('old')\n"
    assert git(repo, "stash", "list").strip() == ""
    row = row_of(repo, "a")
    assert row["has_stash"] is False and row["status"] == "active"


def test_parked_row_kept(repo):
    ensure(repo, "a")
    (repo / "app.py").write_text("print('a')\n")
    assert create(repo, "b") == 0
    a = row_of(repo, "a")
    assert a is not None and a["status"] == "parked" and a["has_stash"] is True
    assert session_archive.read(repo, "a") is None
    assert use(repo, "a") == 0
    assert (repo / "app.py").read_text() == "print('a')\n"


def test_close_owned_worktree_archives(repo, capsys):
    assert create(repo, "w", worktree=True) == 0
    wt = repo / ".claude" / "worktrees" / "sessions-w"
    (wt / "f.txt").write_text("x\n")
    git(wt, "add", "f.txt")
    git(wt, "commit", "-q", "-m", "work")  # unmerged commit: the branch must survive
    (wt / "app.py").write_text("print('wip')\n")
    capsys.readouterr()
    assert close(repo, "w") == 0
    assert "archived to" in capsys.readouterr().out
    assert not wt.exists()
    assert row_of(repo, "w") is None
    assert session_archive.read(repo, "w")["slug"] == "w"
    assert git(repo, "branch", "--list", "superlite/sessions/w").strip()
    assert git(repo, "rev-parse", "--verify", _ref("w")).strip()
    # use brings the worktree back and redirects to it
    assert use(repo, "w") == R.EXIT_REDIRECT
    assert "REDIRECT worktree=" in capsys.readouterr().out
    assert (wt / "app.py").read_text() == "print('wip')\n"
    assert row_of(repo, "w")["status"] == "parallel"


def test_global_base_unchanged(repo):
    git(repo, "branch", "feat/x")
    assert ensure(repo, "first") == 0
    assert reg_of(repo)["base"] == "main"
    assert create(repo, "b", base="feat/x") == 0
    reg = reg_of(repo)
    assert reg["base"] == "main"
    assert row_of(repo, "b")["base"] == "feat/x"
    assert row_of(repo, "first")["base"] == "main"


def test_use_archived_failure_keeps_run_files_and_manifest(repo, monkeypatch, capsys):
    ensure(repo, "a")
    (repo / ".superlite" / "progress.md").write_text("- a\n")
    close(repo, "a")
    store = repo / ".superlite" / "session-store" / "a"
    assert (store / "session.json").is_file() and (store / "progress.md").is_file()

    def boom(*args, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(E, "write", boom)
    assert use(repo, "a") != 0
    assert (store / "session.json").is_file()  # the manifest survives
    assert (store / "progress.md").read_text() == "- a\n"  # run files are parked again
    assert row_of(repo, "a") is None


def test_discard_archived_slug(repo, capsys):
    ensure(repo, "a")
    (repo / "app.py").write_text("print('wip')\n")
    close(repo, "a")
    capsys.readouterr()
    assert S.cmd_discard(Namespace(slug="a", root=str(repo), keep_branch=False)) == 0
    assert git(repo, "rev-parse", "-q", "--verify", _ref("a"), check=False).strip() == ""
    assert not (repo / ".superlite" / "session-store" / "a").exists()
    assert git(repo, "branch", "--list", "superlite/sessions/a").strip() == ""


def test_park_close_use_restores_untracked(repo, monkeypatch):
    ensure(repo, "a")
    (repo / "app.py").write_text("print('wip')\n")
    (repo / "notes.txt").write_text("untracked\n")
    oid = R.stash_push(repo, R.stash_msg("a"))
    reg = reg_of(repo)
    reg["sessions"][0].update(status="parked", has_stash=True, stash_oid=oid, stash_message=R.stash_msg("a"))
    reg["active"] = None
    R.write(repo, reg)
    git(repo, "switch", "-q", "main")
    assert close(repo, "a") == 0
    for name, fn in zip(("ref_for", "save", "restore"), _REAL_WIP):
        monkeypatch.setattr(session_wip, name, fn, raising=False)
    assert use(repo, "a") == 0
    assert (repo / "app.py").read_text() == "print('wip')\n"
    assert (repo / "notes.txt").read_text() == "untracked\n"
