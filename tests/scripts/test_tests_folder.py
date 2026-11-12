"""Test results live in .superlite/tests/; old archives with a root test-summary.json still read."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from test_0_3_1_scripts import project, run

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import proto_discard  # noqa: E402
import review_packet  # noqa: E402
import run_tests  # noqa: E402

SUFFIXES = {".json", ".log", ".done", ".suspect"}


def test_root_clean_after_runs(tmp_path):
    proj = project(tmp_path, "pass")
    assert run("run_tests", "--fast", "test_pass.py", cwd=proj).returncode == 0
    r = run("run_tests", "--fast", "test_pass.py", "--background", cwd=proj)
    assert r.returncode == 0, r.stderr
    run_id = re.search(r"--run (\S+)", r.stdout).group(1)
    assert run("run_tests", "--wait", "--run", run_id, "--timeout", "120", cwd=proj).returncode == 0
    sl = proj / ".superlite"
    assert not [p.name for p in sl.iterdir() if p.name.startswith("test-")]
    assert (sl / "tests" / "summary.json").is_file()
    assert (sl / "tests" / "latest").is_file()


def test_background_files_in_runs(tmp_path):
    proj = project(tmp_path, "pass")
    r = run("run_tests", "--fast", "test_pass.py", "--background", cwd=proj)
    run_id = re.search(r"--run (\S+)", r.stdout).group(1)
    assert run("run_tests", "--wait", "--run", run_id, "--timeout", "120", cwd=proj).returncode == 0
    assert run("run_tests", "--wait", "--all", "--timeout", "60", cwd=proj).returncode == 0
    runs = proj / ".superlite" / "tests" / "runs"
    assert (runs / f"{run_id}.json").is_file() and (runs / f"{run_id}.log").is_file()
    assert (runs / f"{run_id}.done").is_file()
    assert {p.suffix for p in runs.iterdir()} <= SUFFIXES
    assert json.loads((runs / f"{run_id}.json").read_text(encoding="utf-8"))["run_id"] == run_id


def test_summary_carries_superlite_run(tmp_path):
    proj = project(tmp_path, "pass")
    sl = proj / ".superlite"
    sl.mkdir()
    (sl / "current-run.json").write_text(json.dumps(
        {"id": "20260101-000000-demo", "topic": "t", "slug": "demo", "started_at": "x"}), encoding="utf-8")
    assert run("run_tests", "--fast", "test_pass.py", cwd=proj).returncode == 0
    s = json.loads((sl / "tests" / "summary.json").read_text(encoding="utf-8"))
    assert s["superlite_run"] == "20260101-000000-demo"


def test_summary_path_fallback(tmp_path):
    new, old = tmp_path / "tests" / "summary.json", tmp_path / "test-summary.json"
    assert run_tests.summary_path(tmp_path) == new
    old.write_text("{}", encoding="utf-8")
    assert run_tests.summary_path(tmp_path) == old
    new.parent.mkdir()
    new.write_text("{}", encoding="utf-8")
    assert run_tests.summary_path(tmp_path) == new


def test_pending_runs_under_tests_runs(tmp_path):
    summary = run_tests.summary_path(tmp_path)
    runs = tmp_path / "tests" / "runs"
    runs.mkdir(parents=True)
    (runs / "a.log").write_text("")
    (runs / "b.log").write_text("")
    (runs / "b.done").write_text("")
    assert [p.name for p in run_tests.pending_runs(summary)] == ["a.json"]


def test_packet_reads_new_and_old(tmp_path):
    data = {"passed": 3, "failed": 1, "skipped": 0, "mode": "full", "failures": ["t.py::x: boom"]}
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "summary.json").write_text(json.dumps(data), encoding="utf-8")
    text, nfail = review_packet.read_test_summary(run_tests.summary_path(tmp_path))
    assert nfail == 1 and "boom" in text
    old = tmp_path / "old"
    old.mkdir()
    (old / "test-summary.json").write_text(json.dumps(data), encoding="utf-8")
    text, nfail = review_packet.read_test_summary(run_tests.summary_path(old))
    assert nfail == 1 and "boom" in text
    (tmp_path / "tests" / "latest").write_text(
        json.dumps({"id": "x", "summary": str(tmp_path / "tests" / "runs" / "x.json")}), encoding="utf-8")
    assert review_packet.read_test_summary(tmp_path / "tests" / "summary.json")[0].startswith("- tests: running")


def test_proto_discard_reads_new_and_old(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(run_tests, "pending_runs", lambda p: seen.append(Path(p)) or [])
    assert proto_discard.drain_tests(tmp_path)
    assert seen == [tmp_path / "tests" / "summary.json"]
    (tmp_path / "test-summary.json").write_text("{}", encoding="utf-8")
    seen.clear()
    assert proto_discard.drain_tests(tmp_path)
    assert seen == [tmp_path / "test-summary.json"]


def test_rewrite_message_path():
    text = (ROOT / "hooks" / "_rewrite").read_text(encoding="utf-8")
    assert ".superlite/tests/summary.json" in text and ".superlite/test-summary.json" not in text
