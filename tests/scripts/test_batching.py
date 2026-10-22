"""Batching: dispatch groups ready tasks of one tier into one agent message, up to 3, by complexity."""
from __future__ import annotations

import json
import sys

from test_plan_tools import SCRIPTS, pt
from test_routing import dispatch, mech_plan, plan, task


def cx_task(tid, cx, mech=False, mins=3, ctx=15, **kw):
    return task(tid, mech, mins, **kw).replace("· Complexity 3 · Est. context 30k", f"· Complexity {cx} · Est. context {ctx}k", 1)


def small(p):
    """Shrink every task's context estimate to 15k so only complexity limits a batch."""
    p.write_text(p.read_text(encoding="utf-8").replace("Est. context 30k", "Est. context 15k"), encoding="utf-8")
    return p


def lines(d):
    return {k: v.split(" tier")[0] for k, v in d.items()}


def test_three_simple_tasks_share_one_agent(tmp_path):
    p = plan(tmp_path, [cx_task(f"T{i}", 2) for i in range(1, 5)], [])
    d, _ = dispatch(tmp_path, p, [], batch=None)
    assert sorted(d) == ["T1,T2,T3", "T4"]  # 3 per agent, the fourth opens a new batch
    assert d["T1,T2,T3"] == "fresh tier=sonnet agent=superlite:implementer"


def test_batch_size_depends_on_complexity(tmp_path):
    p = plan(tmp_path, [cx_task("T1", 5), cx_task("T2", 4), cx_task("T3", 5), cx_task("T4", 7), cx_task("T5", 2)], [])
    d, _ = dispatch(tmp_path, p, [], batch=None)
    assert "T4" in d  # complex: alone
    assert "T1,T2" in d  # two medium fill a batch (1.5 + 1.5)
    assert "T3,T5" in d  # medium + simple (1.5 + 1)


def test_unknown_complexity_counts_as_medium(tmp_path):
    p = small(plan(tmp_path, [task(f"T{i}", False, 3).replace(" · Complexity 3", "") for i in range(1, 4)], []))
    d, _ = dispatch(tmp_path, p, [], batch=None)
    assert sorted(d) == ["T1,T2", "T3"]


def test_tiers_never_share_a_batch(tmp_path):
    d, _ = dispatch(tmp_path, small(mech_plan(tmp_path, 3)), [], batch=None)
    assert d["T1,T2,T3"].endswith("tier=haiku agent=superlite:implementer-haiku")
    assert d["T9"].startswith("fresh tier=sonnet")


def test_summed_context_stays_within_the_tier_budget(tmp_path):
    p = plan(tmp_path, [cx_task(f"T{i}", 2, ctx=30) for i in range(1, 4)], [])
    d, _ = dispatch(tmp_path, p, [], batch=None)
    assert sorted(d) == ["T1,T2", "T3"]  # 3 x 30k is over sonnet's 80k


def test_batch_one_turns_it_off(tmp_path):
    p = plan(tmp_path, [cx_task(f"T{i}", 2) for i in range(1, 4)], [])
    d, _ = dispatch(tmp_path, p, [], batch="1")
    assert sorted(d) == ["T1", "T2", "T3"]


def test_critical_path_tasks_run_alone(tmp_path):
    p = plan(tmp_path, [cx_task("T1", 2, mins=6, produces="pkg.t1.f(x: int) -> int"),
                        cx_task("T2", 2, consumes="pkg.t1.f(x: int) -> int"),
                        cx_task("T3", 2, mins=2), cx_task("T4", 2, mins=2)], [("T1", "T2")])
    cp = pt("lint", "--stable", "--plan", p, cwd=tmp_path)
    assert json.loads((tmp_path / ".superlite" / "stable.json").read_text(encoding="utf-8"))["critical_path"][:1] == ["T1"], cp.both
    d, _ = dispatch(tmp_path, p, [], batch=None)
    assert "T1" in d and "T3,T4" in d  # T1 unblocks T2, so nothing waits behind it


def test_affinity_groups_tasks_that_share_a_dependency(tmp_path):
    t = [cx_task("T1", 2, produces="pkg.t1.f(x: int) -> int"), cx_task("T2", 2, produces="pkg.t2.f(x: int) -> int")]
    t += [cx_task("T3", 2, consumes="pkg.t1.f(x: int) -> int"), cx_task("T4", 2, consumes="pkg.t2.f(x: int) -> int"),
          cx_task("T5", 2, consumes="pkg.t1.f(x: int) -> int"), cx_task("T6", 2, consumes="pkg.t2.f(x: int) -> int")]
    p = plan(tmp_path, t, [("T1", "T3"), ("T2", "T4"), ("T1", "T5"), ("T2", "T6")])
    (tmp_path / "l.md").write_text("- checkpoint T1 aaaaaaa t1\n- checkpoint T2 bbbbbbb t2\n", encoding="utf-8")
    from test_routing import DISPATCH, pool, run
    cp = run(DISPATCH, "--plan", p, "--pool", pool(tmp_path, []), "--ledger", tmp_path / "l.md", "--batch", "2", cwd=tmp_path)
    assert cp.returncode == 0, cp.both
    got = {ln.split()[0] for ln in cp.out.splitlines()}
    assert got == {"T3,T5", "T4,T6"}  # same producer, same agent


def test_a_batch_in_flight_is_running_and_released_task_by_task(tmp_path):
    p = plan(tmp_path, [cx_task(f"T{i}", 2) for i in range(1, 5)], [])
    busy = {"id": "s1", "tier": "sonnet", "tasks": [], "busy": True, "current": "T1,T2,T3"}
    d, _ = dispatch(tmp_path, p, [busy], batch=None)
    assert list(d) == ["T4"]  # the batch's tasks are running, not ready
    sys.path.insert(0, str(SCRIPTS))
    import checkpoint
    f = tmp_path / "pool.json"
    checkpoint.release_agents(f, ["T1", "T3"])
    ag = json.loads(f.read_text(encoding="utf-8"))["agents"][0]
    assert ag["current"] == "T2" and ag["busy"] is True and ag["tasks"] == ["T1", "T3"]  # still on T2
    checkpoint.release_agents(f, ["T2"])
    ag = json.loads(f.read_text(encoding="utf-8"))["agents"][0]
    assert "current" not in ag and ag["busy"] is False and ag["tasks"] == ["T1", "T3", "T2"]
