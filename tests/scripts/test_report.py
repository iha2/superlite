"""report.py: projected vs actual build report, the saved projection, n/a where nothing is projected, delta math."""
from __future__ import annotations

import json

import sys

from test_plan_tools import SCRIPTS

sys.path.insert(0, str(SCRIPTS))
import report  # noqa: E402
from test_metrics_history import setup_run
from test_plan_tools import SCRIPTS, run
from test_routing import mech_plan

REPORT = SCRIPTS / "report.py"


def go(tmp_path, p, *extra):
    cp = run(REPORT, "--plan", p, "--dir", tmp_path / ".superlite", *extra, cwd=tmp_path)
    assert cp.returncode == 0, cp.both
    return cp.out


def fixture(tmp_path):
    p = mech_plan(tmp_path, 2)
    setup_run(tmp_path)
    return p


def test_normal_run_with_saved_projection_and_tests(tmp_path):
    p = fixture(tmp_path)
    sl = tmp_path / ".superlite"
    assert run(SCRIPTS / "plan_tools.py", "lint", "--stable", "--plan", p, cwd=tmp_path).returncode in (0, 1)
    assert (sl / "projection.json").is_file()  # written by the stable lint
    (sl / "test-summary.json").write_text(json.dumps({"passed": 9, "failed": 1, "skipped": 0}), encoding="utf-8")
    c = json.loads(go(tmp_path, p, "--json"))
    assert c["projection_note"].startswith("projection: fixed forecast")
    h = c["headline"]
    assert h["wall_min"]["projected"] and h["wall_min"]["actual"] is not None and h["agents"]["actual"] == 2
    assert c["quality"]["tests"]["failed"] == 1 and c["quality"]["first_time_n"] == 3 and c["quality"]["first_time_pass"] == 2
    assert c["concurrency"]["peak"] >= 1 and c["cost_split"]["total_usd"] >= c["cost_split"]["agents_usd"]
    txt = go(tmp_path, p)  # the default is the summary
    assert "tests 9 passed, 1 failed" in txt and "takeaway: " in txt and "model mix:" not in txt and len(txt.splitlines()) <= 10
    assert txt.rstrip().splitlines()[-1] == "Full report: " + (p.with_name(p.stem + ".report.md")).as_posix()
    full = go(tmp_path, p, "--full")
    assert "Projected" in full and "model mix:" in full and full.splitlines() == (sl / "report.md").read_text(encoding="utf-8").splitlines() == p.with_name(p.stem + ".report.md").read_text(encoding="utf-8").splitlines()
    assert json.loads((sl / "report.json").read_text(encoding="utf-8"))["quality"]["tests"]["failed"] == 1


def test_missing_projection_recomputes_with_a_note_and_stays_fixed_once_saved(tmp_path):
    p = fixture(tmp_path)
    txt = go(tmp_path, p, "--full")
    assert "no saved forecast, recomputed" in txt
    sl = tmp_path / ".superlite"
    report.save_projection(p, sl)
    first = (sl / "projection.json").read_text(encoding="utf-8")
    p.write_text(p.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert report.save_projection(p, sl) is None and (sl / "projection.json").read_text(encoding="utf-8") == first
    assert "(plan edited since)" in go(tmp_path, p, "--full")


def test_unreadable_plan_gives_na_never_invented_numbers(tmp_path):
    fixture(tmp_path)
    bad = tmp_path / "missing-plan.md"
    proj, note = report.load_projection(bad, tmp_path / ".superlite")
    assert proj is None and "n/a" in note
    h = {"wall_min": {"projected": None, "actual": 3.0}}
    assert report.delta(None, 3.0) == {"abs": None, "pct": None}
    assert report.delta(0, 3.0) == {"abs": 3.0, "pct": None} and h


def test_missing_test_summary_is_na(tmp_path):
    p = fixture(tmp_path)
    assert "tests n/a (no test-summary.json)" in go(tmp_path, p, "--full") and "  tests n/a;" in go(tmp_path, p)


def test_delta_math_and_takeaway(tmp_path):
    assert report.delta(10.0, 8.2) == {"abs": -1.8, "pct": -18.0}
    assert report.delta(2.0, 3.0) == {"abs": 1.0, "pct": 50.0}
    c = {"headline": {"wall_min": {"projected": 10.0, "actual": 8.2}, "cost_usd_agents": {"projected": 2.0, "actual": 2.18}},
         "tasks": [{"task": "T13", "delta": {"abs": 2.0, "pct": 90.0}}]}
    assert report.takeaway(c) == "ran 18% faster than projected; cost 9% over; biggest miss T13"
    c["headline"]["wall_min"]["projected"] = None
    assert report.takeaway(c).startswith("cost 9% over")
    rows = [{"start_s": 0, "done_s": 60}, {"start_s": 60, "done_s": 120}, {"start_s": 30, "done_s": 90}]
    assert report.peak_concurrency(rows) == 2  # an end and a start at the same second do not overlap


def test_two_builds_in_one_project_keep_two_reports_and_list_and_show_them(tmp_path):
    p1 = fixture(tmp_path)
    go(tmp_path, p1)
    p2 = mech_plan(tmp_path, 1, name="second.md")
    go(tmp_path, p2)  # the same .superlite, a second plan: the latest copy changes, the first build's pair stays
    assert p1.with_name("plan.report.md").is_file() and p2.with_name("second.report.md").is_file()
    assert p1.with_name("plan.report.json").is_file() and p2.with_name("second.report.json").is_file()
    assert (tmp_path / ".superlite" / "report.md").read_bytes() == p2.with_name("second.report.md").read_bytes()
    lst = run(REPORT, "--list", "--plans", tmp_path, cwd=tmp_path).out
    assert "plan  " in lst and "second  " in lst
    assert len(lst.strip().splitlines()) == 2 and "time " in lst and "cost " in lst
    shown = run(REPORT, "--show", "plan", "--plans", tmp_path, cwd=tmp_path).out
    assert shown.startswith("build report (projected -> actual)") and "plan.report.md" in shown
    assert "model mix:" in run(REPORT, "--show", "plan.md", "--full", "--plans", tmp_path, cwd=tmp_path).out
    assert "no stored report for nope" in run(REPORT, "--show", "nope", "--plans", tmp_path, cwd=tmp_path).out


def test_post_finish_report_uses_the_matching_archive(tmp_path):
    p = fixture(tmp_path)
    sl = tmp_path / ".superlite"
    import run_archive
    archived = run_archive.archive(sl, "done", plan=p.as_posix(), rid="finished")
    assert archived is not None and not (sl / "metrics.jsonl").exists()
    out = go(tmp_path, p)
    assert "build report (projected -> actual)" in out
    assert (archived / "report.json").is_file()
    assert p.with_name("plan.report.json").is_file()
