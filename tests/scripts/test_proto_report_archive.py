"""proto_report: a sandbox prototype finds its archived run by run_id; a legacy worktree one the newest run of its checkout."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import proto  # noqa: E402


@pytest.fixture
def wt(tmp_path):
    w = tmp_path.resolve() / "wt"
    w.mkdir()
    subprocess.run(["git", "init", "-q", str(w)], check=True)
    return w


def archive(wt, name, checkout, at, headline=None):
    d = wt / ".superlite" / "runs" / name
    d.mkdir(parents=True)
    (d / "run.json").write_text(json.dumps({"id": name, "checkout": Path(checkout).as_posix(), "archived_at": at}))
    if headline is not None:
        (d / "report.json").write_text(json.dumps({"headline": headline, "cost_split": {"a": 1}, "tasks": [1, 2]}))
    return d


def st(wt):
    return {"worktree": wt.as_posix()}


def test_report_from_archive(wt):
    archive(wt, "A", wt, "2026-11-10T10:00:00", "from A")
    assert proto.proto_report(st(wt)) == {"headline": "from A", "cost_split": {"a": 1}, "tasks": 2}


def test_newest_archive_wins(wt):
    archive(wt, "A", wt, "2026-11-10T10:00:00", "old")
    archive(wt, "B", wt, "2026-11-10T11:00:00", "new")
    archive(wt, "C", wt, "2026-11-10T12:00:00")  # newest but no report
    assert proto.proto_report(st(wt))["headline"] == "new"


def test_other_checkout_ignored(wt, tmp_path):
    archive(wt, "A", tmp_path / "elsewhere", "2026-11-10T10:00:00", "not mine")
    assert proto.proto_report(st(wt)) is None


def test_saved_proto_report_still_first(wt):
    archive(wt, "A", wt, "2026-11-10T10:00:00", "from A")
    (wt / ".superlite" / "report.json").write_text(json.dumps({"headline": "live"}))
    assert proto.proto_report(st(wt))["headline"] == "live"
    assert proto.proto_report({**st(wt), "proto_report": {"headline": "saved"}}) == {"headline": "saved"}


def sandbox_state(wt, rid):
    return {"sandbox": (wt / ".superlite" / "sandboxes" / "host" / "export").as_posix(), "run_id": rid}


def test_sandbox_report_found_by_run_id_not_newest(wt):
    archive(wt, "20261110-100000-host", wt, "2026-11-10T10:00:00", "mine")
    archive(wt, "20261110-110000-host", wt, "2026-11-10T11:00:00", "newer, same checkout")
    rep = proto.proto_report(sandbox_state(wt, "20261110-100000-host"))
    assert rep["headline"] == "mine" and rep["run_id"] == "20261110-100000-host" and rep["sandbox"] == ".superlite/sandboxes/host/export"


def test_sandbox_report_none_without_matching_id(wt):
    archive(wt, "20261110-100000-host", wt, "2026-11-10T10:00:00", "mine")
    assert proto.proto_report(sandbox_state(wt, None)) is None
    assert proto.proto_report(sandbox_state(wt, "20261110-120000-host")) is None


def test_sandbox_live_report_first(wt):
    archive(wt, "20261110-100000-host", wt, "2026-11-10T10:00:00", "archived")
    (wt / ".superlite" / "report.json").write_text(json.dumps({"headline": "live"}))
    assert proto.proto_report(sandbox_state(wt, "20261110-100000-host"))["headline"] == "live"
