"""history.phase_of and the run rows, report.feature_parts: the open sandbox decides `proto`; rows and report carry run_id and sandbox."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import history  # noqa: E402
import report  # noqa: E402
import sandbox  # noqa: E402

SB_REL = ".superlite/sandboxes/host/x"


@pytest.fixture
def seam(monkeypatch):
    """Fake sandbox.open_sandbox by default (T1 contract); the real one when SUPERLITE_REAL=1."""
    if os.environ.get("SUPERLITE_REAL") == "1":
        return
    def fake(sl):
        try:
            st = json.loads((Path(sl) / "feature.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if st.get("status") == "building" and st.get("sandbox"):
            return Path(st["sandbox"]).relative_to(Path(sl).resolve().parent).as_posix()
        return None
    monkeypatch.setattr(sandbox, "open_sandbox", fake)


def make(tmp_path, **state):
    sl = tmp_path.resolve() / "proj" / ".superlite"
    sl.mkdir(parents=True)
    if state:
        (sl / "feature.json").write_text(json.dumps({"v": 1, "topic": "x", **state}), encoding="utf-8")
    return sl


def sb_state(sl, status="building"):
    return {"sandbox": (sl.parent / SB_REL).as_posix(), "status": status, "run_id": "20261111-140000-feat"}


def test_phase_proto_when_sandbox_open(tmp_path, seam):
    assert history.phase_of(make(tmp_path / "a")) == "build"
    sl = make(tmp_path / "b")
    (sl / "feature.json").write_text(json.dumps({"v": 1, "topic": "x", **sb_state(sl)}), encoding="utf-8")
    assert history.phase_of(sl) == "proto"
    (sl / "feature.json").write_text(json.dumps({"v": 1, "topic": "x", **sb_state(sl, "tryout")}), encoding="utf-8")
    assert history.phase_of(sl) == "build"


def test_legacy_worktree_phase_still_proto(tmp_path, seam):
    sl = make(tmp_path)
    (sl / "feature.json").write_text(json.dumps({"v": 1, "topic": "x", "status": "building", "worktree": sl.parent.as_posix()}), encoding="utf-8")
    assert history.phase_of(sl) == "proto"
    (sl / "feature.json").write_text(json.dumps({"v": 1, "topic": "x", "status": "building", "worktree": (tmp_path / "other").as_posix()}), encoding="utf-8")
    assert history.phase_of(sl) == "build"


def test_rows_carry_run_id_and_sandbox(tmp_path, seam, monkeypatch):
    import metrics
    import plan_tools as pt
    import routing
    sl = make(tmp_path)
    (sl / "feature.json").write_text(json.dumps({"v": 1, "topic": "x", **sb_state(sl)}), encoding="utf-8")
    rec = {"task": "T1", "model": "sonnet", "kind": "design", "loc": 5, "components": 1, "tests": "fast", "agent_type": "a",
           "duration_s": 1.0, "tokens": {"in": 1, "out": 1, "cache_read": 0, "cache_write": 0}, "cost_usd": 0.1, "tool_calls": 1,
           "first_time_pass": True, "rework_s": 0, "rework_cost": 0}
    monkeypatch.setattr(metrics, "task_records", lambda sl, p: [rec])
    monkeypatch.setattr(pt.Plan, "load", classmethod(lambda cls, p: type("P", (), {"by_id": {}})()))
    monkeypatch.setattr(pt, "review_focus_cases", lambda plan: {})
    monkeypatch.setattr(history, "predicted_flags", lambda plan: {})
    monkeypatch.setattr(routing, "read_tiers", lambda p: {})
    rows = history.run_rows("P.md", sl, "r1")
    assert rows[0]["phase"] == "proto"
    assert rows[0]["run_id"] == "20261111-140000-feat"
    assert rows[0]["sandbox"] == SB_REL


def test_report_json_has_run_id_and_sandbox(tmp_path, seam):
    sl = make(tmp_path)
    (sl / "feature.json").write_text(json.dumps({"v": 1, "topic": "x", **sb_state(sl)}), encoding="utf-8")
    f = report.feature_parts(sl, {})
    assert f["run_id"] == "20261111-140000-feat"
    assert f["sandbox"] == SB_REL
