"""session_wip.save must leave no ref and an intact tree when the reset or clean after the snapshot fails."""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"))

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
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    (r / "app.py").write_text("two\n")
    (r / "new.txt").write_text("untracked\n")
    return r


@pytest.mark.parametrize("fail", ["reset", "clean"])
def test_failed_save_keeps_tree_and_leaves_no_ref(repo, monkeypatch, fail):
    real = W._git

    def faulty(cwd, *args, **kw):
        if args[:1] == (fail,) and (fail == "clean" or "--hard" in args):  # the recovery `reset -q` must still run
            raise RuntimeError(f"git {fail}: injected")
        return real(cwd, *args, **kw)

    monkeypatch.setattr(W, "_git", faulty)
    with pytest.raises(RuntimeError, match="injected"):
        W.save(repo, "a")
    monkeypatch.setattr(W, "_git", real)
    assert not W.exists(repo, "a")
    assert (repo / "app.py").read_text() == "two\n"
    assert (repo / "new.txt").read_text() == "untracked\n"
    assert git(repo, "diff", "--cached", "--name-only") == ""


@pytest.mark.parametrize("untracked", [True, False])
def test_failure_after_reset_restores_index_deletions_and_keeps_nothing_half_done(repo, monkeypatch, untracked):
    (repo / "gone.txt").write_text("g\n")
    (repo / "kept.txt").write_text("k\n")
    git(repo, "add", "gone.txt", "kept.txt")
    git(repo, "commit", "-q", "-m", "more")
    (repo / "app.py").write_text("staged\n")
    git(repo, "add", "app.py")
    (repo / "app.py").write_text("disk\n")  # staged and unstaged differ
    (repo / "gone.txt").unlink()  # unstaged deletion
    git(repo, "rm", "-q", "kept.txt")  # staged deletion
    before = git(repo, "status", "--porcelain", "--untracked-files=all")
    real = W._git

    def faulty(cwd, *args, **kw):
        if args[:1] == ("clean",) or (not untracked and args[:1] == ("reset",) and "--hard" in args):
            real(cwd, "reset", "--hard", "-q")  # the reset really happened, then the step fails
            raise RuntimeError("git step: injected")
        return real(cwd, *args, **kw)

    monkeypatch.setattr(W, "_git", faulty)
    with pytest.raises(RuntimeError, match="injected"):
        W.save(repo, "a", untracked=untracked)
    monkeypatch.setattr(W, "_git", real)
    assert not W.exists(repo, "a")
    assert git(repo, "status", "--porcelain", "--untracked-files=all") == before
    assert git(repo, "show", ":app.py") == "staged\n"
    assert (repo / "app.py").read_text() == "disk\n"
    assert not (repo / "gone.txt").exists() and not (repo / "kept.txt").exists()
    assert (repo / "new.txt").read_text() == "untracked\n"


def test_rollback_failure_keeps_ref(repo, monkeypatch):
    real = W._git

    def faulty(cwd, *args, **kw):
        if args[:1] == ("clean",) or args[:1] == ("checkout",):
            raise RuntimeError(f"git {args[0]}: injected")
        return real(cwd, *args, **kw)

    monkeypatch.setattr(W, "_git", faulty)
    with pytest.raises(RuntimeError, match="kept on refs/superlite/wip/a"):
        W.save(repo, "a")
    monkeypatch.setattr(W, "_git", real)
    assert W.exists(repo, "a")
    git(repo, "clean", "-fdq")  # the failed clean left the untracked file; the ref still has it
    assert W.restore(repo, "a") is True
    assert (repo / "app.py").read_text() == "two\n" and (repo / "new.txt").read_text() == "untracked\n"
