"""report.trend_line, the lessons line, and reading the summary from the tests folder."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "swarm" / "scripts"))

import lessons_store  # noqa: E402
import report  # noqa: E402


def row(run, ftp, findings=None, cost=1.0, rework=0.0):
    return {"v": 1, "run": run, "task": "T1", "repository": "/r", "first_time_pass": ftp,
            "findings": findings, "cost_usd": cost, "rework_cost": rework}


def test_trend_line():
    rows = [row("r1", False, 4, 7, 3), row("r1", True, 0, 7, 0), row("r2", True, 2, 8, 2),
            row("r2", False, 0, 8, 0), row("r2", True, 0, 0, 0), row("r3", True, 1, 9, 1)]
    assert report.trend_line(rows) == ("trend (last 3 runs): first-time pass 50% -> 67% -> 100%; "
                                       "findings 4 -> 2 -> 1; rework+fixer cost 21% -> 12% -> 11%")


def test_trend_last_n_and_empty():
    assert report.trend_line([]) == ""
    rows = [row(f"r{i}", True, i) for i in range(1, 6)]
    assert "last 2 runs" in report.trend_line(rows, last_n=2)
    assert "findings 4 -> 5" in report.trend_line(rows, last_n=2)


def test_report_lessons_line(tmp_path):
    hub = tmp_path / ".superlite"
    store = lessons_store.empty()
    for i, proven in enumerate((True, False, False)):
        store["lessons"][f"l{i}"] = {"status": "cached", "proven": proven}
    store["lessons"]["gone"] = {"status": "evicted", "proven": False}
    hub.mkdir()
    (hub / "lessons.json").write_text(json.dumps(store), encoding="utf-8")
    assert report.lessons_line(lessons_store.load(hub)) == "lessons: 3 cached, 1 proven"
    assert report.lessons_line(lessons_store.empty()) == ""


def test_report_reads_new_summary(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "summary.json").write_text(json.dumps({"passed": 4, "failed": 0}), encoding="utf-8")
    assert report.read_tests(tmp_path)["passed"] == 4
    old = tmp_path / "old"
    old.mkdir()
    (old / "test-summary.json").write_text(json.dumps({"passed": 2, "failed": 1}), encoding="utf-8")
    assert report.read_tests(old)["failed"] == 1
