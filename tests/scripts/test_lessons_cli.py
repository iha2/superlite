"""Tests for the brief, report and CLI; lessons_score is faked unless SUPERLITE_REAL=1."""
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import lessons as lessons_cli  # noqa: E402
import lessons_store as lessons  # noqa: E402


@pytest.fixture(autouse=True)
def score(monkeypatch):
    if os.environ.get("SUPERLITE_REAL") == "1":
        return
    fake = types.SimpleNamespace()
    fake.module_key = lambda p: p.rsplit("/", 1)[0] if "/" in p else "."
    fake.line = lambda les: f"- {les['module']}: {les['text']} ({les['kind']}, {len(les['runs'])} runs)"
    fake.rank = lambda les: les["u"]

    def select(ls, budget):
        out, used = [], 0
        for les in sorted(ls, key=lambda x: -x["u"]):
            n = len(fake.line(les)) + 1
            if used + n > budget:
                break
            out.append(les)
            used += n
        return out

    fake.select = select
    monkeypatch.setattr(lessons_cli, "lessons_score", fake)


def seed(hub, module="skills/x", key="k", kind="pitfall", text="avoid: boom", u=0.1, status="cached", n=1):
    def fn(store):
        lid = lessons.add_signal(store, kind, module, key, text, "s", "r1")
        les = store["lessons"][lid]
        les.update(status=status, u=u, baseline=0.5,
                   deliveries=[{"run": "r1", "task": "T", "hit": True}] * n)
        return store
    lessons.update(hub, fn)


def lines(p):
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]


def test_brief_with_matching_lesson_logs_delivery(tmp_path):
    hub, sl = tmp_path / "hub", tmp_path / "sl"
    seed(hub)
    out = lessons_cli.brief(hub, sl, "r3", "T2", ["skills/x/y.py"])
    assert out.startswith("In this repo (learned from past runs):\n- skills/x: avoid: boom")
    assert out.endswith("\n")
    rows = lines(sl / "deliveries.jsonl")
    assert len(rows) == 1
    assert rows[0] == {"v": 1, "run": "r3", "task": "T2", "agent": "implementer",
                       "lessons": [lessons.lesson_id("pitfall", "skills/x", "k")]}


def test_no_block_for_unrelated_task(tmp_path):
    hub, sl = tmp_path / "hub", tmp_path / "sl"
    seed(hub)
    assert lessons_cli.brief(hub, sl, "r3", "T9", ["docs/a.md"]) == ""
    assert not (sl / "deliveries.jsonl").exists()


def test_candidate_and_empty_store_give_nothing(tmp_path):
    hub, sl = tmp_path / "hub", tmp_path / "sl"
    assert lessons_cli.brief(hub, sl, "r3", "T2", ["skills/x/y.py"]) == ""
    seed(hub, status="candidate")
    assert lessons_cli.brief(hub, sl, "r3", "T2", ["skills/x/y.py"]) == ""
    assert not (sl / "deliveries.jsonl").exists()


def test_one_delivery_line_per_brief(tmp_path):
    hub, sl = tmp_path / "hub", tmp_path / "sl"
    seed(hub, key="a")
    seed(hub, key="b")
    lessons_cli.brief(hub, sl, "r3", "T2", ["skills/x/y.py", "skills/x/z.py"])
    lessons_cli.brief(hub, sl, "r3", "T3", ["skills/x/y.py"])
    rows = lines(sl / "deliveries.jsonl")
    assert [r["task"] for r in rows] == ["T2", "T3"]
    assert len(rows[0]["lessons"]) == 2


def test_budget_by_agent(tmp_path):
    hub, sl = tmp_path / "hub", tmp_path / "sl"
    for i in range(30):
        seed(hub, key=f"k{i}", text="avoid: " + "z" * 100, u=i / 100)
    sizes = {}
    for agent in ("implementer", "fixer", "reviewer", "drafter"):
        out = lessons_cli.brief(hub, sl, "r3", "T" + agent, ["skills/x/y.py"], agent)
        body = out.split("\n", 1)[1]
        sizes[agent] = len(body)
    assert sizes["implementer"] <= 1000 and sizes["fixer"] <= 1000
    assert 1000 < sizes["reviewer"] <= 2000 and sizes["drafter"] <= 2000
    assert sizes["reviewer"] > sizes["implementer"]


def test_report_sections(tmp_path):
    hub = tmp_path / "hub"
    seed(hub, key="a")
    seed(hub, key="p", kind="practice", text="do: run t.py", u=0.3, n=5)
    hist = [
        {"v": 1, "repository": "x", "date": "d1", "run": "r1", "task": "T1", "first_time_pass": False, "findings": 2,
         "rework_cost": 1.0, "cost_usd": 4.0, "status": "unfinished"},
        {"v": 1, "repository": "x", "date": "d1", "run": "r1", "task": "T2", "first_time_pass": True, "findings": 0,
         "rework_cost": 0.0, "cost_usd": 4.0, "status": "unfinished"},
        {"v": 1, "repository": "x", "date": "d2", "run": "r2", "task": "T1", "first_time_pass": True, "findings": 1,
         "rework_cost": 0.0, "cost_usd": 2.0, "status": "complete"},
    ]
    out = lessons_cli.report(hub, hist)
    assert "failures by category:" in out
    assert "trend (last 10 runs):" in out
    assert "what works here:" in out
    assert "r1" in out and "r2" in out  # unfinished run included
    assert "50%" in out  # r1 first-time pass 1/2
    assert "12%" in out  # r1 rework share 1/8
    assert "U 0.30" in out and "deliveries 5" in out and "baseline 0.50" in out
    assert "hits 5" in out


def test_report_last_n_and_empty(tmp_path):
    hist = [{"repository": "x", "date": f"d{i}", "run": f"r{i}", "task": "T", "first_time_pass": True, "findings": 0,
             "rework_cost": 0, "cost_usd": 1} for i in range(5)]
    out = lessons_cli.report(tmp_path / "hub", hist, last_n=2)
    assert "r4" in out and "r3" in out and "r0" not in out
    assert "failures by category:" in lessons_cli.report(tmp_path / "hub", [])


def test_main_list_sorted_by_u(tmp_path, capsys):
    hub = tmp_path / "hub"
    seed(hub, key="lo", text="avoid: lo", u=0.1)
    seed(hub, key="hi", text="avoid: hi", u=0.4)
    assert lessons_cli.main(["list", "--hub", str(hub)]) == 0
    out = capsys.readouterr().out
    assert out.index("avoid: hi") < out.index("avoid: lo")


def test_main_drop_pin_restore(tmp_path, capsys):
    hub = tmp_path / "hub"
    seed(hub)
    lid = lessons.lesson_id("pitfall", "skills/x", "k")
    assert lessons_cli.main(["pin", lid, "--hub", str(hub)]) == 0
    assert lessons.load(hub)["lessons"][lid]["pinned"] is True
    assert lessons_cli.main(["drop", lid, "--hub", str(hub)]) == 0
    assert lessons.load(hub)["lessons"][lid]["status"] == "dropped"
    assert lessons_cli.main(["restore", lid, "--hub", str(hub)]) == 0
    assert lessons.load(hub)["lessons"][lid]["status"] == "candidate"


def test_main_unknown_id(tmp_path, capsys):
    assert lessons_cli.main(["drop", "L-x", "--hub", str(tmp_path / "hub")]) == 1
    assert "no lesson L-x" in capsys.readouterr().err


def test_main_brief_prints(tmp_path, capsys):
    hub, sl = tmp_path / "hub", tmp_path / "sl"
    seed(hub)
    argv = ["brief", "--hub", str(hub), "--sl", str(sl), "--run", "r3", "--task", "T2",
            "--files", "skills/x/y.py"]
    assert lessons_cli.main(argv) == 0
    assert "In this repo" in capsys.readouterr().out
    assert (sl / "deliveries.jsonl").exists()


def test_main_report_reads_history(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sl = tmp_path / "sl"
    repo = lessons_cli.history.repository_id(sl)
    hist = tmp_path / "history.jsonl"
    rows = [{"run": "r1", "task": "T1", "first_time_pass": True, "findings": 0, "rework_cost": 0, "cost_usd": 1,
             "repository": repo},
            {"run": "other", "task": "T1", "repository": "/elsewhere"}]
    hist.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    argv = ["report", "--hub", str(tmp_path / "hub"), "--sl", str(sl), "--history", str(hist)]
    assert lessons_cli.main(argv) == 0
    out = capsys.readouterr().out
    assert "trend (last 10 runs):" in out and "r1" in out and "other" not in out


def test_help_writes_no_file(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as e:
        lessons_cli.main(["--help"])
    assert e.value.code == 0
    assert list(tmp_path.iterdir()) == []


def test_help_no_side_effects(tmp_path):
    for sub in ([], ["list"], ["show"], ["drop"], ["report"], ["brief"]):
        r = subprocess.run([sys.executable, str(SCRIPTS / "lessons.py"), *sub, "--help"], cwd=tmp_path,
                           capture_output=True, text=True)
        assert r.returncode == 0 and "usage" in r.stdout
    assert list(tmp_path.iterdir()) == []


def test_brief_from_plan_owns(tmp_path, capsys, monkeypatch):
    plan = tmp_path / "plan.md"
    plan.write_text("""# P

## Stable tasks: purpose, dependencies, interfaces

### T1 thing
**Owns:** skills/x/y.py
**Consumes:** none
""", encoding="utf-8")
    hub, sl = tmp_path / "hub", tmp_path / "sl"
    seed(hub)
    (sl).mkdir()
    argv = ["brief", "--hub", str(hub), "--sl", str(sl), "--run", "r3", "--task", "T1", "--plan", str(plan)]
    assert lessons_cli.main(argv) == 0
    assert "In this repo" in capsys.readouterr().out
    assert lines(sl / "deliveries.jsonl")[0]["task"] == "T1"


def test_brief_fixer_file_and_current_run(tmp_path, capsys):
    hub, sl = tmp_path / "hub", tmp_path / "sl"
    seed(hub)
    sl.mkdir()
    (sl / "current-run.json").write_text(json.dumps({"id": "20261112-000000-a", "topic": "t", "slug": "s",
                                                      "started_at": "2026-11-12T00:00:00Z"}), encoding="utf-8")
    argv = ["brief", "--hub", str(hub), "--sl", str(sl), "--task", "T2", "--agent", "fixer", "--file", "skills/x/y.py"]
    assert lessons_cli.main(argv) == 0
    row = lines(sl / "deliveries.jsonl")[0]
    assert row["run"] == "20261112-000000-a" and row["agent"] == "fixer"


def test_show(tmp_path, capsys):
    hub = tmp_path / "hub"
    seed(hub)
    lid = lessons.lesson_id("pitfall", "skills/x", "k")
    assert lessons_cli.main(["show", lid, "--hub", str(hub)]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == lid
    assert lessons_cli.main(["show", "L-x", "--hub", str(hub)]) == 1


def test_brief_drafter_files_without_task(tmp_path, capsys):
    hub, sl = tmp_path / "hub", tmp_path / "sl"
    seed(hub)
    sl.mkdir()
    argv = ["brief", "--hub", str(hub), "--sl", str(sl), "--run", "r3", "--agent", "drafter", "--files", "skills/x/y.py"]
    assert lessons_cli.main(argv) == 0
    assert "In this repo" in capsys.readouterr().out
    row = lines(sl / "deliveries.jsonl")[0]
    assert row["task"] is None and row["agent"] == "drafter"


def test_brief_reviewer_batched_files_with_task(tmp_path, capsys):
    hub, sl = tmp_path / "hub", tmp_path / "sl"
    seed(hub)
    sl.mkdir()
    argv = ["brief", "--hub", str(hub), "--sl", str(sl), "--run", "r3", "--agent", "reviewer", "--task", "T4",
            "--files", "skills/x/y.py", "skills/x/z.py"]
    assert lessons_cli.main(argv) == 0
    assert "In this repo" in capsys.readouterr().out
    assert lines(sl / "deliveries.jsonl")[0]["task"] == "T4"


def test_brief_plan_without_task_is_empty(tmp_path, capsys):
    hub, sl = tmp_path / "hub", tmp_path / "sl"
    seed(hub)
    sl.mkdir()
    argv = ["brief", "--hub", str(hub), "--sl", str(sl), "--run", "r3", "--plan", str(tmp_path / "none.md")]
    assert lessons_cli.main(argv) == 0
    assert capsys.readouterr().out == ""


def test_report_counts_revised_outcome_once(tmp_path):
    base = {"v": 1, "repository": "r", "run": "r1", "task": "T1", "findings": 1, "rework_cost": 0, "cost_usd": 4.0}
    hist = [dict(base, first_time_pass=False, status="unfinished"),
            dict(base, first_time_pass=True, status="complete")]
    out = lessons_cli.report(tmp_path / "hub", hist)
    assert "- r1: first-time pass 100%, findings 1," in out
