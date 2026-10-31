"""sl session sync: ff, merge, conflict, --continue, --abort, --later, no fetch (feature checks 6-10)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import session  # noqa: E402
import session_sync  # noqa: E402


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def commit(cwd, name, text, msg):
    (Path(cwd) / name).write_text(text)
    git(cwd, "add", "-A")
    git(cwd, "commit", "-q", "-m", msg)


@pytest.fixture(autouse=True)
def fake_or_real(monkeypatch):
    """Fake base_sha_of by default (merge-base); the real one when SUPERLITE_REAL=1 and T1 provides it."""
    if os.environ.get("SUPERLITE_REAL") == "1" and hasattr(session, "base_sha_of"):
        return

    def fake(root, reg, row):
        return git(root, "merge-base", row["branch"], reg["base"]).strip()
    monkeypatch.setattr(session, "base_sha_of", fake, raising=False)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "app"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "t")
    (r / ".gitignore").write_text(".superlite/\n")
    (r / "app.py").write_text("one\ntwo\nthree\n")
    (r / "other.py").write_text("x\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    assert session.main(["create", "--slug", "b", "--root", str(r)]) == 0
    return r


def host(r):
    return r / ".superlite" / "worktrees" / "sessions" / "host"


def row(r, slug="b"):
    reg = json.loads((r / ".superlite" / "sessions.json").read_text(encoding="utf-8"))
    return next(s for s in reg["sessions"] if s["slug"] == slug)


def sync(r, *a):
    return session_sync.main(["--slug", "b", "--root", str(r), *a])


def test_fast_forward_records_base_sha(repo, capsys):
    commit(repo, "m1.txt", "1", "m1")
    commit(repo, "m2.txt", "2", "m2")
    assert sync(repo) == 0
    assert "session: b synced with main (fast-forward)" in capsys.readouterr().out
    assert row(repo)["base_sha"] == git(repo, "rev-parse", "main").strip()
    assert (host(repo) / "m2.txt").exists()


def test_merge_with_wip_restored(repo, capsys):
    commit(host(repo), "mine.txt", "m", "mine")
    commit(repo, "other.py", "y\n", "main other")
    (host(repo) / "app.py").write_text("one\ntwo\nthree\nwip\n")
    assert sync(repo) == 0
    assert "synced with main (merge)" in capsys.readouterr().out
    assert len(git(host(repo), "log", "-1", "--format=%P").split()) == 2
    assert (host(repo) / "app.py").read_text().endswith("wip\n")
    assert git(host(repo), "stash", "list") == ""


def conflict(repo):
    commit(host(repo), "app.py", "one\nB\nthree\n", "b edit")
    commit(repo, "app.py", "one\nM\nthree\n", "main edit")
    (host(repo) / "other.py").write_text("wip\n")
    return sync(repo)


def test_conflict_exit5_leaves_merge_and_row(repo, capsys):
    assert conflict(repo) == 5
    assert "CONFLICT b: app.py" in capsys.readouterr().out
    assert session.in_progress(host(repo))
    assert row(repo)["sync"] == "conflict"


def test_continue_commits_and_restores_stash(repo, capsys):
    assert conflict(repo) == 5
    assert sync(repo, "--continue") == 2  # still unmerged
    assert session.in_progress(host(repo))
    (host(repo) / "app.py").write_text("one\nR\nthree\n")
    git(host(repo), "add", "app.py")
    assert sync(repo, "--continue") == 0
    assert not session.in_progress(host(repo))
    assert (host(repo) / "other.py").read_text() == "wip\n"
    assert git(host(repo), "stash", "list") == ""
    r = row(repo)
    assert "sync" not in r and r["base_sha"] == git(repo, "rev-parse", "main").strip()


def test_abort_restores_branch_and_stash(repo, capsys):
    assert conflict(repo) == 5
    before = git(host(repo), "rev-parse", "HEAD")
    assert sync(repo, "--abort") == 0
    assert not session.in_progress(host(repo))
    assert git(host(repo), "rev-parse", "HEAD") == before
    assert (host(repo) / "other.py").read_text() == "wip\n"
    assert git(host(repo), "stash", "list") == ""
    assert row(repo)["sync"] == "needs-sync"


def test_refuses_when_merge_in_progress(repo, capsys):
    assert conflict(repo) == 5
    assert sync(repo) == 2
    assert "already in progress" in capsys.readouterr().err
    assert row(repo)["sync"] == "conflict"


def test_later_records_offer_only(repo, capsys):
    commit(repo, "m1.txt", "1", "m1")
    head = git(host(repo), "rev-parse", "HEAD")
    assert sync(repo, "--later") == 0
    assert row(repo)["sync_offered"] == git(repo, "rev-parse", "main").strip()
    assert git(host(repo), "rev-parse", "HEAD") == head


def test_untracked_gate_asks(repo, capsys):
    commit(repo, "m1.txt", "1", "m1")
    (host(repo) / "new.txt").write_text("u")
    assert sync(repo) == 4
    assert "ASK untracked=1 slug=b" in capsys.readouterr().out
    assert sync(repo, "--untracked", "keep") == 0
    assert (host(repo) / "new.txt").exists()


def test_no_fetch_from_origin(repo, tmp_path, capsys):
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", str(origin))
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-q", "origin", "main")
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(origin), str(other))
    git(other, "checkout", "-q", "-B", "main", "origin/main")
    git(other, "config", "user.email", "t@example.com")
    git(other, "config", "user.name", "t")
    commit(other, "remote.txt", "r", "remote")
    git(other, "push", "-q", "origin", "main")
    assert sync(repo) == 0
    assert "session: b already up to date with main" in capsys.readouterr().out
    assert not (host(repo) / "remote.txt").exists()
    assert git(repo, "rev-parse", "origin/main").strip() != git(other, "rev-parse", "HEAD").strip()


def test_registry_write_under_lock(repo, monkeypatch):
    held = []
    orig = session.registry_lock

    class Spy:
        def __init__(self, root):
            self.cm = orig(root)

        def __enter__(self):
            held.append(1)
            return self.cm.__enter__()

        def __exit__(self, *e):
            held.pop()
            return self.cm.__exit__(*e)
    monkeypatch.setattr(session, "registry_lock", Spy)
    seen = []
    orig_w = session.write_reg
    monkeypatch.setattr(session, "write_reg", lambda r, g: (seen.append(bool(held)), orig_w(r, g))[1])
    assert sync(repo, "--later") == 0
    assert seen == [True]


def test_continue_abort_without_merge_exit2(repo, capsys):
    assert sync(repo, "--continue") == 2
    assert sync(repo, "--abort") == 2
    assert "no merge in progress" in capsys.readouterr().err


def test_continue_refuses_rebase_in_progress(repo, monkeypatch, capsys):
    monkeypatch.setattr(session_sync, "merging", lambda cwd: False)
    assert sync(repo, "--continue") == 2


def test_stash_reapply_conflict_keeps_stash_and_row(repo, capsys):
    commit(host(repo), "mine.txt", "m", "mine")
    commit(repo, "other.py", "y\n", "main other")
    (host(repo) / "other.py").write_text("wip\n")
    assert sync(repo) == 2
    assert "kept in git stash" in capsys.readouterr().err
    assert git(host(repo), "stash", "list") != ""
    r = row(repo)
    assert r["sync_stash"] and "sync" not in r


def test_abort_stash_conflict_keeps_stash(repo, monkeypatch, capsys):
    assert conflict(repo) == 5
    monkeypatch.setattr(session, "restore_stash", lambda cwd, oid: False)
    assert sync(repo, "--abort") == 2
    assert row(repo)["sync_stash"]
    assert row(repo)["sync"] == "needs-sync"


def test_exception_during_merge_aborts_and_restores(repo, monkeypatch):
    commit(host(repo), "app.py", "one\nB\nthree\n", "b edit")
    commit(repo, "app.py", "one\nM\nthree\n", "main edit")
    (host(repo) / "other.py").write_text("wip\n")
    head = git(host(repo), "rev-parse", "HEAD")

    def boom(cwd):
        raise RuntimeError("boom")
    monkeypatch.setattr(session, "unmerged", boom)
    with pytest.raises(RuntimeError):
        sync(repo)
    assert not session.in_progress(host(repo))
    assert git(host(repo), "rev-parse", "HEAD") == head
    assert (host(repo) / "other.py").read_text() == "wip\n"
    assert git(host(repo), "stash", "list") == ""


def test_lock_released_after_conflict(repo):
    assert conflict(repo) == 5
    assert sync(repo, "--later") == 0  # would block forever if the lock leaked
    assert row(repo)["sync"] == "conflict"


def test_end_to_end_via_session_py(repo):
    commit(repo, "m1.txt", "1", "m1")
    cp = subprocess.run([sys.executable, str(SCRIPTS / "session.py"), "sync", "--slug", "b", "--root", str(repo)],
                        capture_output=True, text=True)
    assert cp.returncode == 0, cp.stderr
    assert (host(repo) / "m1.txt").exists()
    assert row(repo)["base_sha"] == git(repo, "rev-parse", "main").strip()


def test_no_network_git_verbs():
    import re
    src = (SCRIPTS / "session_sync.py").read_text()
    assert not re.search(r'"(fetch|push|pull|rebase)"|--force', src)
