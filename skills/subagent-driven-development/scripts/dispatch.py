#!/usr/bin/env python3
"""Ready tasks and who runs each, in one call: next_tasks + assign for every ready task.

    dispatch.py --plan P [--pool .superlite/pool.json] [--ledger .superlite/progress.md]
                [--committed T1,T2] [--running T3] [--cap 10] [--batch N]

stdout, one line per batch of ready tasks (one agent, one message), longest predicted first, within the cap:
    Tn agent=<id>        clear winner (D11 scoring, margin >= 3); that agent is now taken
    Tn,Tm poll <id>,<id> tie or no signal: relevance-poll these idle agents
    Tn fresh             no eligible idle agent: start a primed implementer
    T0 wave0             synthesized Wave 0 skeleton still pending (`sl wave0`): its own agent, first line
stderr: `evict=<id> <reason>` for each idle agent eligible for none of the ready tasks.

Batching: `split.choose` picks how many new implementers and which ready tasks each holds
(exact A* = argmin max(B+A, F/T); ties smaller F then A; busy count; CP alone; context budget).
`--batch 1` turns batching off (one task per agent). Writes `.superlite/dispatch.json` with the batches printed.

Defaults when a flag is omitted: done tasks come from the ledger's `- checkpoint Tn <sha>`
lines (old `- commit` lines too); running tasks are the pool agents' `current` tasks (`Tn` or a batch `Tn,Tm`) not yet
checkpointed (busy or not: a FAILED or BLOCKED task keeps its `current` until `checkpoint` records it). An agent with
a `current` gets no new task, and no agent appears on two lines of one call (`agent=` or
`poll`). The plan is parsed once, in this process. Stdlib only.
"""

# Complexity notation:
# n = tasks in the plan, e = dependency edges, L = plan text size, R = ready tasks returned (<= cap)
# a = agents in the pool, F = files known per agent, o = Owns paths of one task, c = ledger lines
import argparse  # O(1)
import json  # O(1)
import sys  # O(1)
from pathlib import Path  # O(1)

sys.path.insert(0, str(Path(__file__).resolve().parent))  # O(1)
import assign as asg  # noqa: E402 | O(1)
import checkpoint as ct  # noqa: E402 | O(1)
import routing  # noqa: E402 | O(1)
import next_tasks as nt  # noqa: E402 | O(1)


def load_pool(path):  # O(pool bytes)
    p = Path(path)  # O(1)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"agents": []}  # O(pool bytes)


def currents(ag):  # O(len current)
    """The tasks an agent is on: `current` is "Tn", a batch "Tn,Tm", or a list."""
    cur = ag.get("current") or []  # O(1)
    parts = cur if isinstance(cur, list) else str(cur).split(",")  # O(len)
    return [str(x).strip() for x in parts if str(x).strip()]  # O(len)


def running_from_pool(pool, committed=()):  # O(a)
    done = set(committed)  # O(c)
    return [t for ag in pool.get("agents", []) for t in currents(ag) if t not in done]  # O(a + tasks)


def stable_cp(plan):  # O(stable.json)
    """Critical-path task ids that `lint --stable` saved, else none."""
    try:  # O(1)
        import plan_tools as pt  # O(1) lazy
        return set(json.loads((pt.stable_root(plan) / ".superlite" / "stable.json").read_text(encoding="utf-8")).get("critical_path") or [])  # O(file)
    except Exception:  # noqa: BLE001 | no stable.json: no task is held alone for it | O(1)
        return set()  # O(1)


def predicted_minutes(plan, graph, info):  # O(n * predict)
    """Per-task minutes: predictor when features exist, else stated Est. time, else default."""
    try:  # O(1)
        import plan_tools as pt  # O(1)
        class _Args:  # O(1)
            models = coef = None  # O(1)
            stated = False  # O(1)
        times, _ = pt.effective_times(pt.Plan.load(str(plan)), graph, _Args())  # O(n * predict)
        return {t: float(times.get(t) or info.get(t, {}).get("est_time") or nt.DEFAULT_TIME) for t in graph}  # O(n)
    except Exception:  # noqa: BLE001 | fall back to heading times | O(1)
        return {t: (info[t]["est_time"] if t in info and info[t]["est_time"] is not None  # O(n)
                    else nt.DEFAULT_TIME) for t in graph}


def busy_left_tenths(pool, times, committed=()):  # O(a * tasks)
    """Remaining minutes (tenths) for each busy implementer, from its unfinished current tasks."""
    import split as sp  # O(1)
    done = set(committed)  # O(c)
    out = []  # O(1)
    for ag in pool.get("agents", []):  # O(a)
        cur = [t for t in currents(ag) if t not in done]  # O(tasks)
        if not cur and not (ag.get("busy") or ag.get("status") == "busy"):  # O(1)
            continue  # O(1)
        if not cur:  # O(1)
            continue  # O(1)
        out.append(sp.tenths(sum(times.get(t, nt.DEFAULT_TIME) for t in cur)))  # O(tasks)
    return out  # O(a)


def make_batches(ready, tier, info, times, max_ctx, cp=(), busy_left=(), solo_mode=False):  # O(tiers * packing)
    """[[task ids]] from split.choose per tier. `--batch 1` (solo_mode) returns one task per batch."""
    import split as sp  # O(1)
    if solo_mode or not ready:  # O(1)
        return [[t] for t in ready]  # O(R)
    cp = set(cp)  # O(|cp|)
    by_tier: dict[str, list] = {}  # O(1)
    for t in ready:  # O(R)
        by_tier.setdefault(tier(t), []).append(t)  # O(1)
    batches = []  # O(1)
    for tr, ids in by_tier.items():  # O(tiers)
        ms = [sp.tenths(times.get(t, nt.DEFAULT_TIME)) for t in ids]  # O(R)
        ctx = [int(info.get(t, {}).get("est_context") or asg.DEFAULT_CONTEXT_K) for t in ids]  # O(R)
        ix = {i for i, t in enumerate(ids) if t in cp}  # O(R)
        cap = float(max_ctx(tr))  # O(1)
        r = sp.choose(ms, tuple(busy_left), ctx, cap, ix)  # O(packing)
        if r is None:  # O(1)
            batches.extend([[t] for t in ids])  # O(R)
            continue  # O(1)
        for group in r[3]:  # O(A)
            batches.append([ids[i] for i in group])  # O(|group|)
    # longest batch first (sum of minutes), then ready order of the seed
    order = {t: k for k, t in enumerate(ready)}  # O(R)
    batches.sort(key=lambda g: (-sum(times.get(t, nt.DEFAULT_TIME) for t in g), order[g[0]]))  # O(R log R)
    return batches  # O(R)


def write_dispatch_json(plan, lines):  # O(R)
    """Record the batches this call printed so the batch guard can match them."""
    try:  # O(1)
        import plan_tools as pt  # O(1)
        root = pt.stable_root(plan) / ".superlite"  # O(1)
        root.mkdir(parents=True, exist_ok=True)  # O(1)
        # Include T0 when printed (wave0 line): guard matches it as its own batch. T0 is never
        # packed by split.choose; it only appears here if dispatch prepended the wave0 line.
        batches = [t.split(",") for t, _ in lines]  # O(R)
        (root / "dispatch.json").write_text(  # O(R)
            json.dumps({"batches": batches, "lines": [f"{t} {d}" for t, d in lines]}, indent=1) + "\n",
            encoding="utf-8")
    except Exception:  # noqa: BLE001 | never break dispatch on a write miss | O(1)
        pass  # O(1)


def is_idle(ag):  # O(1)
    return not (ag.get("busy") or ag.get("status") == "busy" or ag.get("current"))  # O(1)


def plan_dispatch(plan, pool, committed, running, cap, tiers=None, cfg=None, lane_virtual=0, batch=None):  # O(L + n + e + packing + R*a*(F + o))
    """[(tasks, decision)] and {agent id: evict reason}; tasks is "Tn" or a batch "Tn,Tm"; decision is 'agent=ID tier=T',
    'poll IDS tier=T' or 'fresh tier=T agent=TYPE'. A batch only goes to an idle agent of its own tier; the lane tier
    (haiku) reuses an idle eligible lane agent (lowest context, then fewest tasks) with no poll, else starts a fresh
    one. `batch=1` forces one task per agent; otherwise split.choose picks the batches."""
    cfg = cfg or routing.load_cfg()  # O(file)
    graph = nt.taskgraph(plan)  # O(L + n + e), memoized
    info = nt.parse_plan(plan)  # O(L)
    if tiers is None:  # O(1)
        try:  # O(1)
            tiers = routing.read_tiers(plan, cfg)  # O(stable.json) or a computation
        except Exception:  # no tiers: everything is default-tier | O(1)
            tiers = {}  # O(1)
    default = cfg.get("default_tier", "sonnet")  # O(1)
    tier = lambda t: tiers.get(t, default)  # noqa: E731 | O(1)
    times = predicted_minutes(plan, graph, info)  # O(n * predict)
    ready_ids = nt.ready_tasks(graph, times, committed, running, cap,  # O(n*(n + e) + R log R) draft closure
                               nt.taskgraph_full(plan).get("status", {}),  # O(1) memoized
                               nt.stable_filter(plan))  # only stable tasks when .superlite/stable.json exists
    solo = batch == 1  # O(1)
    cp = stable_cp(plan) if (cfg.get("batch") or {}).get("critical_path_solo", True) else set()  # O(file)
    groups = make_batches(ready_ids, tier, info, times,  # O(packing)
                          lambda tr: float(routing.tier_limits(cfg, tr)["max_context_k"]),
                          cp, busy_left_tenths(pool, times, committed), solo)
    members = {",".join(g): g for g in groups}  # batch key -> its tasks | O(R)
    ready = list(members)  # O(R) batches, longest first (a batch is led by its longest task)
    tier_of_task = tier  # O(1)
    tier = lambda u: tier_of_task(members[u][0] if u in members else u)  # noqa: E731 | a batch has one tier | O(1)
    lane = cfg.get("lane") or {}  # O(1)
    lane_tier = lane.get("tier")  # O(1)
    agents = pool.get("agents", [])  # O(a)
    idle = [ag for ag in agents if is_idle(ag)]  # O(a)
    eligible = {}  # task -> {agent id: score} over eligible idle agents of the task's own tier | O(1)
    eligible_somewhere = set()  # O(1)
    reasons = {}  # O(1)
    for t in ready:  # O(R) iterations, one per batch
        ms = members[t]  # O(1)
        deps = sorted({d for m in ms for d in graph.get(m, [])})  # O(d)
        owns = [o for m in ms for o in info.get(m, {}).get("owns", [])]  # O(o)
        est_context = sum(info.get(m, {}).get("est_context") or asg.DEFAULT_CONTEXT_K for m in ms)  # O(1)
        limits = routing.tier_limits(cfg, tier(t))  # O(1)
        eligible[t] = {}  # O(1)
        for ag in idle:  # O(a) iterations
            if routing.agent_tier(ag, cfg) != tier(t):  # strict: never across tiers | O(1)
                continue  # O(1)
            why = asg.eligibility(ag, est_context, limits)  # O(1)
            if why:  # O(1)
                reasons.setdefault(ag["id"], why)  # O(1)
                continue  # O(1)
            eligible_somewhere.add(ag["id"])  # O(1)
            eligible[t][ag["id"]] = asg.score(ag, t, deps, owns, info)  # O(F + o*F)

    def rank(t):  # resolve signal first: clear winners, then polls with signal, then none | O(a log a)
        d, top = asg.decide(eligible[t]), max(eligible[t].values(), default=0)  # O(a log a)
        return 0 if d.startswith("agent=") else (1 if top > 0 else 2)  # O(1)

    order = {t: i for i, t in enumerate(ready)}  # O(R)
    taken = set()  # agents on an earlier resolved line (agent= or poll) | O(1)
    decided = {}  # O(1)
    in_lane = [t for t in ready if lane_tier and tier(t) == lane_tier]  # O(R)
    ctx = {ag["id"]: float(ag.get("context_k", 0)) for ag in agents}  # lowest context first | O(a)
    ntasks = {ag["id"]: len(ag.get("tasks", [])) for ag in agents}  # then fewest tasks done | O(a)
    for t in sorted((x for x in ready if x not in in_lane), key=lambda x: (rank(x), order[x])):  # O(R log R + R*a log a)
        scores = {k: v for k, v in eligible[t].items() if k not in taken}  # O(a)
        decision = asg.decide(scores)  # O(a log a)
        if decision.startswith("agent="):  # O(1)
            taken.add(decision.split("=", 1)[1])  # O(1)
        elif decision == "poll":  # polled agents are reserved too: a poll winner has one task | O(1)
            taken.update(scores)  # O(a)
            decision = "poll " + ",".join(scores)  # O(a)
        decided[t] = decision  # O(1)
    for t in in_lane:  # the lane: reuse an idle eligible member, no poll; fresh only when none is left | O(R*a)
        free = sorted((k for k in eligible[t] if k not in taken), key=lambda k: (ctx.get(k, 0), ntasks.get(k, 0), k))  # O(a log a)
        if free:  # O(1)
            decided[t] = "agent=" + free[0]  # O(1)
            taken.add(free[0])  # O(1)
        else:  # nothing waits: a fresh lane agent starts this round | O(1)
            decided[t] = "fresh"  # O(1)
    out = []  # O(1)
    for t in ready:  # O(R) lines stay longest first
        if t not in decided:  # waits for a lane agent | O(1)
            continue  # O(1)
        d = decided[t]  # O(1)
        d += f" tier={tier(t)}" + (f" agent={routing.tier_agent(cfg, tier(t))}" if d == "fresh" else "")  # O(1)
        out.append((t, d))  # O(1)
    evict = {k: v for k, v in reasons.items() if k not in eligible_somewhere}  # O(a)
    return out, evict  # O(1)


def render(lines, evict, swarm=None):  # O(R + a)
    for aid, why in evict.items():  # O(a) iterations
        print(f"evict={aid} {why}", file=sys.stderr)  # O(1)
    for t, decision in lines:  # O(R) iterations
        print(f"{t} {decision}")  # O(1)
    if swarm:  # O(S)
        for row in swarm:  # O(S)
            print(row)  # O(1)


def run(plan, pool_path, ledger, committed=None, running=None, cap=10, batch=None):  # O(c + L + n + e + R*a*(F + o))
    """Library entry for checkpoint --dispatch; flags left as None take their defaults."""
    pool = load_pool(pool_path)  # O(pool bytes)
    done = nt.csv(committed) if committed is not None else sorted(ct.checkpointed_tasks(ledger))  # O(c)
    busy = nt.csv(running) if running is not None else running_from_pool(pool, done)  # O(a)
    import wave0  # noqa: E402 | O(1) lazy
    w0 = wave0.synth_info(plan) and "T0" not in done and "T0" not in busy  # synthesized Wave 0 still pending | O(1)
    lines, evict = plan_dispatch(plan, pool, done, busy, cap, lane_virtual=1 if w0 else 0, batch=batch)  # see plan_dispatch
    if w0:  # first line, a haiku lane agent | O(R)
        cfg = routing.load_cfg()  # O(file)
        lt = (cfg.get("lane") or {}).get("tier", "haiku")  # O(1)
        lines = [("T0", f"wave0 tier={lt} agent={routing.tier_agent(cfg, lt)}")] + lines  # O(R)
    return lines, evict  # O(R)


def main(argv=None):  # O(c + L + n + e + R*a*(F + o))
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])  # O(1)
    ap.add_argument("--plan")  # O(1)
    ap.add_argument("--pool", default=".superlite/pool.json")  # O(1)
    ap.add_argument("--ledger", default=ct.DEFAULT_LEDGER)  # O(1)
    ap.add_argument("--committed", help="default: the ledger's checkpoint lines")  # O(1)
    ap.add_argument("--running", help="default: `current` of each busy pool agent")  # O(1)
    ap.add_argument("--cap", type=int, default=None, help="default: superlite.json cap, else 10")  # O(1)
    ap.add_argument("--batch", type=int, help="most tasks per agent message (default: config batch.max_tasks; 1 = off)")  # O(1)
    a = ap.parse_args(argv)  # O(1)
    import project_config  # O(1)
    try:  # bad superlite.json fails closed before any dispatch | O(1)
        cap = project_config.effective_cap(a.cap, Path(".").resolve())  # O(file)
    except project_config.ProjectConfigError as exc:  # O(1)
        print(str(exc), file=sys.stderr)  # O(1)
        return 2  # O(1)
    plan = a.plan or load_pool(a.pool).get("plan") or nt.DEFAULT_PLAN  # O(pool bytes)
    import orchestrator  # O(1)
    blocked = orchestrator.require(plan, "dispatch")  # human mode waits for approval | O(file)
    if blocked:  # O(1)
        return blocked  # O(1)
    lines, evict = run(plan, a.pool, a.ledger, a.committed, a.running, cap, a.batch)  # see run
    swarm = None  # O(1)
    try:  # Cursor (or other) host: additive SWARM block after the usual lines | O(H)
        import host_models as hm  # O(1)
        host = hm.detect_host(plan)  # Execution header or SUPERLITE_HOST | O(L)
        if host:  # O(1)
            resolved = hm.resolve(host, plan)  # O(H) keeps existing host-models.json unless first time
            text = [f"{t} {d}" for t, d in lines]  # O(R)
            swarm = hm.swarm_lines(text, resolved)  # O(R)
    except Exception:  # noqa: BLE001 | host optional; never break dispatch | O(1)
        swarm = None  # O(1)
    write_dispatch_json(plan, lines)  # O(R)
    render(lines, evict, swarm)  # see render
    try:  # the budget line when it changed (needs .superlite/metrics.jsonl) | O(compute)
        import budget  # O(1)
        budget.maybe_print(plan, str(Path(a.pool).parent))  # O(compute)
    except Exception:  # noqa: BLE001 | O(1)
        pass  # O(1)
    return 0  # O(1)


if __name__ == "__main__":  # O(1)
    sys.exit(main())  # O(1)
