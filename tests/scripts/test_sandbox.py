"""sandbox: paths, file listing, inside check, open sandbox lookup."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import sandbox  # noqa: E402
import state  # noqa: E402

SB = f"{state.DIR_NAME}/sandboxes/host/x"


@pytest.fixture
def repo(tmp_path):
    r = tmp_path.resolve() / "app"
    r.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=r, check=True, capture_output=True)
    return r


def test_path_for():
    assert sandbox.path_for(Path("/r"), "host", "export") == Path("/r") / state.DIR_NAME / "sandboxes" / "host" / "export"


def test_inside_basic():
    assert sandbox.inside(SB, SB + "/a.py")
    assert sandbox.inside(SB, SB + "/sub/./a.py")
    assert not sandbox.inside(SB, "src/a.py")
    assert not sandbox.inside(SB, SB + "-other/a.py")
    assert not sandbox.inside(SB, SB[:-2])


def test_inside_rejects_dotdot():
    assert not sandbox.inside(SB, SB + "/../../y.py")
    assert not sandbox.inside(SB, SB + "/a/../../x2/y.py")


def test_inside_backslash():
    assert sandbox.inside(SB, SB.replace("/", "\\") + "\\a.py")
    assert not sandbox.inside(SB, SB.replace("/", "\\") + "\\..\\..\\y.py")


def test_files_skips_deps(repo):
    sb = repo / SB
    for rel in ("b.py", "a/c.py", "node_modules/m/i.js", ".venv/lib/x.py", "__pycache__/z.pyc", "a/node_modules/q.js"):
        (sb / rel).parent.mkdir(parents=True, exist_ok=True)
        (sb / rel).write_text("x\n", encoding="utf-8")
    assert sandbox.files(repo, sb) == [f"{SB}/a/c.py", f"{SB}/b.py"]
    assert sandbox.files(repo, repo / "missing") == []


def test_session_of_no_registry(repo):
    assert sandbox.session_of(repo) == "host"
    assert sandbox.session_of(repo / "nope") == "host"


def feature(repo, **kw):
    sl = repo / state.DIR_NAME
    sl.mkdir(exist_ok=True)
    (sl / "feature.json").write_text(json.dumps(kw), encoding="utf-8")
    return sl


@pytest.mark.parametrize("status", ["building", "tryout", "approved"])
def test_open_sandbox_open(repo, status):
    sl = feature(repo, status=status, sandbox=(repo / SB).as_posix())
    assert sandbox.open_sandbox(sl) == SB


def test_open_sandbox_with_relative_run_dir(repo, monkeypatch):
    feature(repo, status="building", sandbox=(repo / SB).as_posix())
    monkeypatch.chdir(repo)
    assert sandbox.open_sandbox(Path(state.DIR_NAME)) == SB


def test_open_sandbox_discarded_none(repo):
    assert sandbox.open_sandbox(feature(repo, status="discarded", sandbox=(repo / SB).as_posix())) is None


def test_open_sandbox_legacy_and_missing(repo):
    assert sandbox.open_sandbox(feature(repo, status="building", worktree="/w")) is None
    assert sandbox.open_sandbox(repo / "nodir") is None
    (repo / state.DIR_NAME / "feature.json").write_text("{bad", encoding="utf-8")
    assert sandbox.open_sandbox(repo / state.DIR_NAME) is None
