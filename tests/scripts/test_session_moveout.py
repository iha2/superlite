"""ensure moves the open host session out of the main checkout (feature checks 1-5), base_sha, sync delegation."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "swarm" / "scripts"
SESSION = SCRIPTS / "session.py"
sys.path.insert(0, str(SCRIPTS))

import session  # noqa: E402

REAL = os.environ.get("SUPERLITE_REAL") == "1"


def run(*args, cwd=None):
    e = dict(os.environ, PYTHONIOENCODING="utf-8")
    e.pop("SUPERLITE_NO_AUTO_SESSION", None)
    cp = subprocess.run([sys.executable, str(SESSION), *map(str, args)], capture_output=True, cwd=cwd, env=e, timeout=120)
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
    (r / ".gitignore").write_text(".superlite/\n")
    (r / "app.py").write_text("print('hi')\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    return r


@pytest.fixture
def started(repo):
    """Session a open in the main checkout with a tracked edit and a run file."""
    assert run("ensure", "--slug", "a", cwd=repo).returncode == 0
    (repo / "app.py").write_text("print('wip')\n")
    (repo / ".superlite" / "orchestrator.json").write_text('{"mode":"ai"}\n')
    (repo / ".superlite" / "checkpoints.json").write_text('{"cp":1}\n')
    return repo


def reg_of(r):
    return json.loads((r / ".superlite" / "sessions.json").read_text(encoding="utf-8"))


def row_of(r, slug):
    return next(s for s in reg_of(r)["sessions"] if s["slug"] == slug)


def branch(cwd):
    return git(cwd, "branch", "--show-current").strip()


def wt_of(r, slug):
    return r / ".superlite" / "worktrees" / "sessions" / slug


def snapshot(r):
    return (reg_of(r), branch(r), git(r, "worktree", "list"), git(r, "status", "--porcelain"), git(r, "branch"))


def test_check1_move_needs_confirmation_and_changes_nothing(started):
    before = snapshot(started)
    cp = run("ensure", "--slug", "b", cwd=started)
    assert cp.returncode == 4, cp.both
    assert f"ASK move slug=a: a moves from the main checkout to {wt_of(started, 'a').as_posix()}; its chat must be idle" in cp.out
    assert snapshot(started) == before and not wt_of(started, "b").exists()


def test_check2_both_sessions_move_out(started):
    cp = run("ensure", "--slug", "b", "--move", "yes", cwd=started)
    assert cp.returncode == 0, cp.both
    a, b = wt_of(started, "a"), wt_of(started, "b")
    assert branch(a) == "superlite/sessions/a" and branch(b) == "superlite/sessions/b"
    assert (a / "app.py").read_text() == "print('wip')\n"
    assert (a / ".superlite" / "orchestrator.json").read_text() == '{"mode":"ai"}\n'
    assert (a / ".superlite" / "checkpoints.json").is_file()
    assert branch(started) == "main" and git(started, "status", "--porcelain").strip() == ""
    assert not (started / ".superlite" / "orchestrator.json").exists()
    lines = cp.out.strip().splitlines()
    assert f"session: moved a -> {a.as_posix()}" in lines
    assert lines[-1] == f"SESSION worktree={b} slug=b branch=superlite/sessions/b"
    reg = reg_of(started)
    assert reg["active"] is None and reg["host_worktree"] == started.as_posix()
    ra, rb = row_of(started, "a"), row_of(started, "b")
    assert ra["status"] == rb["status"] == "parallel" and ra["owned"] is True and rb["owned"] is True
    assert ra["moved_from"] == started.as_posix() and "moved_from" not in rb
    main_sha = git(started, "rev-parse", "main").strip()
    assert ra["base_sha"] == main_sha and rb["base_sha"] == main_sha


def test_check3_untracked_gate_then_stash_or_keep(started):
    (started / "notes.txt").write_text("x\n")
    before = snapshot(started)
    cp = run("ensure", "--slug", "b", "--move", "yes", cwd=started)
    assert cp.returncode == 4 and "ASK untracked=1 slug=a: notes.txt" in cp.out
    assert snapshot(started) == before
    cp = run("ensure", "--slug", "b", "--move", "yes", "--untracked", "stash", cwd=started)
    assert cp.returncode == 0, cp.both
    assert (wt_of(started, "a") / "notes.txt").is_file() and not (started / "notes.txt").exists()


def test_check3_keep_leaves_untracked_in_main_checkout(started):
    (started / "notes.txt").write_text("x\n")
    cp = run("ensure", "--slug", "b", "--move", "yes", "--untracked", "keep", cwd=started)
    assert cp.returncode == 0, cp.both
    assert (started / "notes.txt").is_file() and not (wt_of(started, "a") / "notes.txt").exists()
    assert (wt_of(started, "a") / "app.py").read_text() == "print('wip')\n"


def test_check4_moved_row_records_where_it_came_from(started):
    run("ensure", "--slug", "b", "--move", "yes", cwd=started)
    row = row_of(started, "a")
    assert row["moved_from"] == started.as_posix() and row["worktree"] == wt_of(started, "a").as_posix()
    assert isinstance(row["moved_at"], int) and row["moved_at"] > 10**12


def test_check5_finished_host_session_still_reuses_main_checkout(started):
    assert session.mark_run(started, "finished") == "a"
    cp = run("ensure", "--slug", "c", cwd=started)
    assert cp.returncode == 0 and "ASK" not in cp.out, cp.both
    assert branch(started) == "superlite/sessions/c" and not wt_of(started, "c").exists()
    assert row_of(started, "a")["status"] == "parked"


def test_rollback_on_failure_leaves_everything_unchanged(started, monkeypatch, capsys):
    (started / "notes.txt").write_text("x\n")
    before = snapshot(started)
    real_git = session.git

    def failing(*args, **kw):
        if args[:2] == ("worktree", "add") and "-b" in args:
            raise session.GitError("boom")
        return real_git(*args, **kw)
    monkeypatch.setattr(session, "git", failing)
    rc = session.main(["ensure", "--slug", "b", "--move", "yes", "--untracked", "stash", "--root", str(started)])
    assert rc == 1 and "rolling back" in capsys.readouterr().err
    assert snapshot(started) == before
    assert (started / "app.py").read_text() == "print('wip')\n" and (started / "notes.txt").is_file()
    assert (started / ".superlite" / "orchestrator.json").read_text() == '{"mode":"ai"}\n'
    assert (started / ".superlite" / "checkpoints.json").is_file()
    assert not wt_of(started, "a").exists()


def test_new_rows_get_base_sha(repo):
    assert run("ensure", "--slug", "a", cwd=repo).returncode == 0
    assert row_of(repo, "a")["base_sha"] == git(repo, "rev-parse", "main").strip()


def test_base_sha_of_fills_missing_from_merge_base(repo):
    assert run("ensure", "--slug", "a", cwd=repo).returncode == 0
    reg = reg_of(repo)
    row = row_of(repo, "a")
    want = row.pop("base_sha")
    (repo / "x.txt").write_text("x\n")
    git(repo, "add", "x.txt")
    git(repo, "commit", "-q", "-m", "on a")
    assert session.base_sha_of(repo, reg, row) == want and row["base_sha"] == want
    assert session.base_sha_of(repo, reg, {**row, "base_sha": "abc"}) == "abc"


def test_sync_delegates_remaining_argv(monkeypatch):
    import types
    seen = []
    monkeypatch.setitem(sys.modules, "session_sync", types.SimpleNamespace(main=lambda argv: seen.append(argv) or 5))
    assert session.main(["sync", "--slug", "b", "--later"]) == 5 and seen == [["--slug", "b", "--later"]]


def test_sync_unavailable_when_module_missing(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "session_sync", None)
    assert session.main(["sync", "--later"]) == 2
    assert "session: sync unavailable" in capsys.readouterr().err


def test_staged_and_tracked_wip_survive_the_move(started):
    (started / "new.py").write_text("n\n")
    git(started, "add", "new.py")
    git(started, "add", "app.py")
    (started / "app.py").write_text("print('wip2')\n")
    cp = run("ensure", "--slug", "b", "--move", "yes", cwd=started)
    assert cp.returncode == 0, cp.both
    a = wt_of(started, "a")
    assert (a / "app.py").read_text() == "print('wip2')\n" and (a / "new.py").is_file()
    assert "A  new.py" in git(a, "status", "--porcelain") or "AM" in git(a, "status", "--porcelain")
    assert git(started, "status", "--porcelain").strip() == ""


def test_existing_worktree_path_changes_nothing(started):
    wt_of(started, "a").mkdir(parents=True)
    before = snapshot(started)
    cp = run("ensure", "--slug", "b", "--move", "yes", cwd=started)
    assert cp.returncode == 2 and "already exists" in cp.err
    assert snapshot(started) == before and (started / "app.py").read_text() == "print('wip')\n"
    assert (started / ".superlite" / "orchestrator.json").is_file()


def test_base_checked_out_elsewhere_rolls_back(started, tmp_path):
    git(started, "worktree", "add", "-q", str(tmp_path / "other"), "main")
    (started / "notes.txt").write_text("x\n")
    before = snapshot(started)
    cp = run("ensure", "--slug", "b", "--move", "yes", "--untracked", "stash", cwd=started)
    assert cp.returncode == 1 and "rolling back" in cp.err, cp.both
    assert snapshot(started) == before
    assert (started / "app.py").read_text() == "print('wip')\n" and (started / "notes.txt").is_file()
    assert (started / ".superlite" / "checkpoints.json").is_file()
    assert not wt_of(started, "a").exists() and not wt_of(started, "b").exists()


def test_stash_apply_failure_in_keep_worktree_rolls_back(started, monkeypatch, capsys):
    before = snapshot(started)
    real = session.restore_stash
    calls = []

    def fake(cwd, oid):
        calls.append(cwd)
        return False if len(calls) == 1 else real(cwd, oid)
    monkeypatch.setattr(session, "restore_stash", fake)
    rc = session.main(["ensure", "--slug", "b", "--move", "yes", "--root", str(started)])
    assert rc == 1 and "rolling back" in capsys.readouterr().err
    assert snapshot(started) == before and (started / "app.py").read_text() == "print('wip')\n"
    assert (started / ".superlite" / "orchestrator.json").read_text() == '{"mode":"ai"}\n'
    assert not wt_of(started, "a").exists()
