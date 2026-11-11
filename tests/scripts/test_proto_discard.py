"""proto_discard.discard_open: remove the sandbox, archive the build's run files, keep feature state (real modules)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import proto_discard  # noqa: E402
import proto  # noqa: E402
import run_tests  # noqa: E402
import session_registry  # noqa: E402
import state  # noqa: E402

SL = state.DIR_NAME


def git(cwd, *a):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd, check=True,
                          capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path.resolve() / "app"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "commit", "-q", "--allow-empty", "-m", "i")
    (r / SL).mkdir()
    return r


def open_proto(r, status="building", **extra):
    sb = r / SL / "sandboxes" / "host" / "x"
    (sb / "src").mkdir(parents=True)
    (sb / "src" / "a.py").write_text("x\n")
    st = {"v": 1, "topic": "x", "plan": "p.md", "sandbox": sb.as_posix(), "session": "host", "status": status,
          "run_id": "20261111-100000-host", **extra}
    (r / SL / "feature.json").write_text(json.dumps(st))
    (r / SL / "orchestrator.json").write_text('{"mode": "human"}\n')
    return r / SL, sb


def runs(r):
    d = r / SL / "runs"
    return sorted(d.iterdir()) if d.is_dir() else []


def test_discard_removes_folder_and_archives_markers(repo):
    sl, sb = open_proto(repo)
    for n in ("stable.json", "pool.json", "prime.md"):
        (sl / n).write_text("{}\n")
    st = proto_discard.discard_open(sl)
    assert st["status"] == "discarded" and st["discard_reason"] == "discard" and st["discarded"]
    assert not sb.exists()
    (arch,) = runs(repo)
    assert arch.name.endswith("-unfinished")
    assert {"stable.json", "pool.json", "prime.md"} <= {p.name for p in arch.iterdir()}
    assert (sl / "feature.json").is_file() and (sl / "orchestrator.json").is_file()
    assert json.loads((sl / "feature.json").read_text())["status"] == "discarded"
    assert proto_discard.discard_open(sl) is None


def test_discard_after_finish_no_archive(repo):
    sl, sb = open_proto(repo)
    assert proto_discard.discard_open(sl, reason="close")["discard_reason"] == "close"
    assert runs(repo) == [] and not sb.exists()


def test_discard_none_without_state(repo):
    assert proto_discard.discard_open(repo / SL) is None


def test_discard_refuses_while_background_tests_are_running(repo, monkeypatch, capsys):
    sl, sb = open_proto(repo)
    state = (sl / "feature.json").read_bytes()
    monkeypatch.setattr(run_tests, "pending_runs", lambda _: [Path("worker.log")])
    monkeypatch.setattr(run_tests, "wait_all", lambda _: 2)
    result = proto_discard.discard_open(sl)
    assert result == {"status": "tests-running"}
    assert (sl / "feature.json").read_bytes() == state
    assert sb.is_dir() and runs(repo) == []
    assert "tests still running; retry" in capsys.readouterr().err


def test_proto_command_refuses_while_background_tests_are_running(repo, monkeypatch, capsys):
    sl, sb = open_proto(repo)
    state = (sl / "feature.json").read_bytes()
    monkeypatch.setattr(run_tests, "pending_runs", lambda _: [Path("worker.log")])
    monkeypatch.setattr(run_tests, "wait_all", lambda _: 2)
    assert proto.main(["discard", "--dir", str(sl)]) == 2
    assert (sl / "feature.json").read_bytes() == state and sb.is_dir() and runs(repo) == []
    assert "tests still running; retry" in capsys.readouterr().err


def test_legacy_worktree_discard(repo):
    wt = repo.parent / "wt"
    git(repo, "worktree", "add", "-q", "-b", "proto-x", str(wt))
    (repo / SL / "feature.json").write_text(json.dumps(
        {"topic": "x", "worktree": wt.as_posix(), "branch": "proto-x", "status": "building"}))
    st = proto_discard.discard_open(repo / SL)
    assert st["status"] == "discarded" and st["branch_kept"] is False
    assert not wt.exists()
    assert "proto-x" not in git(repo, "branch").stdout


def test_legacy_discard_reads_report_from_its_worktree(repo):
    wt = repo.parent / "wt"
    git(repo, "worktree", "add", "-q", "-b", "proto-report", str(wt))
    (repo / SL / "report.json").write_text(json.dumps({"headline": "wrong checkout"}))
    (wt / SL).mkdir()
    (wt / SL / "report.json").write_text(json.dumps({"headline": "prototype report", "tasks": [1]}))
    (repo / SL / "feature.json").write_text(json.dumps(
        {"topic": "x", "worktree": wt.as_posix(), "branch": "proto-report", "status": "building"}))
    st = proto_discard.discard_open(repo / SL)
    assert st["proto_report"]["headline"] == "prototype report"


def test_discard_records_session(repo):
    session_registry.write(repo, {"sessions": [{"slug": "host"}], "active": "host"})
    sl, _ = open_proto(repo)
    proto_discard.discard_open(sl)
    row = session_registry.find_row(session_registry.read(repo), "host")
    (e,) = row["sandboxes"]
    assert e["topic"] == "x" and e["status"] == "discarded" and e["ended"]


def test_discard_without_session_row_still_discards(repo):
    sl, sb = open_proto(repo)
    assert proto_discard.discard_open(sl)["status"] == "discarded" and not sb.exists()


def test_discard_keeps_proto_report(repo):
    sl, _ = open_proto(repo)
    (sl / "report.json").write_text(json.dumps({"headline": "built it", "tasks": [1, 2]}))
    st = proto_discard.discard_open(sl)
    assert st["proto_report"]["headline"] == "built it" and st["proto_report"]["tasks"] == 2
    assert json.loads((sl / "feature.json").read_text())["proto_report"]["headline"] == "built it"


def test_refuses_sandbox_outside_home(repo, tmp_path):
    sl, _ = open_proto(repo)
    outside = tmp_path / "keepme"
    outside.mkdir()
    (outside / "f").write_text("x")
    st = json.loads((sl / "feature.json").read_text())
    st["sandbox"] = outside.as_posix()
    (sl / "feature.json").write_text(json.dumps(st))
    out = proto_discard.discard_open(sl)
    assert (outside / "f").is_file() and out["sandbox_kept"] is True and out["status"] == "discarded"


def test_refuses_dotdot_sandbox(repo):
    sl, _ = open_proto(repo)
    st = json.loads((sl / "feature.json").read_text())
    st["sandbox"] = (repo / SL / "sandboxes" / "host" / ".." / ".." / ".." / "src").as_posix()
    (repo / "src").mkdir()
    (sl / "feature.json").write_text(json.dumps(st))
    proto_discard.discard_open(sl)
    assert (repo / "src").is_dir()
