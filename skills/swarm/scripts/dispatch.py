#!/usr/bin/env python3
"""Ready tasks and who runs each, in one call.

    dispatch.py [--plan P] [--pool .superlite/pool.json] [--ledger .superlite/progress.md]
                [--checkpointed T1,T2] [--running T3] [--cap 10] [--batch N]
    dispatch.py ready  [next_tasks flags]       the ready task ids, longest predicted first
    dispatch.py assign --task Tn [--pool P]     agent=<id> | poll | fresh for one task

stdout, one line per batch of ready tasks (one agent, one message), longest predicted first, within the cap:
    Tn agent=<id>        clear winner (score margin >= 3); that agent is now taken
    Tn,Tm poll <id>,<id> tie or no signal: relevance-poll these idle agents
    Tn fresh             no eligible idle agent: start a primed implementer
    T0 wave0             synthesized Wave 0 skeleton still pending (`superlite-cli wave0`): its own agent, first line
stderr: `evict=<id> <reason>` for each idle agent eligible for none of the ready tasks.

Scoring: +5 if the agent built a task this one depends on, +3 per owned or read file the task touches, +1 for the
same package. A winner needs a margin of at least 3. An agent is eligible when its context plus the task's Est. context
is within the tier's max_context_k, it has done fewer than max_tasks tasks and idled at most max_idle_min (all from the
tier config). Busy agents are never candidates, and an agent never takes a task of another tier.

Batching: choose() picks how many new implementers and which ready tasks each holds
(exact A* = argmin max(B+A, F/T); ties smaller F then A; busy count; CP alone; context budget).
`--batch 1` turns batching off (one task per agent). Writes `.superlite/dispatch.json` with the batches printed.

Defaults when a flag is omitted: done tasks come from the ledger's `- checkpoint Tn <sha>` lines; running tasks are the
pool agents' `current` tasks (`Tn` or a batch `Tn,Tm`) not yet checkpointed (a FAILED or BLOCKED task keeps its
`current` until `checkpoint` records it). An agent with a `current` gets no new task, and no agent appears on two
lines of one call. Stdlib only.
"""

import argparse
import json
import posixpath
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402
import checkpoint as ct  # noqa: E402
import next_tasks as nt  # noqa: E402
import routing  # noqa: E402
import state  # noqa: E402

MARGIN = 3
DEFAULT_CONTEXT_K = 20.0
FIX_REUSE = {"max_tasks": 3, "max_context_k": 40}  # today's fix-work reuse limits, when the tier has no `fix_reuse`


# ------------------------------------------------------------------ batching: the exact split

def tenths(minutes):  # O(1)
    """Nearest tenth of a minute as an int (6 seconds)."""
    return int(round(float(minutes) * 10))


def lpt(ms, ctx, A, cap):  # O(n log n + n*A)
    """Longest-first greedy onto A bins within the context cap: (max load, bins) or None."""
    loads, cl, bins = [0] * A, [0] * A, [[] for _ in range(A)]
    for i in sorted(range(len(ms)), key=lambda i: -ms[i]):
        ok = [j for j in range(A) if cl[j] + ctx[i] <= cap]
        if not ok:
            return None
        j = min(ok, key=lambda j: loads[j])
        loads[j] += ms[i]
        cl[j] += ctx[i]
        bins[j].append(i)
    return max(loads, default=0), bins


def fits(items, A, cap, ccap, memo):  # O(exponential worst; memoized)
    """Pack items into A bins of at most cap minutes and ccap context, or None.

    items: (minutes, context, id) longest first. The longest opens a bin; every
    fullest-first completion is tried. Identical twins are excluded together.
    """
    if not items:
        return []
    if A == 0:
        return None
    total = sum(m for m, _, _ in items)
    if total > A * cap or any(m > cap or c > ccap for m, c, _ in items):
        return None
    if A >= len(items):
        return [[i] for _, _, i in items]
    if A == 1:
        return [[i for _, _, i in items]] if sum(c for _, c, _ in items) <= ccap else None
    key = (A, tuple((m, c) for m, c, _ in items))
    if key in memo:
        return None
    (m0, c0, i0), rest = items[0], items[1:]
    need = total - (A - 1) * cap
    suffix = [0] * (len(rest) + 1)
    for k in range(len(rest) - 1, -1, -1):
        suffix[k] = suffix[k + 1] + rest[k][0]
    chosen: list = []
    chosen_set: set = set()

    def complete(pos, load, cload):  # O(2^n)
        if load + suffix[pos] < need:
            return None
        if pos == len(rest):
            left = [it for k, it in enumerate(rest) if k not in chosen_set]
            sub = fits(left, A - 1, cap, ccap, memo)  # recurse
            return None if sub is None else [[i0] + [rest[k][2] for k in chosen]] + sub
        m, c, _ = rest[pos]
        if load + m <= cap and cload + c <= ccap:
            chosen.append(pos)
            chosen_set.add(pos)
            r = complete(pos + 1, load + m, cload + c)
            chosen.pop()
            chosen_set.discard(pos)
            if r is not None:
                return r
        nxt = pos + 1
        while nxt < len(rest) and rest[nxt][:2] == rest[pos][:2]:
            nxt += 1
        return complete(nxt, load, cload)

    r = complete(0, m0, c0) if c0 <= ccap and m0 <= cap else None
    if r is None:
        memo.add(key)
    return r


def exact_split(ms, ctx, A, cap):  # O(log(sum) * packing)
    """Exact minimum makespan (tenths) and batches for A implementers, or None."""
    n = len(ms)
    if n == 0:
        return 0, [[] for _ in range(A)]
    if A <= 0:
        return None
    items = sorted(((ms[i], ctx[i], i) for i in range(n)), key=lambda t: (-t[0], -t[1]))
    srt = [t[0] for t in items]
    lo = max(srt[0], -(-sum(srt) // A), srt[A - 1] + srt[A] if A < n else 0)
    start = lpt(ms, ctx, A, cap)
    hi, best = (int(start[0]), start[1]) if start else (sum(srt), None)
    if best is not None and hi <= lo:
        return hi, best
    if best is None:
        best = fits(items, A, hi, cap, set())
        if best is None:
            return None
    while lo < hi:
        mid = (lo + hi) // 2
        got = fits(items, A, mid, cap, set())
        if got is None:
            lo = mid + 1
        else:
            hi, best = mid, got
    return hi, best


def choose(ms, busy=(), ctx=None, cap=float("inf"), cp=()):  # O(A * packing)
    """Pick A* and batches. ms/busy/ctx in whole tenths; cap is the context budget.

    Score is max(B + A, F/T). Returns (score, F, A, batches) where batches are
    task indices into ms, or None when nothing fits. No batch is empty.
    """
    n = len(ms)
    if n == 0:
        return None
    ctx = list(ctx) if ctx is not None else [0] * n
    busy = tuple(int(b) for b in busy if b)
    B = len(busy)
    T = max(list(ms) + list(busy)) or 1
    solo = [i for i in range(n) if i in cp]
    rest = [i for i in range(n) if i not in cp]
    rm, rc = [ms[i] for i in rest], [ctx[i] for i in rest]
    fixed = max([ms[i] for i in solo] + list(busy), default=0)
    counts = [A for A in range(max(1, len(solo)), n + 1) if not (rest and A - len(solo) == 0)]
    upper = {}
    for A in counts:
        k = A - len(solo)
        r = lpt(rm, rc, k, cap) if k else (0, [])
        if r is not None:
            upper[A] = max(B + A, max(r[0], fixed) / T)
    if not upper:
        return None
    U = min(upper.values())

    def lower_score(A):  # O(n log n)
        k = A - len(solo)
        lb = 0
        if rm:
            srt = sorted(rm, reverse=True)
            lb = max(srt[0], -(-sum(rm) // k), srt[k - 1] + srt[k] if k < len(rm) else 0)
        return max(B + A, max(lb, fixed) / T)

    best = None
    for A in counts:
        if lower_score(A) > U + 1e-12:
            continue
        k = A - len(solo)
        r = (0, []) if k == 0 else exact_split(rm, rc, k, cap)
        if r is None:
            continue
        F = max(r[0], fixed)
        score = max(B + A, F / T)
        groups = [[rest[i] for i in b] for b in r[1] if b] + [[i] for i in solo]
        if best is None or (score, F, A) < (best[0], best[1], best[2]):
            best = (score, F, A, [g for g in groups if g])
    return best


# ------------------------------------------------------------------ assignment: who takes a task

def package(path):  # O(len path)
    return posixpath.dirname(path.replace("\\", "/"))


def reuse_limits(cfg, tier, fix=False):  # O(1)
    """The tier's reuse limits (max_tasks, max_context_k, max_idle_min). With `fix`, fix work uses the tier's
    `fix_reuse` {max_tasks, max_context_k} when present, else 3 tasks and 40k."""
    lim = dict(routing.tier_limits(cfg, tier))
    if fix:
        fr = (cfg.get("tiers", {}).get(tier) or {}).get("fix_reuse") or FIX_REUSE
        lim.update({k: fr.get(k, FIX_REUSE[k]) for k in FIX_REUSE})
    return lim


def eligibility(agent, est_context, limits):  # O(1)
    """None when the agent may take work, else a short reason."""
    max_ctx = float(limits["max_context_k"])
    max_idle = float(limits["max_idle_min"])
    max_tasks = int(limits["max_tasks"])
    if float(agent.get("context_k", 0)) + est_context > max_ctx:
        return f"context over {max_ctx:g}k"
    if len(agent.get("tasks", [])) >= max_tasks:
        return f"{max_tasks} tasks done"
    if float(agent.get("idle_min", 0)) > max_idle:
        return f"idle over {max_idle:g} min"
    return None


def score(agent, deps, owns, info):  # O(t + d + o*F*len)
    s = 0
    built = set(agent.get("tasks", []))
    if built & set(deps):
        s += 5
    files = {f.replace("\\", "/") for f in agent.get("files", [])}
    for t in built:
        files.update(info.get(t, {}).get("owns", []))
    s += 3 * sum(1 for o in owns if any(nt.owns_match(f, [o]) for f in files))
    pkgs = {package(f) for f in files if package(f)}
    if any(package(o) in pkgs for o in owns if package(o)):
        s += 1
    return s


def decide(scores):  # O(a log a)
    """scores: {id: score} of eligible agents -> 'agent=<id>' | 'poll' | 'fresh'."""
    if not scores:
        return "fresh"
    ranked = sorted(scores.values(), reverse=True)
    top, second = ranked[0], (ranked[1] if len(ranked) > 1 else 0)
    if top > 0 and top - second >= MARGIN:
        return f"agent={[k for k, v in scores.items() if v == top][0]}"
    return "poll"


# ------------------------------------------------------------------ pool and plan inputs

def load_pool(path):  # O(pool bytes)
    """The pool, or an empty one when absent; a pool that exists but is not valid json raises (callers report it)."""
    p = Path(path)
    if not p.exists():
        return {"agents": []}
    return json.loads(p.read_text(encoding="utf-8"))


def currents(ag):  # O(len current)
    """The tasks an agent is on: `current` is "Tn", a batch "Tn,Tm", or a list."""
    cur = ag.get("current") or []
    parts = cur if isinstance(cur, list) else str(cur).split(",")
    return [str(x).strip() for x in parts if str(x).strip()]


def running_from_pool(pool, checkpointed=()):  # O(a)
    done = set(checkpointed)
    return [t for ag in pool.get("agents", []) for t in currents(ag) if t not in done]


def is_idle(ag):  # O(1)
    return not (ag.get("busy") or ag.get("status") == "busy" or ag.get("current"))


def stable_cp(plan):  # O(stable.json)
    """Critical-path task ids that `lint --stable` saved, else none."""
    data = base.read_json(state.path(nt.stable_root(plan), "stable.json"), {})
    return set(data.get("critical_path") or []) if isinstance(data, dict) else set()


def predicted_minutes(plan, graph, info):  # O(n * predict)
    """Per-task minutes: predictor when features exist, else stated Est. time, else default."""
    stated = {t: (info[t]["est_time"] if t in info and info[t]["est_time"] is not None else nt.DEFAULT_TIME)
              for t in graph}
    try:
        import plan_tools as pt

        class _Args:
            models = coef = None
            stated = False
        times, _ = pt.effective_times(pt.Plan.load(str(plan)), graph, _Args())
    except base.advisory_errors() as e:  # the heading times still dispatch
        print(f"predictor unavailable, using stated times: {e}", file=sys.stderr)
        return stated
    return {t: float(times.get(t) or stated[t]) for t in graph}


def busy_left_tenths(pool, times, checkpointed=()):  # O(a * tasks)
    """Remaining minutes (tenths) for each busy implementer, from its unfinished current tasks."""
    done = set(checkpointed)
    out = []
    for ag in pool.get("agents", []):
        cur = [t for t in currents(ag) if t not in done]
        if cur:
            out.append(tenths(sum(times.get(t, nt.DEFAULT_TIME) for t in cur)))
    return out


def make_batches(ready, tier, info, times, max_ctx, cp=(), busy_left=(), solo_mode=False):  # O(tiers * packing)
    """[[task ids]] from choose() per tier. `--batch 1` (solo_mode) returns one task per batch."""
    if solo_mode or not ready:
        return [[t] for t in ready]
    cp = set(cp)
    by_tier: dict = {}
    for t in ready:
        by_tier.setdefault(tier(t), []).append(t)
    batches = []
    for tr, ids in by_tier.items():
        ms = [tenths(times.get(t, nt.DEFAULT_TIME)) for t in ids]
        ctx = [int(info.get(t, {}).get("est_context") or DEFAULT_CONTEXT_K) for t in ids]
        r = choose(ms, tuple(busy_left), ctx, float(max_ctx(tr)), {i for i, t in enumerate(ids) if t in cp})
        batches.extend([[t] for t in ids] if r is None else [[ids[i] for i in g] for g in r[3]])
    order = {t: k for k, t in enumerate(ready)}  # longest batch first, then ready order
    batches.sort(key=lambda g: (-sum(times.get(t, nt.DEFAULT_TIME) for t in g), order[g[0]]))
    return batches


def write_dispatch_json(plan, lines):  # O(R)
    """Record the batches this call printed so the batch guard can match them (T0 is its own batch)."""
    batches = [t.split(",") for t, _ in lines]
    try:
        base.write_json(state.path(nt.stable_root(plan), "dispatch.json"),
                        {"batches": batches, "lines": [f"{t} {d}" for t, d in lines]})
    except OSError as e:  # a write miss never breaks dispatch
        print(f"warning: dispatch.json not written: {e}", file=sys.stderr)


def tier_of(plan, cfg):  # O(tiers)
    """task -> tier from the plan's saved or computed tiers; unknown tasks take the default tier."""
    default = cfg.get("default_tier", "sonnet")
    try:
        tiers = routing.read_tiers(plan, cfg)
    except base.advisory_errors() as e:  # no tiers: everything is default-tier
        print(f"tiers unavailable, using the default tier: {e}", file=sys.stderr)
        tiers = {}
    return lambda t: tiers.get(t, default)


# ------------------------------------------------------------------ plan_dispatch

def eligible_scores(units, ready_info, idle, cfg, tier):  # O(R * a * (F + o))
    """({unit: {agent id: score}}, {agent id: evict reason}, ids eligible for something). Strict: never across tiers."""
    table, reasons, somewhere = {}, {}, set()
    for u in units:
        members, deps, owns, est_context, info = ready_info(u)
        limits = reuse_limits(cfg, tier(u))
        table[u] = {}
        for ag in (a for a in idle if routing.agent_tier(a, cfg) == tier(u)):
            why = eligibility(ag, est_context, limits)
            if why:
                reasons.setdefault(ag["id"], why)
                continue
            somewhere.add(ag["id"])
            table[u][ag["id"]] = score(ag, deps, owns, info)
    return table, reasons, somewhere


def pick_agents(ready, in_lane, table, agents):  # O(R log R + R*a)
    """{unit: decision}; lane units reuse an idle member (lowest context, fewest tasks) or start fresh."""
    def rank(t):  # clear winners first, then polls with signal, then none
        top = max(table[t].values(), default=0)
        return 0 if decide(table[t]).startswith("agent=") else (1 if top > 0 else 2)

    order = {t: i for i, t in enumerate(ready)}
    taken, decided = set(), {}  # taken: agents on an earlier resolved line (agent= or poll)
    for t in sorted((x for x in ready if x not in in_lane), key=lambda x: (rank(x), order[x])):
        scores = {k: v for k, v in table[t].items() if k not in taken}
        decision = decide(scores)
        if decision.startswith("agent="):
            taken.add(decision.split("=", 1)[1])
        elif decision == "poll":  # polled agents are reserved too: a poll winner has one task
            taken.update(scores)
            decision = "poll " + ",".join(scores)
        decided[t] = decision
    ctx = {ag["id"]: float(ag.get("context_k", 0)) for ag in agents}
    ntasks = {ag["id"]: len(ag.get("tasks", [])) for ag in agents}
    for t in in_lane:
        free = sorted((k for k in table[t] if k not in taken), key=lambda k: (ctx.get(k, 0), ntasks.get(k, 0), k))
        if not free:
            decided[t] = "fresh"  # nothing waits: a fresh lane agent starts this round
            continue
        decided[t] = "agent=" + free[0]
        taken.add(free[0])
    return decided


def plan_dispatch(plan, pool, checkpointed, running, cap, cfg=None, batch=None):  # O(L + n + e + packing + R*a*(F + o))
    """[(tasks, decision)] and {agent id: evict reason}; tasks is "Tn" or a batch "Tn,Tm"; decision is 'agent=ID tier=T',
    'poll IDS tier=T' or 'fresh tier=T agent=TYPE'. A batch only goes to an idle agent of its own tier; the lane tier
    (haiku) reuses an idle eligible lane agent with no poll, else starts a fresh one. `batch=1` forces one task per agent."""
    cfg = cfg or routing.load_cfg()
    graph = nt.taskgraph(plan)
    info = nt.parse_plan(plan)
    task_tier = tier_of(plan, cfg)
    times = predicted_minutes(plan, graph, info)
    ready_ids = nt.ready_tasks(graph, times, checkpointed, running, cap, nt.taskgraph_full(plan)["status"],
                               nt.stable_filter(plan))  # only stable tasks when stable.json exists
    cp = stable_cp(plan) if (cfg.get("batch") or {}).get("critical_path_solo", True) else set()
    groups = make_batches(ready_ids, task_tier, info, times, lambda tr: routing.tier_limits(cfg, tr)["max_context_k"],
                          cp, busy_left_tenths(pool, times, checkpointed), batch == 1)
    members = {",".join(g): g for g in groups}  # batch key -> its tasks
    tier = lambda u: task_tier(members[u][0])  # noqa: E731 | a batch has one tier

    def ready_info(u):
        ms = members[u]
        return (ms, sorted({d for m in ms for d in graph.get(m, [])}), [o for m in ms for o in info.get(m, {}).get("owns", [])],
                sum(info.get(m, {}).get("est_context") or DEFAULT_CONTEXT_K for m in ms), info)

    ready = list(members)
    agents = pool.get("agents", [])
    table, reasons, somewhere = eligible_scores(ready, ready_info, [a for a in agents if is_idle(a)], cfg, tier)
    lane_tier = (cfg.get("lane") or {}).get("tier")
    decided = pick_agents(ready, [t for t in ready if lane_tier and tier(t) == lane_tier], table, agents)
    out = []
    for t in (t for t in ready if t in decided):
        d = decided[t] + f" tier={tier(t)}"
        out.append((t, d + (f" agent={routing.tier_agent(cfg, tier(t))}" if decided[t] == "fresh" else "")))
    return out, {k: v for k, v in reasons.items() if k not in somewhere}


def render(lines, evict, swarm=None):  # O(R + a)
    for aid, why in evict.items():
        print(f"evict={aid} {why}", file=sys.stderr)
    for t, decision in lines:
        print(f"{t} {decision}")
    for row in swarm or ():
        print(row)


def run(plan, pool_path, ledger, checkpointed=None, running=None, cap=10, batch=None):  # O(c + L + n + e + R*a*(F + o))
    """Library entry for checkpoint --dispatch; flags left as None take their defaults."""
    pool = load_pool(pool_path)
    done = nt.csv(checkpointed) if checkpointed is not None else sorted(ct.checkpointed_tasks(ledger))
    busy = nt.csv(running) if running is not None else running_from_pool(pool, done)
    import wave0
    w0 = wave0.synth_info(plan) and "T0" not in done and "T0" not in busy  # synthesized Wave 0 still pending
    lines, evict = plan_dispatch(plan, pool, done, busy, cap, batch=batch)
    if w0:  # first line, a lane agent
        cfg = routing.load_cfg()
        lt = (cfg.get("lane") or {}).get("tier", "haiku")
        lines = [("T0", f"wave0 tier={lt} agent={routing.tier_agent(cfg, lt)}")] + lines
    return lines, evict


# ------------------------------------------------------------------ entry points

def swarm_rows(plan, lines):  # O(R)
    """Cursor (or other) host: the additive SWARM block after the usual lines; None without a host."""
    import host_models as hm
    host = hm.detect_host(plan)  # Execution header or SUPERLITE_HOST
    if not host:
        return None
    return hm.swarm_lines([f"{t} {d}" for t, d in lines], hm.resolve(host, plan))


def assign_main(argv):  # O(L + a*o*F*len)
    """`dispatch assign --task Tn`: agent=<id> | poll | fresh, over the idle agents of the task's tier."""
    ap = argparse.ArgumentParser(prog="dispatch assign")
    ap.add_argument("--task", required=True)
    ap.add_argument("--pool", default=str(state.path(Path.cwd(), "pool.json")))
    ap.add_argument("--plan")
    a = ap.parse_args(argv)
    pool = load_pool(a.pool)
    plan = a.plan or pool.get("plan") or nt.DEFAULT_PLAN
    graph, info, cfg = nt.taskgraph(plan), nt.parse_plan(plan), routing.load_cfg()
    if a.task not in info:
        nt.die(f"unknown task {a.task}")
    task = info[a.task]
    tier = tier_of(plan, cfg)(a.task)
    limits = reuse_limits(cfg, tier)
    est_context = task.get("est_context") or DEFAULT_CONTEXT_K
    scores = {}
    for agent in (g for g in pool.get("agents", []) if not (g.get("busy") or g.get("status") == "busy")):
        if routing.agent_tier(agent, cfg) != tier:
            continue
        why = eligibility(agent, est_context, limits)
        if why:
            print(f"evict={agent['id']} {why}", file=sys.stderr)
            continue
        scores[agent["id"]] = score(agent, graph.get(a.task, []), task["owns"], info)
    print(decide(scores))
    return 0


def main(argv=None):  # O(c + L + n + e + R*a*(F + o))
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "ready":
        return nt.main(argv[1:])
    if argv and argv[0] == "assign":
        return assign_main(argv[1:])
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--plan")
    ap.add_argument("--pool", default=str(state.path(Path.cwd(), "pool.json")))
    ap.add_argument("--ledger", default=ct.DEFAULT_LEDGER)
    ap.add_argument("--checkpointed", help="default: the ledger's checkpoint lines")
    ap.add_argument("--running", help="default: `current` of each busy pool agent")
    ap.add_argument("--cap", type=int, default=None, help="default: superlite.json cap, else 10")
    ap.add_argument("--batch", type=int, help="most tasks per agent message (1 = one task per agent)")
    a = ap.parse_args(argv)
    import project_config
    try:  # bad superlite.json fails closed before any dispatch
        cap = project_config.effective_cap(a.cap, Path(".").resolve())
    except project_config.ProjectConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    plan = a.plan or load_pool(a.pool).get("plan") or nt.DEFAULT_PLAN
    import orchestrator
    blocked = orchestrator.require(plan, "dispatch")  # human mode waits for approval
    if blocked:
        return blocked
    lines, evict = run(plan, a.pool, a.ledger, a.checkpointed, a.running, cap, a.batch)
    try:
        swarm = swarm_rows(plan, lines)
    except Exception as e:  # noqa: BLE001 | host block is optional; never break dispatch
        print(f"warning: SWARM block skipped: {e}", file=sys.stderr)
        swarm = None
    write_dispatch_json(plan, lines)
    render(lines, evict, swarm)
    try:  # the budget line when it changed (needs .superlite/metrics.jsonl)
        import budget
        budget.maybe_print(plan, str(Path(a.pool).parent))
    except Exception as e:  # noqa: BLE001 | never break dispatch
        print(f"warning: budget line skipped: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
