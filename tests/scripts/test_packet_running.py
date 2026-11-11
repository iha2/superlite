"""review_packet.read_test_summary says when the newest test run is still going, and names affected selections."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import review_packet  # noqa: E402

RUNNING = "- tests: running (newest run not finished; results reach the fixers)"


def _point(tmp_path: Path, summary: Path) -> None:
    (tmp_path / "test-summary.latest").write_text(
        json.dumps({"id": "x", "summary": str(summary)}) + "\n", encoding="utf-8")


def test_running_when_pointer_unfinished(tmp_path):
    _point(tmp_path, tmp_path / "test-summary-x.json")  # the pointed run has no summary yet
    text, nfail = review_packet.read_test_summary(tmp_path / "test-summary.json")
    assert text == RUNNING
    assert nfail == 0


def test_finished_unchanged(tmp_path):
    data = {"passed": 3, "failed": 1, "skipped": 0, "mode": "full",
            "failures": ["tests/a.py::t: boom"], "skipped_tests": []}
    (tmp_path / "test-summary-x.json").write_text(json.dumps(data), encoding="utf-8")
    (tmp_path / "test-summary.json").write_text(json.dumps(data), encoding="utf-8")
    _point(tmp_path, tmp_path / "test-summary-x.json")
    text, nfail = review_packet.read_test_summary(tmp_path / "test-summary.json")
    assert text == "- passed 3 · failed 1 · skipped 0\n- FAILING: tests/a.py::t: boom"
    assert nfail == 1


def test_affected_shows_selected(tmp_path):
    data = {"passed": 2, "failed": 0, "skipped": 0, "mode": "affected",
            "selected": ["tests/scripts/test_a.py", "tests/scripts/test_b.py"],
            "failures": [], "skipped_tests": []}
    (tmp_path / "test-summary.json").write_text(json.dumps(data), encoding="utf-8")
    text, nfail = review_packet.read_test_summary(tmp_path / "test-summary.json")
    assert text == "- passed 2 · failed 0 · skipped 0\n- affected: 2 test files selected"
    assert nfail == 0


def test_no_summary_text(tmp_path):
    text, nfail = review_packet.read_test_summary(tmp_path / "test-summary.json")
    assert text == "- no test summary recorded"
    assert "--full" not in text
    assert nfail == 0
