"""WIP on private refs: never the shared stash list."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import session_wip as W  # noqa: E402


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "app"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "t")
    (r / "app.py").write_text("one\n")
    (r / "bin.dat").write_bytes(b"\x00\x01\x02")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    return r


def dirty(r, name="app.py"):
    (r / name).write_text("changed\n")
    (r / "new.txt").write_text("untracked\n")


def test_ref_for():
    assert W.ref_for("dogfood-wip") == "refs/superlite/wip/dogfood-wip"


def test_save_tracked_and_untracked(repo):
    dirty(repo)
    oid = W.save(repo, "a")
    assert len(oid) == 40 and all(c in "0123456789abcdef" for c in oid)
    assert git(repo, "status", "--porcelain") == ""
    assert git(repo, "stash", "list") == ""
    assert git(repo, "rev-parse", "refs/superlite/wip/a").strip() == oid
    assert W.exists(repo, "a")


def test_save_clean_is_none(repo):
    assert W.save(repo, "a") is None
    assert not W.exists(repo, "a")


def test_restore_round_trip(repo):
    dirty(repo)
    (repo / "bin.dat").write_bytes(b"\x00\xff\x10")
    W.save(repo, "a")
    assert (repo / "app.py").read_text() == "one\n" and not (repo / "new.txt").exists()
    assert W.restore(repo, "a") is True
    assert (repo / "app.py").read_text() == "changed\n"
    assert (repo / "new.txt").read_text() == "untracked\n"
    assert (repo / "bin.dat").read_bytes() == b"\x00\xff\x10"
    assert not W.exists(repo, "a")


def test_restore_refuses_dirty(repo):
    dirty(repo)
    W.save(repo, "a")
    (repo / "app.py").write_text("other\n")
    assert W.restore(repo, "a") is False
    assert W.exists(repo, "a")
    assert (repo / "app.py").read_text() == "other\n" and not (repo / "new.txt").exists()


def test_restore_missing_is_noop(repo):
    assert W.restore(repo, "missing") is True
    assert git(repo, "status", "--porcelain") == ""


def test_stash_list_untouched(repo):
    (repo / "app.py").write_text("keep\n")
    git(repo, "stash", "push", "-m", "someone-else")
    before = git(repo, "stash", "list")
    dirty(repo, "bin.dat")
    W.save(repo, "a")
    assert git(repo, "stash", "list") == before
    W.restore(repo, "a")
    assert git(repo, "stash", "list") == before


def test_stash_clear_survives(repo):
    dirty(repo)
    W.save(repo, "a")
    git(repo, "stash", "clear")
    assert W.restore(repo, "a") is True
    assert (repo / "app.py").read_text() == "changed\n" and (repo / "new.txt").exists()


def test_two_slugs_never_cross(repo, tmp_path):
    wt_b = tmp_path / "wtb"
    git(repo, "worktree", "add", "-q", "-b", "other", str(wt_b))
    (repo / "only-a.txt").write_text("a\n")
    (wt_b / "only-b.txt").write_text("b\n")
    W.save(repo, "a")
    W.save(wt_b, "b")
    assert W.exists(repo, "a") and W.exists(repo, "b")
    assert W.restore(repo, "a") is True
    assert (repo / "only-a.txt").exists() and not (repo / "only-b.txt").exists()
    assert W.exists(repo, "b")
    assert W.restore(wt_b, "b") is True
    assert (wt_b / "only-b.txt").exists() and not (wt_b / "only-a.txt").exists()


def test_untracked_false_leaves_untracked(repo):
    dirty(repo)
    W.save(repo, "a", untracked=False)
    assert (repo / "new.txt").exists() and (repo / "app.py").read_text() == "one\n"
    assert git(repo, "status", "--porcelain").strip() == "?? new.txt"
    assert W.restore(repo, "a") is True
    assert (repo / "app.py").read_text() == "changed\n"


def test_untracked_false_untracked_only_is_none(repo):
    (repo / "new.txt").write_text("u\n")
    assert W.save(repo, "a", untracked=False) is None


def test_restore_deleted_and_staged(repo):
    (repo / "app.py").unlink()
    (repo / "staged.txt").write_text("s\n")
    git(repo, "add", "staged.txt")
    (repo / "bin.dat").write_bytes(b"\x00\x09")
    W.save(repo, "a")
    assert (repo / "app.py").exists() and not (repo / "staged.txt").exists()
    assert W.restore(repo, "a") is True
    assert not (repo / "app.py").exists()
    assert (repo / "staged.txt").read_text() == "s\n"
    assert (repo / "bin.dat").read_bytes() == b"\x00\x09"


def test_restore_conflict_keeps_ref_and_tree(repo):
    dirty(repo)
    W.save(repo, "a")
    (repo / "new.txt").write_text("other\n")  # untracked file in the way
    assert W.restore(repo, "a") is False
    assert W.exists(repo, "a") and (repo / "new.txt").read_text() == "other\n"
    assert (repo / "app.py").read_text() == "one\n"


def test_restore_with_noprefix_config(repo):
    git(repo, "config", "diff.noprefix", "true")
    dirty(repo)
    W.save(repo, "a")
    assert W.restore(repo, "a") is True
    assert (repo / "app.py").read_text() == "changed\n"


def test_staged_and_unstaged_round_trip(repo):
    (repo / "app.py").write_text("staged\n")
    git(repo, "add", "app.py")
    (repo / "app.py").write_text("one\n")  # disk back to HEAD, index holds the staged version
    (repo / "bin.dat").write_bytes(b"\x07")
    git(repo, "add", "bin.dat")
    (repo / "bin.dat").write_bytes(b"\x08")  # staged and unstaged differ
    W.save(repo, "a")
    assert git(repo, "status", "--porcelain") == ""
    assert W.restore(repo, "a") is True
    assert git(repo, "show", ":app.py") == "staged\n"
    assert (repo / "app.py").read_text() == "one\n"
    staged = subprocess.run(["git", "show", ":bin.dat"], cwd=repo, capture_output=True, check=True).stdout
    assert staged == b"\x07" and (repo / "bin.dat").read_bytes() == b"\x08"
    assert not W.exists(repo, "a")


def test_deleted_tracked_round_trip(repo):
    (repo / "app.py").unlink()
    (repo / "bin.dat").unlink()
    git(repo, "rm", "-q", "--cached", "bin.dat")  # one staged deletion, one unstaged
    W.save(repo, "a")
    assert (repo / "app.py").exists() and (repo / "bin.dat").exists()
    assert W.restore(repo, "a") is True
    assert not (repo / "app.py").exists() and not (repo / "bin.dat").exists()
    assert sorted(git(repo, "status", "--porcelain").splitlines()) == [" D app.py", "D  bin.dat"]


def test_save_refuses_over_pending_wip(repo):
    dirty(repo)
    first = W.save(repo, "a")
    (repo / "app.py").write_text("second edit\n")
    with pytest.raises(RuntimeError, match="refs/superlite/wip/a"):
        W.save(repo, "a")
    assert git(repo, "rev-parse", "refs/superlite/wip/a").strip() == first
    assert (repo / "app.py").read_text() == "second edit\n"


def _legacy_stash(repo):
    (repo / "app.py").write_text("changed\n")
    (repo / "dir").mkdir()
    (repo / "dir" / "new.txt").write_text("untracked\n")
    git(repo, "stash", "push", "-q", "-u", "-m", "legacy")
    oid = git(repo, "rev-parse", "refs/stash").strip()
    git(repo, "stash", "drop", "-q")
    git(repo, "update-ref", W.ref_for("a"), oid)


def test_restore_legacy_stash_untracked(repo):
    _legacy_stash(repo)
    assert W.restore(repo, "a") is True
    assert (repo / "app.py").read_text() == "changed\n"
    assert (repo / "dir" / "new.txt").read_text() == "untracked\n"
    assert not W.exists(repo, "a")


def test_restore_legacy_stash_refuses_untracked_collision(repo):
    _legacy_stash(repo)
    (repo / "dir").mkdir()
    (repo / "dir" / "new.txt").write_text("mine\n")
    assert W.restore(repo, "a") is False
    assert W.exists(repo, "a")
    assert (repo / "dir" / "new.txt").read_text() == "mine\n"
    assert (repo / "app.py").read_text() == "one\n"
