"""Batching: dispatch uses split.choose for implementer count and batches."""
from __future__ import annotations

import json
import sys

from test_plan_tools import SCRIPTS, pt
from test_routing import dispatch, mech_plan, plan, task


def cx_task(tid, cx, mech=False, mins=3, ctx=15, **kw):
    return task(tid, mech, mins, **kw).replace("· Complexity 3 · Est. context 30k", f"· Complexity {cx} · Est. context {ctx}k", 1)


def equal_task(tid, mins=10, ctx=15):
    """Same features so the predictor gives equal minutes (or stated when features stripped)."""
    body = (
        f"### {tid} task {tid.lower()} · Complexity 2 · Est. context {ctx}k\n"
        f"**Owns:** src/pkg/{tid.lower()}.py, tests/test_{tid.lower()}.py\n"
        f"**Est. time:** {mins} min\n"
        f"**Components:** 1\n**LOC owned:** 100\n**Tests:** fast\n\n"
    )
    return body


def equal_plan(tmp_path, n, mins=10, ctx=15):
    return plan(tmp_path, [equal_task(f"T{i}", mins, ctx) for i in range(1, n + 1)], [])


def test_five_equal_tasks_three_implementers_2_2_1(tmp_path):
    p = equal_plan(tmp_path, 5)
    d, _ = dispatch(tmp_path, p, [], batch=None)
    sizes = sorted(len(k.split(",")) for k in d)
    assert sizes == [1, 2, 2]
    assert all(v.startswith("fresh") for v in d.values())


def test_four_equal_tasks_two_implementers(tmp_path):
    p = equal_plan(tmp_path, 4)
    d, _ = dispatch(tmp_path, p, [], batch=None)
    assert sorted(len(k.split(",")) for k in d) == [2, 2]


def test_tiers_never_share_a_batch(tmp_path):
    d, _ = dispatch(tmp_path, mech_plan(tmp_path, 3), [], batch=None)
    haiku = [k for k, v in d.items() if "tier=haiku" in v]
    sonnet = [k for k, v in d.items() if "tier=sonnet" in v]
    assert haiku and sonnet
    for k in haiku:
        assert "T9" not in k


def test_summed_context_stays_within_the_tier_budget(tmp_path):
    # sonnet max_context_k is 80; two tasks at 50k cannot share
    p = plan(tmp_path, [equal_task(f"T{i}", ctx=50) for i in range(1, 4)], [])
    d, _ = dispatch(tmp_path, p, [], batch=None)
    assert all(len(k.split(",")) == 1 for k in d)


def test_batch_one_turns_it_off(tmp_path):
    p = equal_plan(tmp_path, 5)
    d, _ = dispatch(tmp_path, p, [], batch="1")
    assert sorted(d) == ["T1", "T2", "T3", "T4", "T5"]


def test_critical_path_tasks_run_alone(tmp_path):
    p = plan(tmp_path, [cx_task("T1", 2, mins=6, produces="pkg.t1.f(x: int) -> int"),
                        cx_task("T2", 2, consumes="pkg.t1.f(x: int) -> int"),
                        cx_task("T3", 2, mins=2), cx_task("T4", 2, mins=2)], [("T1", "T2")])
    cp = pt("lint", "--stable", "--plan", p, cwd=tmp_path)
    assert json.loads((tmp_path / ".superlite" / "stable.json").read_text(encoding="utf-8"))["critical_path"][:1] == ["T1"], cp.both
    d, _ = dispatch(tmp_path, p, [], batch=None)
    assert "T1" in d  # alone on the critical path
    assert all("T1," not in k and not k.startswith("T1,") for k in d if k != "T1")


def test_dispatch_json_records_batches(tmp_path):
    p = equal_plan(tmp_path, 5)
    from test_routing import DISPATCH, pool, run
    cp = run(DISPATCH, "--plan", p, "--pool", pool(tmp_path, []), "--ledger", tmp_path / "l.md",
             cwd=tmp_path)
    assert cp.returncode == 0, cp.both
    data = json.loads((tmp_path / ".superlite" / "dispatch.json").read_text(encoding="utf-8"))
    assert sorted(len(b) for b in data["batches"]) == [1, 2, 2]


def test_a_batch_in_flight_is_running_and_released_task_by_task(tmp_path):
    p = equal_plan(tmp_path, 4)
    busy = {"id": "s1", "tier": "sonnet", "tasks": [], "busy": True, "current": "T1,T2"}
    d, _ = dispatch(tmp_path, p, [busy], batch=None)
    assert "T1" not in ",".join(d) and "T2" not in ",".join(d)
    sys.path.insert(0, str(SCRIPTS))
    import checkpoint
    f = tmp_path / "pool.json"
    checkpoint.release_agents(f, ["T1"])
    ag = json.loads(f.read_text(encoding="utf-8"))["agents"][0]
    assert ag["current"] == "T2" and ag["busy"] is True
    checkpoint.release_agents(f, ["T2"])
    ag = json.loads(f.read_text(encoding="utf-8"))["agents"][0]
    assert "current" not in ag and ag["busy"] is False
