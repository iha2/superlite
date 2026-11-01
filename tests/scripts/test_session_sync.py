"""superlite-cli session sync: fast-forward only, --later, no fetch (feature checks 6-10)."""
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
import session_registry  # noqa: E402
import session_sync  # noqa: E402


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def commit(cwd, name, text, msg):
    (Path(cwd) / name).write_text(text)
    git(cwd, "add", "-A")
    git(cwd, "commit", "-q", "-m", msg)


def commit_main(r, name, text, msg):
    """Commit on main while the main checkout hosts session b: through a short-lived worktree."""
    tmp = r.parent / "mainwt"
    git(r, "worktree", "add", "-q", str(tmp), "main")
    try:
        commit(tmp, name, text, msg)
    finally:
        git(r, "worktree", "remove", "--force", str(tmp))


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
    return r


def row(r, slug="b"):
    reg = json.loads((r / ".superlite" / "sessions.json").read_text(encoding="utf-8"))
    return next(s for s in reg["sessions"] if s["slug"] == slug)


def sync(r, *a):
    return session_sync.main(["--slug", "b", "--root", str(r), *a])


def test_fast_forward_records_base_sha(repo, capsys):
    commit_main(repo, "m1.txt", "1", "m1")
    commit_main(repo, "m2.txt", "2", "m2")
    assert sync(repo) == 0
    assert "session: b synced with main (fast-forward)" in capsys.readouterr().out
    assert row(repo)["base_sha"] == git(repo, "rev-parse", "main").strip()
    assert (host(repo) / "m2.txt").exists()


def test_later_records_offer_only(repo, capsys):
    commit_main(repo, "m1.txt", "1", "m1")
    head = git(host(repo), "rev-parse", "HEAD")
    assert sync(repo, "--later") == 0
    assert row(repo)["sync_offered"] == git(repo, "rev-parse", "main").strip()
    assert git(host(repo), "rev-parse", "HEAD") == head


def test_untracked_gate_asks(repo, capsys):
    commit_main(repo, "m1.txt", "1", "m1")
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
    orig = session_registry.registry_lock

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
    orig_u = session_registry.update
    monkeypatch.setattr(session_registry, "update", lambda r, fn: (seen.append(bool(held)), orig_u(r, fn))[1])
    assert sync(repo, "--later") == 0
    assert seen == [True]


def test_stash_reapply_conflict_keeps_stash_and_row(repo, capsys):
    commit_main(repo, "other.py", "y\n", "main other")
    (host(repo) / "other.py").write_text("wip\n")
    assert sync(repo) == 2
    assert "kept in git stash" in capsys.readouterr().err
    assert git(host(repo), "stash", "list") != ""
    r = row(repo)
    assert r["sync_stash"] and "sync" not in r


def test_end_to_end_via_session_py(repo):
    commit_main(repo, "m1.txt", "1", "m1")
    cp = subprocess.run([sys.executable, str(SCRIPTS / "session.py"), "sync", "--slug", "b", "--root", str(repo)],
                        capture_output=True, text=True)
    assert cp.returncode == 0, cp.stderr
    assert (host(repo) / "m1.txt").exists()
    assert row(repo)["base_sha"] == git(repo, "rev-parse", "main").strip()


def cli(script, *args, cwd):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env.pop("SUPERLITE_NO_AUTO_SESSION", None)
    return subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, args)], capture_output=True,
                          text=True, cwd=cwd, env=env, timeout=120)


@pytest.fixture
def parked_p(tmp_path):
    """Main checkout hosts session a; session p was created in a worktree and closed, so its row has no worktree."""
    r = tmp_path.resolve() / "plain"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "t")
    (r / ".gitignore").write_text(".superlite/\n")
    commit(r, "app.py", "one\ntwo\nthree\n", "init")
    assert cli("session.py", "ensure", "--slug", "a", cwd=r).returncode == 0
    assert cli("session.py", "create", "--slug", "p", "--worktree", cwd=r).returncode == 0
    cp = cli("session.py", "close", "--slug", "p", "--keep-branch", cwd=r)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert row(r, "p").get("worktree") in (None, "")
    return r


@pytest.fixture
def two_bases(tmp_path):
    """Session b is cut from main; session c later starts on feature, which moves the registry base."""
    r = tmp_path.resolve() / "bases"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "t")
    (r / ".gitignore").write_text(".superlite/\n")
    commit(r, "app.py", "one\n", "init")
    assert cli("session.py", "ensure", "--slug", "a", cwd=r).returncode == 0
    assert cli("session.py", "ensure", "--slug", "b", "--move", "yes", cwd=r).returncode == 0
    git(r, "switch", "-q", "-c", "feature")
    commit(r, "feat.txt", "feature\n", "feature work")
    assert cli("session.py", "ensure", "--slug", "c", cwd=r).returncode == 0
    reg = json.loads((r / ".superlite" / "sessions.json").read_text(encoding="utf-8"))
    assert reg["base"] == "feature"
    return r


def test_sync_merges_the_rows_own_base(two_bases):
    """sync for b brings in main (its own base), never the feature branch the registry now names."""
    r = two_bases
    wt = r / ".superlite" / "worktrees" / "sessions" / "b"
    tmpw = r.parent / "mainwt"
    git(r, "worktree", "add", "-q", str(tmpw), "main")
    commit(tmpw, "m.txt", "m\n", "main work")
    git(r, "worktree", "remove", "--force", str(tmpw))
    cp = cli("session_sync.py", "--slug", "b", cwd=wt)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert (wt / "m.txt").is_file()
    assert not (wt / "feat.txt").exists(), "b was synced with the registry base, not its own"


def test_sync_refuses_merge_commit(repo, capsys):
    commit(host(repo), "mine.txt", "m", "mine")
    commit_main(repo, "other.py", "y\n", "main other")
    before = git(host(repo), "rev-parse", "HEAD")
    assert sync(repo) == 2
    assert "session: needs a merge commit; not synced" in capsys.readouterr().err
    assert git(host(repo), "rev-parse", "HEAD") == before
