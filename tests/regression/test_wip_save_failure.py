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
