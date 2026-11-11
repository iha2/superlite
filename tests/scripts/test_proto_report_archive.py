"""proto_report falls back to the newest archived run of the same checkout."""
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
