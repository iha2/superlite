"""Batching: dispatch.choose picks the implementer count and batches; the brute-force oracle checks it."""
from __future__ import annotations

import itertools
import json
import random
import sys
import time

from test_plan_tools import SCRIPTS, pt
from test_routing import dispatch, mech_plan, plan, task

sys.path.insert(0, str(SCRIPTS))
import dispatch as dp  # noqa: E402
import next_tasks as nt  # noqa: E402


def sizes(batches):
    return sorted(len(b) for b in batches)


def brute(ms, busy=(), ctx=None, cap=float("inf"), cp=()):
    """Reference oracle: try every non-empty assignment of n tasks onto A labels."""
    n = len(ms)
    ctx = list(ctx) if ctx is not None else [0] * n
    busy = tuple(int(b) for b in busy if b)
    B, T = len(busy), max(list(ms) + list(busy) or [1]) or 1
    best = None
    for A in range(1, n + 1):
        Fbest = None
        for assign in itertools.product(range(A), repeat=n):
            if len(set(assign)) != A:
                continue
            bins = [[i for i in range(n) if assign[i] == j] for j in range(A)]
            if any(sum(ctx[i] for i in b) > cap for b in bins):
                continue
            if any(i in cp and len(b) > 1 for b in bins for i in b):
                continue
            F = max([sum(ms[i] for i in b) for b in bins] + list(busy))
            Fbest = F if Fbest is None else min(Fbest, F)
        if Fbest is None:
            continue
        score = max(B + A, Fbest / T)
        if best is None or (score, Fbest, A) < (best[0], best[1], best[2]):
            best = (score, Fbest, A)
    return best


def test_five_equal_tasks_split_2_2_1():
    r = dp.choose([100] * 5)  # 10.0 min each in tenths
    assert r is not None
    assert r[2] == 3
    assert sizes(r[3]) == [1, 2, 2]
    assert r[1] == 200  # finish 20.0 min


def test_scenario_table_rows():
    # minutes as tenths; busy left as tenths: (ms, busy, implementers, batch sizes, finish)
    rows = [
        ([100] * 4, (), 2, [2, 2], 200),
        ([100] * 5, (), 3, [1, 2, 2], 200),
        ([60] * 16, (), 4, [4, 4, 4, 4], 240),
        ([60] * 17, (), 5, [3, 3, 3, 4, 4], 240),
        ([60] * 20, (), 5, [4, 4, 4, 4, 4], 240),
        ([60] * 21, (), 5, [4, 4, 4, 4, 5], 300),
        ([60] * 25, (), 5, [5, 5, 5, 5, 5], 300),
        ([60] * 26, (), 6, [4, 4, 4, 4, 5, 5], 300),
        ([60] * 100, (), 10, [10] * 10, 600),
        ([100, 40, 30, 30], (), 2, [1, 3], 100),
        ([100, 60, 60], (), 2, [1, 2], 120),
        ([300, 100, 100, 100, 80, 60, 40, 20], (), 2, [3, 5], 400),
        ([300] + [20] * 12, (), 1, [13], 540),
        ([300] + [20] * 20, (), 2, [4, 17], 360),
        ([100] + [10] * 8, (), 1, [9], 180),
        ([40, 20, 20], (), 2, [1, 2], 40),
        ([60] * 3, (200,), 1, [3], 200),
        ([60] * 3, (200, 150, 120, 80), 1, [3], 200),
        ([30] * 10, (300,), 1, [10], 300),
        ([60] * 4, (120, 50), 1, [4], 240),
    ]
    for ms, busy, A, want, F in rows:
        r = dp.choose(ms, busy)
        assert r is not None, (ms, busy)
        assert (r[2], sizes(r[3]), r[1]) == (A, sorted(want), F), (ms, busy)
        assert all(b for b in r[3])


def test_critical_path_alone():
    r = dp.choose([40, 20, 20], (), [0, 0, 0], float("inf"), cp={0})
    assert r[2] == 2 and [[0]] == [b for b in r[3] if 0 in b] and sizes(r[3]) == [1, 2]


def test_context_budget_forces_extra_implementer():
    r = dp.choose([10, 10], (), [60, 60], cap=100)  # minutes fit on one, context does not
    assert r[2] == 2 and sizes(r[3]) == [1, 1]


def test_brute_force_agreement_300():
    random.seed(7)
    for _ in range(300):
        n = random.randint(1, 6)
        ms = [random.choice([10, 20, 30, 40, 50, 60, 80, 100]) for _ in range(n)]
        busy = tuple(b for b in (random.choice([0, 50, 120, 200]) for _ in range(random.randint(0, 2))) if b)
        ctx = [random.choice([10, 20, 30, 40]) for _ in range(n)]
        cap = random.choice([float("inf"), 80, 60])
        cp = set(random.sample(range(n), random.randint(0, min(1, n))))
        e, b = dp.choose(ms, busy, ctx, cap, cp), brute(ms, busy, ctx, cap, cp)
        assert (e is None) == (b is None), (ms, busy, ctx, cap, cp)
        if e:
            assert (abs(e[0] - b[0]), abs(e[1] - b[1]), e[2]) == (0, 0, b[2]), (ms, busy, ctx, cap, cp)
            assert sorted(i for g in e[3] for i in g) == list(range(n)) and all(e[3])


def test_sixty_tasks_under_one_second():
    random.seed(3)
    t0 = time.perf_counter()
    r = dp.choose([random.randint(15, 60) for _ in range(60)])
    assert time.perf_counter() - t0 < 1.0
    assert r is not None and all(r[3])


def test_tenths_helper():
    assert (dp.tenths(2.354), dp.tenths(2.35), dp.tenths(1.0)) == (24, 24, 10)


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


def test_five_equal_tasks_dispatch_2_2_1(tmp_path):
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


def test_never_busy_agent(tmp_path):
    p = equal_plan(tmp_path, 3)
    owner = {"id": "own", "tier": "sonnet", "tasks": [], "files": ["src/pkg/t1.py"], "busy": True, "current": "T9"}
    flagged = {"id": "flag", "tier": "sonnet", "tasks": [], "files": ["src/pkg/t1.py"], "status": "busy"}
    idle = {"id": "idle", "tier": "sonnet", "tasks": [], "files": []}
    d, err = dispatch(tmp_path, p, [owner, flagged, idle])
    assert "own" not in " ".join(d.values()) and "flag" not in " ".join(d.values())
    assert "evict=own" not in err and "evict=flag" not in err
    assert [v for v in d.values() if "idle" in v] and all("tier=sonnet" in v for v in d.values())


def test_never_crosses_tiers(tmp_path):
    only_haiku = {"id": "h", "tier": "haiku", "tasks": []}
    d, _ = dispatch(tmp_path, equal_plan(tmp_path, 2), [only_haiku])
    assert all(v.startswith("fresh tier=sonnet") for v in d.values())


def test_batch_cap_is_the_tier_context_budget():
    tier = lambda t: "sonnet"  # noqa: E731
    info = {t: {"est_context": 50} for t in ("T1", "T2", "T3")}
    out = dp.make_batches(["T1", "T2", "T3"], tier, info, {}, lambda tr: 80)
    assert all(len(g) == 1 for g in out)


def test_reuse_limits_come_from_the_tier_and_fix_reuse_defaults():
    cfg = {"tiers": {"sonnet": {"max_tasks": 9, "max_context_k": 70, "max_idle_min": 30},
                     "haiku": {"max_tasks": 5, "max_context_k": 60, "max_idle_min": 40,
                               "fix_reuse": {"max_tasks": 2, "max_context_k": 25}}}}
    assert dp.reuse_limits(cfg, "sonnet")["max_tasks"] == 9
    assert dp.reuse_limits(cfg, "sonnet", fix=True) == {"max_tasks": 3, "max_context_k": 40, "max_idle_min": 30}
    assert dp.reuse_limits(cfg, "haiku", fix=True)["max_tasks"] == 2
    assert dp.eligibility({"tasks": ["a", "b"], "context_k": 0}, 10, dp.reuse_limits(cfg, "haiku", fix=True)) == "2 tasks done"


GRAPH_PLAN = """# Plan
**Goal:** g
**Architecture:** a
**Tech stack:** python
**Spec:** none

```mermaid
flowchart LR
  %% taskgraph
  T1 --> T2
  T1 --> T3
```

### T1 producer · Complexity 2 · Est. context 10k
**Owns:** a.py
**Produces:** a.f(x: int) -> int
**Produces:** a.g(x: int) -> int
e.g.: a.f(1) -> 2

### T2 consumer with an example · Complexity 2 · Est. context 10k
**Owns:** b.py
**Consumes:** a.f(x: int) -> int (T1)

### T3 consumer without an example · Complexity 2 · Est. context 10k
**Owns:** c.py
**Consumes:** a.g(x: int) -> int (T1)
"""


def test_ready_respects_waited_edges(tmp_path):
    f = tmp_path / "plan.md"
    f.write_text(GRAPH_PLAN, encoding="utf-8")
    graph = nt.taskgraph(str(f))
    assert graph == {"T1": [], "T2": [], "T3": ["T1"]}  # T2 has an example for every interface it takes from T1
    assert nt.ready_tasks(graph, {}, [], [], 10, {}, None) == ["T1", "T2"]
    assert nt.ready_tasks(graph, {}, ["T1"], [], 10, {}, None) == ["T2", "T3"]
    assert sorted(nt.taskgraph_full(str(f))["seams"]) == ["T1↔T2", "T1↔T3"]  # seams still list every edge


def test_predictor_check_value():
    # 1 component, 120 loc, slow, Sonnet -> 2.354 min before scale; the shipped predictor.json scales by 0.9.
    from pathlib import Path

    import predict

    coef = predict.load_coef(Path(SCRIPTS).parents[2] / "skills" / "swarm" / "config" / "predictor.json")
    feats = {"components": 1, "loc": 120, "tests": "slow"}
    raw = 1.0813 + 0.405 + 0.6068 + 0.002178 * 120
    assert abs(raw - 2.354) < 1e-3
    assert abs(predict.time_from(feats, "sonnet", predict.load_models(), coef) - raw * 0.9) < 1e-3
