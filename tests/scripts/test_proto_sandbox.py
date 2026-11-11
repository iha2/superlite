"""proto start/tryout/discard on a sandbox folder inside the checkout (no worktree, no branch).

T1 (sandbox), T2 (session_sandboxes) and T4 (proto_discard) are faked with their contract values; set SUPERLITE_REAL=1
to run the same tests against the real modules."""
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import proto  # noqa: E402
import run_archive  # noqa: E402
import sandbox  # noqa: E402
import session_sandboxes  # noqa: E402

REAL = os.environ.get("SUPERLITE_REAL") == "1"


class Result:
    def __init__(self, rc, out, err):
        self.returncode, self.out, self.err = rc, out, err
        self.both = out + err


def inproc(*args, cwd, cc=False):
    """proto.main in this process (so the fakes apply), captured like a subprocess."""
    out, err = io.TextIOWrapper(io.BytesIO(), encoding="utf-8"), io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    old = (sys.stdout, sys.stderr, os.getcwd(), os.environ.get("CLAUDECODE"))
    sys.stdout, sys.stderr = out, err
    os.chdir(cwd)
    os.environ.pop("CLAUDECODE", None)
    if cc:
        os.environ["CLAUDECODE"] = "1"
    try:
        try:
            rc = proto.main([str(a) for a in args])
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 1
            if isinstance(e.code, str):
                err.write(e.code)
    finally:
        sys.stdout, sys.stderr = old[0], old[1]
        os.chdir(old[2])
        os.environ.pop("CLAUDECODE", None)
        if old[3] is not None:
            os.environ["CLAUDECODE"] = old[3]
    texts = []
    for f in (out, err):
        f.flush()
        texts.append(f.buffer.getvalue().decode("utf-8"))
    return Result(rc, *texts)


@pytest.fixture
def rows():
    return []


@pytest.fixture(autouse=True)
def fakes(monkeypatch, rows):
    """Contract values of T1, T2 and T4; the real modules under SUPERLITE_REAL=1."""
    if REAL:
        return
    monkeypatch.setattr(sandbox, "session_of", lambda checkout: "host")
    monkeypatch.setattr(sandbox, "path_for", lambda checkout, session, topic: Path(checkout) / ".superlite" / "sandboxes" / session / topic)

    def record(checkout, topic, **fields):
        rows.append({"topic": topic, **fields})
        return True

    def discard_open(sl, reason="discard"):
        st = proto.read_state(sl)
        if not st or st["status"] == "discarded":
            return None
        rep = proto.proto_report(st)
        if rep:
            st["proto_report"] = rep
        shutil.rmtree(st["sandbox"], ignore_errors=True)
        st.update(status="discarded", discarded=proto.now_ms(), discard_reason=reason)
        proto.write_state(sl, st)
        return st

    monkeypatch.setattr(session_sandboxes, "record", record)
    import proto_discard
    monkeypatch.setattr(proto_discard, "discard_open", discard_open)


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "app"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "t")
    (r / ".gitignore").write_text(".superlite/\n")
    (r / "lib.py").write_text("VALUE = 1\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    return r


def feature(repo):
    return json.loads((repo / ".superlite" / proto.FILE).read_text(encoding="utf-8"))


def test_start_makes_sandbox_no_branch(repo):
    before = (git(repo, "worktree", "list"), git(repo, "branch"))
    cp = inproc("start", "--topic", "export", cwd=repo)
    assert cp.returncode == 0, cp.both
    sb = repo / ".superlite" / "sandboxes" / "host" / "export"
    assert sb.is_dir()
    assert (git(repo, "worktree", "list"), git(repo, "branch")) == before
    st = feature(repo)
    assert st["sandbox"] == sb.as_posix() and st["session"] == "host" and st["run_id"]
    assert not {"worktree", "branch", "base", "adopted"} & set(st)
    assert st["status"] == "building" and st["rounds"] == 1 and st["level"] == "L1"
    assert cp.out.splitlines()[0] == "proto: export building, sandbox .superlite/sandboxes/host/export"
    assert ".superlite/sandboxes/host/export/docs/plans/<date>-export.proto.md" in cp.out
    assert git(repo, "status", "--porcelain") == ""


def test_start_ignores_sandbox_without_session_or_gitignore(repo):
    (repo / ".gitignore").unlink()
    cp = inproc("start", "--topic", "export", cwd=repo)
    sb = repo / ".superlite" / "sandboxes" / "host" / "export"
    assert cp.returncode == 0, cp.both
    exclude = (repo / ".git" / "info" / "exclude").read_text()
    assert ".superlite/" in exclude
    (sb / "stub.py").write_text("value = 1\n")
    assert git(repo, "check-ignore", "--no-index", ".superlite/sandboxes/host/export/stub.py").strip()
    assert ".superlite/sandboxes/host/export/stub.py" not in git(repo, "status", "--porcelain", "--untracked-files=all")


def test_start_under_claude_code_no_enter_line(repo):
    cp = inproc("start", "--topic", "export", cwd=repo, cc=True)
    assert cp.returncode == 0, cp.both
    assert "EnterWorktree" not in cp.both and "proto: create" not in cp.both
    assert not (repo / ".superlite" / "proto-pending.json").exists()


def test_fix_round_keeps_run_id(repo):
    inproc("start", "--topic", "export", cwd=repo)
    first = feature(repo)["run_id"]
    assert first == run_archive.read_current_run(repo / ".superlite")["id"]
    cp = inproc("start", "--topic", "export", "--run", "python entry.py", cwd=repo)
    assert cp.returncode == 0 and "(fix round 2)" in cp.out
    st = feature(repo)
    assert st["rounds"] == 2 and st["run_id"] == first and st["status"] == "building" and st["run"] == "python entry.py"
    assert inproc("start", "--topic", "other", cwd=repo).returncode == 2  # one open prototype at a time


def test_tryout_run_line_cds_into_sandbox(repo):
    inproc("start", "--topic", "export", "--run", "python entry.py", cwd=repo)
    cp = inproc("tryout", cwd=repo)
    sb = (repo / ".superlite" / "sandboxes" / "host" / "export").as_posix()
    assert cp.returncode == 0, cp.both
    assert f'run: cd "{sb}" && python entry.py' in cp.out


def test_status_prints_session_and_run(repo):
    inproc("start", "--topic", "export", cwd=repo)
    st = feature(repo)
    cp = inproc("status", cwd=repo)
    assert cp.out.startswith("proto: export building, sandbox .superlite/sandboxes/host/export")
    assert f"session host, run {st['run_id']}" in cp.out


def test_report_found_by_run_id(repo, tmp_path):
    runs = repo / ".superlite" / "runs"
    for name, headline in (("20261111-100000-host", "mine"), ("20261111-110000-host", "other")):
        d = runs / name
        d.mkdir(parents=True)
        (d / "run.json").write_text(json.dumps({"id": name, "checkout": repo.as_posix(), "archived_at": "2026-11-11T12:00:00" if name.startswith("20261111-1000") else "2026-11-11T13:00:00"}))
        (d / "report.json").write_text(json.dumps({"headline": headline, "tasks": [1]}))
    sb = repo / ".superlite" / "sandboxes" / "host" / "export"
    st = {"sandbox": sb.as_posix(), "run_id": "20261111-100000-host", "topic": "export"}
    rep = proto.proto_report(st)
    assert rep["headline"] == "mine" and rep["run_id"] == "20261111-100000-host"
    assert rep["sandbox"] == ".superlite/sandboxes/host/export"
    assert proto.proto_report({**st, "run_id": "20261111-999999-host"}) is None


def test_sees_uncommitted_code(repo):
    inproc("start", "--topic", "export", "--run", "python entry.py", cwd=repo)
    (repo / "lib.py").write_text("VALUE = 2\n")  # modified, not committed
    sb = repo / ".superlite" / "sandboxes" / "host" / "export"
    (sb / "entry.py").write_text(
        "import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).resolve().parents[4]))\n"
        "import lib\nprint('value', lib.VALUE)\n")
    cp = subprocess.run([sys.executable, "entry.py"], cwd=sb, capture_output=True, text=True)
    assert cp.stdout.strip() == "value 2", cp.stderr


def test_session_row_follows_state_changes(repo, rows):
    if REAL:
        pytest.skip("rows are read from the registry under SUPERLITE_REAL")
    inproc("start", "--topic", "export", cwd=repo)
    inproc("tryout", cwd=repo)
    inproc("discard", cwd=repo)
    assert [r["status"] for r in rows] == ["building", "tryout", "discarded"]
    assert rows[0]["path"] == ".superlite/sandboxes/host/export" and rows[0]["run_id"] == feature(repo)["run_id"]
    assert rows[-1]["ended"]


def test_row_failure_never_breaks_proto(repo, monkeypatch):
    def boom(*a, **k):
        raise OSError("registry locked")
    monkeypatch.setattr(session_sandboxes, "record", boom)
    assert inproc("start", "--topic", "export", cwd=repo).returncode == 0
    assert inproc("tryout", cwd=repo).returncode == 0


def test_discard_removes_sandbox_and_keeps_report(repo):
    inproc("start", "--topic", "export", cwd=repo)
    sb = repo / ".superlite" / "sandboxes" / "host" / "export"
    head = {"wall_min": {"projected": 4.0, "actual": 5.0}}
    (repo / ".superlite" / "report.json").write_text(json.dumps({"headline": head, "tasks": [{}, {}]}))
    cp = inproc("discard", cwd=repo)
    assert cp.returncode == 0, cp.both
    st = feature(repo)
    assert st["status"] == "discarded" and st["proto_report"]["headline"] == head and not sb.exists()
    assert inproc("discard", cwd=repo).out.startswith("proto: nothing")


@pytest.mark.parametrize("topic", ["../x", "a/b", "A", "", ".hidden", "x" * 60])
def test_topic_cannot_leave_the_sandboxes_folder(repo, topic):
    assert inproc("start", "--topic", topic, cwd=repo).returncode == 2
    assert not (repo / ".superlite" / proto.FILE).exists()


def test_path_leaving_sandboxes_is_refused(repo, monkeypatch):
    monkeypatch.setattr(sandbox, "path_for", lambda checkout, session, topic: Path(checkout) / "src" / topic)
    cp = inproc("start", "--topic", "export", cwd=repo)
    assert cp.returncode == 2 and not (repo / "src").exists()


def test_existing_sandbox_folder_is_not_reused(repo):
    sb = repo / ".superlite" / "sandboxes" / "host" / "export"
    sb.mkdir(parents=True)
    (sb / "keep.txt").write_text("x")
    assert inproc("start", "--topic", "export", cwd=repo).returncode == 2
    assert (sb / "keep.txt").is_file() and not (repo / ".superlite" / proto.FILE).exists()


def test_legacy_worktree_state_still_discards(repo):
    wt = repo / ".claude" / "worktrees" / "export-proto"
    git(repo, "worktree", "add", str(wt), "-b", "superlite/export-proto", "HEAD")
    proto.write_state(repo / ".superlite", {"v": 1, "topic": "export", "worktree": wt.as_posix(), "branch": "superlite/export-proto",
                                            "base": "main", "status": "building", "rounds": 1})
    assert "worktree" in inproc("status", cwd=repo).out
    cp = inproc("discard", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert not wt.exists() and "superlite/export-proto" not in git(repo, "branch", "--list")
    assert feature(repo)["status"] == "discarded"
