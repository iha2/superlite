"""history.py: unique_rows, no double recording, the phase field, stats over unique rows."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import history  # noqa: E402


def row(task="T1", run="r1", date="2026-11-10", dur=60.0, tin=10, tout=5, model="sonnet", **kw):
    return {"v": 1, "date": date, "run": run, "source": "run", "task": task, "kind": "design", "loc": 50, "tests": "fast",
            "band": "design/le60/fast", "tier": "sonnet", "model": model, "duration_s": dur,
            "tokens": {"in": tin, "out": tout, "cache_read": 0, "cache_write": 0}, "cost_usd": 0.1, **kw}


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("SUPERLITE_HOME", str(home))
    sl = tmp_path / "proj" / ".superlite"
    sl.mkdir(parents=True)
    return home, sl


def fake_rows(monkeypatch, rows):
    monkeypatch.setattr(history, "run_rows", lambda plan, sl_dir=".superlite", run_id=None: [dict(r, run=run_id or r["run"]) for r in rows])


def lines(p):
    return [json.loads(x) for x in Path(p).read_text(encoding="utf-8").splitlines()]


def test_unique_rows_drops_content_duplicates():
    a = row()
    assert history.unique_rows([a, dict(a, run="r2")]) == [a]


def test_unique_rows_keeps_distinct():
    a, b = row(), row(task="T2")
    assert history.unique_rows([a, b]) == [a, b]
    c = row(tout=6)
    assert history.unique_rows([a, c]) == [a, c]


def test_record_run_twice_records_once(env, monkeypatch):
    home, sl = env
    fake_rows(monkeypatch, [row(), row(task="T2")])
    assert len(history.record_run("P.md", sl, "X1")) == 2
    assert history.record_run("P.md", sl, "X1") == []
    assert len(lines(home / "history.jsonl")) == 2
    assert len(lines(sl / "history.jsonl")) == 2
    assert json.loads((sl / "stats.json").read_text())["rows"] == 2


def test_record_run_new_id_is_recorded(env, monkeypatch):
    home, sl = env
    fake_rows(monkeypatch, [row()])
    history.record_run("P.md", sl, "X1")
    history.record_run("P.md", sl, "X2")
    assert [r["run"] for r in lines(home / "history.jsonl")] == ["X1", "X2"]


def test_phase_build_default(env, monkeypatch):
    import proto
    monkeypatch.setattr(proto, "originating_run", lambda cwd: None)
    assert history.phase_of(env[1]) == "build"


def test_phase_proto(env, monkeypatch):
    import proto
    seen = []
    monkeypatch.setattr(proto, "originating_run", lambda cwd: seen.append(Path(cwd)) or Path("/hub/.superlite"))
    assert history.phase_of(env[1]) == "proto"
    assert seen == [env[1].resolve().parent]


def test_run_rows_carry_phase(env, monkeypatch):
    import metrics
    import plan_tools as pt
    import proto
    import routing
    monkeypatch.setattr(proto, "originating_run", lambda cwd: Path("/hub/.superlite"))
    rec = {"task": "T1", "kind": "design", "loc": 50, "components": 1, "tests": "fast", "model": "sonnet", "agent_type": "x",
           "duration_s": 5.0, "tokens": {"in": 1, "out": 1, "cache_read": 0, "cache_write": 0}, "cost_usd": 0.0,
           "tool_calls": 1, "first_time_pass": True, "rework_s": 0, "rework_cost": 0}
    monkeypatch.setattr(pt.Plan, "load", classmethod(lambda cls, p: type("P", (), {"by_id": {}})()))
    monkeypatch.setattr(metrics, "task_records", lambda sl, plan: [rec])
    monkeypatch.setattr(history, "predicted_flags", lambda plan, args=None: {})
    monkeypatch.setattr(routing, "read_tiers", lambda p: {})
    monkeypatch.setattr(pt, "review_focus_cases", lambda plan: {})
    assert [r["phase"] for r in history.run_rows("P.md", env[1], "X1")] == ["proto"]
    monkeypatch.setattr(proto, "originating_run", lambda cwd: None)
    assert [r["phase"] for r in history.run_rows("P.md", env[1], "X1")] == ["build"]


def test_stats_use_unique_rows(tmp_path):
    a = row()
    p = tmp_path / "stats.json"
    st = history.write_stats([a, dict(a, run="r2"), row(task="T2")], p)
    assert st["rows"] == 2 and st["bands"]["design/le60/fast"]["n"] == 2
    assert json.loads(p.read_text())["rows"] == 2


def test_old_rows_without_phase_read_fine(env):
    home, sl = env
    history.append_rows([row()], home / "history.jsonl")
    rows = history.read_rows()
    assert rows and "phase" not in rows[0]
    assert history.write_stats(rows, home / "stats.json")["rows"] == 1


def test_stats_skip_proto_rows(tmp_path):
    build = row()
    p1, p2 = tmp_path / "a.json", tmp_path / "b.json"
    a = history.write_stats([build], p1)
    b = history.write_stats([build, row(task="T9", run="r2", dur=999.0, phase="proto")], p2)
    assert a == b


def test_proto_row_does_not_shadow_identical_build_row(tmp_path):
    build = row()
    st = history.write_stats([dict(build, phase="proto", run="r0"), build], tmp_path / "s.json")
    assert st["rows"] == 1
