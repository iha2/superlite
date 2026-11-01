"""Task graph and schedule for plan_tools: parse the `%% taskgraph` block, simulate the ready queue, report the
critical path, and the build shape (which edges are not waited on). Stdlib only.

An edge Ta -> Tb that Tb takes only through interfaces Ta gives `e.g.` examples for is not waited on
(planfile.Plan.waited_edges): the schedule, lint and stable partition leave it out of the graph.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys

import plan_tools as pt
from plan_stubs import draft_ids, param_names, parse_signature, sig_key
from plan_tools import REF_TAIL, TASK_ID, Plan, PlanError, Task, nkey, strip_ticks

IMPLEMENTER_PREFIX = 18000
CTX_CEILING = 80000
MERGE_MIN_VARIABLE = 18000
MERGE_MIN_MINUTES = 1.0
DEFAULT_SIZE_BAND = {"low_min": 1.5, "high_min": 6.0}
GRAPH_SKIP = ("flowchart", "graph", "subgraph", "end", "direction", "classdef", "style", "class", "click", "linkstyle")


def find_taskgraph_blocks(plan: Plan) -> list:  # O(L) total
    found = []
    for b in plan.mermaid_blocks():
        for j, ln in enumerate(b.content):
            if re.match(r"^\s*%%\s*taskgraph\b", ln):
                found.append((b, j))
                break
    return found


def _node_token(part: str) -> str:  # O(s)
    cleaned = re.sub(r'\[[^\]]*\]|\([^)]*\)|\{[^}]*\}|"[^"]*"', "", part).strip()
    m = re.match(r"[A-Za-z_][\w\-]*", cleaned)
    return m.group(0) if m else cleaned


def _graph_line(ln: str, deps: dict, mentioned: list) -> None:  # O(s)
    """Add the nodes and `a --> b` edges of one graph line to deps (task -> prerequisites)."""
    parts = re.split(r"\s*-->\s*(?:\|[^|]*\|\s*)?", ln)
    groups = [[_node_token(p) for p in part.split("&") if p.strip()] for part in parts]
    for tid in (t for ids in groups for t in ids):
        if not pt.TASK_ID_RE.match(tid):
            raise PlanError(f"unknown task id {tid!r} in taskgraph")
        deps.setdefault(tid, set())
        if tid not in mentioned:
            mentioned.append(tid)
    for left, right in zip(groups, groups[1:]):
        for b in right:
            deps[b].update(left)


def parse_taskgraph(plan: Plan) -> dict[str, list[str]]:  # O(L*s + R*M + e log n) total
    blocks = find_taskgraph_blocks(plan)
    if not blocks:
        raise PlanError("no '%% taskgraph' block found in plan")
    deps: dict[str, set[str]] = {}
    mentioned: list[str] = []
    for block, j in blocks:
        for raw in block.content[j + 1:]:
            ln = raw.strip()
            if ln and not ln.startswith("%%") and ln.split()[0].lower() not in GRAPH_SKIP:
                _graph_line(ln, deps, mentioned)
    known = set(plan.by_id)
    unknown = [t for t in mentioned if known and t not in known]
    if unknown:
        raise PlanError(f"unknown task id {unknown[0]!r} in taskgraph (no such task in plan)")
    order = [t.id for t in plan.tasks] + [t for t in mentioned if t not in known]
    graph = {tid: sorted(deps.get(tid, set()), key=nkey) for tid in order}
    cycle = find_cycle(graph)
    if cycle:
        raise PlanError("cycle in taskgraph: " + " → ".join(cycle))
    return graph


def find_cycle(graph: dict[str, list[str]]) -> list[str] | None:  # O(n+e) total
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(n: str):  # O(deg) + recursion
        state[n] = 1
        stack.append(n)
        for d in graph.get(n, []):
            if state.get(d, 0) == 1:
                return stack[stack.index(d):] + [d]
            r = visit(d) if state.get(d, 0) == 0 else None
            if r:
                return r
        stack.pop()
        state[n] = 2
        return None

    for n in graph:
        r = visit(n) if state.get(n, 0) == 0 else None
        if r:
            return list(reversed(r))
    return None


def ancestors(graph: dict[str, list[str]]) -> dict[str, set[str]]:  # O(e*n) time, O(n^2) space
    memo: dict[str, set[str]] = {}

    def anc(n: str) -> set[str]:  # O(deg*n) per call
        if n in memo:
            return memo[n]
        memo[n] = set()
        s: set[str] = set()
        for d in graph.get(n, []):
            s.add(d)
            s |= anc(d)
        memo[n] = s
        return s

    for n in graph:
        anc(n)
    return memo


def seams_of(graph: dict[str, list[str]]) -> list[str]:
    return [f"{d}↔{t}" for t in graph for d in graph[t]]


def unwaited_edges(plan: Plan) -> list[tuple[str, str]]:
    """Edges (producer, consumer) of the plan's graph that the consumer does not wait on."""
    waited = set(plan.waited_edges())
    return [e for e in plan.base.edges if e not in waited and e[0] in plan.by_id and e[1] in plan.by_id]


def waited_graph(plan: Plan) -> dict[str, list[str]]:
    """The task graph with the edges the consumer does not wait on left out."""
    gone = set(unwaited_edges(plan))
    return {t: [d for d in deps if (d, t) not in gone] for t, deps in parse_taskgraph(plan).items()}


def _predict():
    import predict  # lazy: predict imports plan_tools
    return predict


def effective_times(plan: Plan, graph: dict[str, list[str]], args) -> tuple[dict[str, float], dict[str, str]]:  # O(n*p) total + config I/O
    """Per-task minutes. Features present: the predictor's value wins. A stated `Est. time` is used only
    for a task with no features, or for every task with --stated."""
    predict = _predict()
    models = predict.load_models(getattr(args, "models", None))
    coef = predict.load_coef(getattr(args, "coef", None))
    force_stated = bool(getattr(args, "stated", False))
    times: dict[str, float] = {}
    source: dict[str, str] = {}
    for tid in graph:
        task = plan.by_id.get(tid) or Task(id=tid, synthetic=True)
        stated = task.stated_time if (force_stated or not task.has_features) else None
        if stated is not None:
            times[tid], source[tid] = float(stated), "stated"
            continue
        p = predict.predict_task(task, models, coef)
        times[tid], source[tid] = p["est_time_min"], p["source"]
    return times, source


def effective_context(task: Task, models: dict, coef: dict, force_stated: bool = False) -> tuple[int | None, str]:  # O(k*s + p)
    """Per-task context tokens. Features present: the predictor's value wins, as for Est. time. A stated
    `Est. context` is used only for a task with no features, or with --stated. (None, "none") if neither."""
    stated = task.stated_context
    if stated is not None and (force_stated or not task.has_features):
        return stated, "stated"
    if task.has_features:
        return _predict().predict_task(task, models, coef)["est_context"], "predicted"
    return None, "none"


def _run_queue(times: dict, graph: dict, cap: int) -> tuple[dict, dict, dict]:  # O(n*(n+e) + n*c) total
    """Start ready tasks longest first on up to cap lanes; returns (start, end, lane) per task."""
    order = {t: i for i, t in enumerate(sorted(graph, key=nkey))}
    done: dict[str, float] = {}
    start: dict[str, float] = {}
    lane: dict[str, int] = {}
    running: list[tuple[float, int, str, int]] = []
    free = list(range(1, max(1, cap) + 1))
    remaining = set(graph)
    now = 0.0
    while remaining or running:
        ready = sorted((t for t in remaining if all(d in done for d in graph[t])), key=lambda t: (-times[t], order[t]))
        while ready and free:
            t = ready.pop(0)
            ln = min(free)
            free.remove(ln)
            start[t], lane[t] = now, ln
            running.append((now + times[t], order[t], t, ln))
            remaining.discard(t)
        if not running:
            break
        running.sort()
        now = running[0][0]
        while running and running[0][0] <= now + 1e-12:
            end, _, t, ln = running.pop(0)
            done[t] = end
            free.append(ln)
    return start, done, lane


def _peak_width(start: dict, done: dict, times: dict) -> int:  # O(n log n)
    events = []
    for t in start:
        if times[t] > 0:
            events += [(start[t], 1), (done[t], -1)]
    events.sort(key=lambda e: (e[0], e[1]))
    width = peak = 0
    for _, d in events:
        width += d
        peak = max(peak, width)
    return peak


def _critical_path(times: dict, graph: dict) -> tuple[list, float]:  # O(n*(n+e))
    """The longest chain on the dependency graph (unlimited lanes) and its minutes."""
    ef: dict[str, float] = {}
    pred: dict[str, str | None] = {}
    anc_all = ancestors(graph)
    for t in sorted(graph, key=lambda x: (len(anc_all[x]), nkey(x))):
        best, bp = 0.0, None
        for d in sorted(graph[t], key=nkey):
            if ef[d] > best + 1e-12:
                best, bp = ef[d], d
        ef[t] = best + times[t]
        pred[t] = bp
    if not ef:
        return [], 0.0
    endt = max(sorted(ef, key=nkey), key=lambda x: ef[x])
    path = [endt]
    while pred[path[-1]]:
        path.append(pred[path[-1]])
    path.reverse()
    return path, ef[endt]


def simulate(times: dict[str, float], graph: dict[str, list[str]], cap: int) -> dict:  # O(n*(n+e) + n*c) total
    start, done, lane = _run_queue(times, graph, cap)
    path, cp_len = _critical_path(times, graph)
    return {
        "start": start, "end": done, "lane": lane,
        "build_min": max(done.values()) if done else 0.0,
        "peak_width": _peak_width(start, done, times),
        "critical_path": path,
        "critical_path_min": cp_len,
    }


def schedule_report(plan: Plan, args) -> dict:  # O(n*(n+e) + n*p) total
    graph = waited_graph(plan)
    drafts = draft_ids(plan)
    graph = {t: [d for d in deps if d not in drafts] for t, deps in graph.items() if t not in drafts}
    times, source = effective_times(plan, graph, args)
    cap = args.cap
    sim = simulate(times, graph, cap)
    sched = _predict().load_coef(getattr(args, "coef", None)).get("schedule") or {}
    # Run clock: setup + scaled build + wrap-up.
    pre = float(sched.get("pre_build_min", 0.0))
    post = float(sched.get("post_build_min", sched.get("tail_min", 3.5)))
    scale = float(sched.get("build_scale", 1.0))
    if getattr(args, "tail_min", None) is not None:  # --tail-min overrides wrap-up only
        post = float(args.tail_min)
    target = args.target_min if getattr(args, "target_min", None) is not None else plan.header_target()
    total = pre + scale * float(sim["build_min"]) + post
    sim.update({
        "graph": graph, "times": times, "source": source,
        "pre_build_min": pre, "build_scale": scale, "post_build_min": post,
        "tail_min": post,  # alias: wrap-up (old name kept for JSON consumers)
        "total_min": total, "target_min": target,
        "target_met": None if target is None else total <= target + 1e-9,
        "cap": cap,
    })
    return sim


def summary_line(rep: dict) -> str:
    path = " → ".join(rep["critical_path"]) or "-"
    line = (f"critical path: {path} ({rep['critical_path_min']:.1f} min) · peak width {rep['peak_width']} · "
            f"predicted build {rep['build_min']:.1f} min · predicted total ≈ {rep['total_min']:.1f} min")
    if rep["target_min"] is not None:
        line += " (target ✓)" if rep["target_met"] else f" (target ✗ {rep['target_min']:.1f})"
    return line


def chain_id(cp, ctimes) -> str:
    """Short hash of the chain's task ids and predicted minutes: a ruling written before lint cannot match it."""
    key = "|".join(cp) + ":" + "|".join(f"{m:.1f}" for m in ctimes)
    return "chain-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:4]


def edge_notes(plan: Plan) -> list[str]:
    """One advisory EDGE line per edge the consumer does not wait on."""
    return [f"EDGE: {b} does not wait for {a}: every interface it takes from {a} has an e.g. example, so the edge is not waited on"
            for a, b in unwaited_edges(plan)]


def shape_line(plan: Plan, rep: dict) -> str:
    """shape: chain length, peak width, edges not waited on, predicted build minutes."""
    return (f"shape: chain {len(rep['critical_path'])} tasks {rep['critical_path_min']:.1f} min, peak width {rep['peak_width']}, "
            f"edges not waited on {len(unwaited_edges(plan))}, predicted build {rep['build_min']:.1f} min")


def _merge_candidates(tid: str, plan: Plan, graph: dict, ctx: dict, anc: dict, cp: set) -> list:  # O(n)
    my_dirs = {o.rsplit("/", 1)[0] for o in plan.by_id[tid].owns if "/" in o}
    cands = []
    for other in graph:
        ot = plan.by_id.get(other)
        adjacent = other in graph[tid] or tid in graph[other]
        same_pkg = bool(ot and my_dirs & {o.rsplit("/", 1)[0] for o in ot.owns if "/" in o})
        independent = other not in anc[tid] and tid not in anc[other]
        if other == tid or not (adjacent or same_pkg) or (independent and tid in cp and other in cp):
            continue
        merged = (ctx[tid] or IMPLEMENTER_PREFIX) + max(0, (ctx[other] or IMPLEMENTER_PREFIX) - IMPLEMENTER_PREFIX)
        if merged <= CTX_CEILING:
            cands.append((0 if adjacent else 1, merged, nkey(other), other))
    return sorted(cands)


def mergeable_tasks(plan: Plan, graph, times, args) -> list[dict]:  # O(n*p + e*n + n*(n+e) + n^2*k*s) total
    predict = _predict()
    models = predict.load_models(getattr(args, "models", None))
    coef = predict.load_coef(getattr(args, "coef", None))
    ctx: dict[str, int | None] = {}
    for tid in graph:
        task = plan.by_id.get(tid)
        ctx[tid] = effective_context(task, models, coef, bool(getattr(args, "stated", False)))[0] if task else None
    anc = ancestors(graph)
    cp = set(simulate(times, graph, 1000)["critical_path"])
    res = []
    for tid in graph:
        if tid not in plan.by_id:
            continue
        reasons = []
        c, tm = ctx[tid], times.get(tid)
        if c is not None and c - IMPLEMENTER_PREFIX < MERGE_MIN_VARIABLE:
            reasons.append(f"variable context {(c - IMPLEMENTER_PREFIX) / 1000:.0f}k < {MERGE_MIN_VARIABLE // 1000}k")
        if tm is not None and tm < MERGE_MIN_MINUTES:
            reasons.append(f"predicted {tm:.1f} min < {MERGE_MIN_MINUTES:.0f} min")
        if reasons:
            cands = _merge_candidates(tid, plan, graph, ctx, anc, cp)
            res.append({"task": tid, "reasons": reasons, "merge_with": cands[0][3] if cands else None})
    return res


def cmd_taskgraph(args) -> int:  # O(C+L + parse + n*p + merge) total
    plan = Plan.load(args.plan)
    graph = parse_taskgraph(plan)
    seams = seams_of(graph)
    times, _ = effective_times(plan, graph, args)
    merges = mergeable_tasks(plan, graph, times, args)
    status = {tid: (plan.by_id[tid].status if tid in plan.by_id else "approved") for tid in graph}
    kinds = {tid: (plan.by_id[tid].explicit_features.get("kind", "design") if tid in plan.by_id else "design")
             for tid in graph}
    if args.format == "full":
        print(json.dumps({"graph": graph, "seams": seams, "mergeable": merges, "status": status, "kind": kinds},
                         ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(graph, ensure_ascii=False, indent=2))
    for s in seams:
        print(f"seam {s}", file=sys.stderr)
    for tid, st in status.items():
        if st == "draft":
            print(f"status {tid} draft", file=sys.stderr)
    for m in merges:
        to = f"merge with {m['merge_with']}" if m["merge_with"] else "no safe merge partner"
        print(f"warning: mergeable {m['task']} ({'; '.join(m['reasons'])}); {to}", file=sys.stderr)
    return 0


def cmd_schedule(args) -> int:  # O(C+L + schedule) total
    plan = Plan.load(args.plan)
    rep = schedule_report(plan, args)
    if args.json:
        out = {k: rep[k] for k in ("critical_path", "critical_path_min", "peak_width", "build_min",
                                   "pre_build_min", "build_scale", "post_build_min", "tail_min",
                                   "total_min", "target_min", "target_met", "cap") if k in rep}
        out["tasks"] = {t: {"min": round(rep["times"][t], 3), "start": round(rep["start"][t], 3),
                            "end": round(rep["end"][t], 3), "lane": rep["lane"][t], "source": rep["source"][t]}
                        for t in rep["times"]}
        out["shape"] = shape_line(plan, rep)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    print(summary_line(rep))
    print(shape_line(plan, rep))
    for t in sorted(rep["start"], key=lambda x: (rep["start"][x], nkey(x))):
        print(f"  {t}: lane {rep['lane'][t]}, {rep['start'][t]:.1f} to {rep['end'][t]:.1f} min ({rep['source'][t]})")
    return 0


PRIMITIVE = re.compile(r"^(none|void|unit|null|undefined|bool|boolean|int|integer|float|number|str|string|bytes|any)$", re.I)
CALL = re.compile(r"^`?([A-Za-z_][\w.]*)\s*\(")


def structured(ret) -> bool:
    """A return type that is more than a primitive: it needs an example to be faked."""
    return bool(ret) and not PRIMITIVE.match(re.sub(r"\s+", "", ret))


def has_example(task: Task, sig: dict) -> bool:  # O(k*s)
    names = set()
    for ex in task.examples:
        m = CALL.match(strip_ticks(ex))
        if m:
            names.update((m.group(1), m.group(1).rsplit(".", 1)[-1]))
    return sig["name"] in names or sig["member"] in names


def resolve_consumes(plan: Plan) -> list:  # O(n*q)
    """[(consumer id, consumer sig, [(producer id, producer sig)])]: name first, then the Task-id tail narrows it."""
    produced: dict = {}
    for t in plan.tasks:
        for item in t.produces:
            sig = parse_signature(item)
            if sig:
                produced.setdefault(sig_key(sig), []).append((t.id, sig))
    out = []
    for t in plan.tasks:
        for item in t.consumes:
            sig = parse_signature(item)
            if not sig:
                continue
            ref = re.search(REF_TAIL, strip_ticks(item))
            refs = re.findall(TASK_ID, ref.group(1)) if ref else []
            cands = [c for c in produced.get(sig_key(sig), []) if not refs or c[0] in refs]
            out.append((t.id, sig, cands, refs))
    return out


def example_issues(plan: Plan) -> list:  # O(n*q)
    """EXAMPLE: a consumer of a structured Produces without an `e.g.` line is a fix task until the producer has one."""
    issues, seen = [], set()
    for cid, sig, cands, _ in resolve_consumes(plan):
        if not cands:  # lint reports the missing Produces on its own
            continue
        pid, psig = next(((i, s) for i, s in cands if param_names(s["args"]) == param_names(sig["args"])), cands[0])
        if structured(psig["ret"]) and not has_example(plan.by_id[pid], psig) and (cid, psig["name"]) not in seen:
            seen.add((cid, psig["name"]))
            issues.append(pt.Issue(f"EXAMPLE: {cid} consumes {psig['name']} but its Produces line has no e.g.", [cid]))
    return issues


def split_notes(plan: Plan) -> list:  # O(n*q)
    """NOTE for a task of two or more components that consumes independent interfaces (different modules)."""
    out = []
    for t in plan.tasks:
        comps = t.explicit_features.get("components", 1)
        mods = {}
        for item in t.consumes:
            sig = parse_signature(item)
            if sig:
                mods.setdefault(".".join(sig["module"]) or sig["cls"] or sig["name"], sig["name"])
        if comps >= 2 and len(mods) >= 2:
            out.append(f"NOTE: {t.id} has {comps} components and consumes independent interfaces ({', '.join(sorted(mods.values()))}); "
                       "consider splitting it so each part starts when its own producer is done")
    return out


def sig_text(item: str) -> str:
    sig = parse_signature(item)
    if not sig:
        return re.sub(r"\s+", " ", strip_ticks(item)).strip()
    return f"{sig['name']}({sig['args']})" + (f" -> {sig['ret']}" if sig["ret"] else "")


def task_interface(task: Task) -> dict:  # O(k*s)
    return {"consumes": [sig_text(i) for i in task.consumes], "produces": [sig_text(i) for i in task.produces]}


def block_hash(task: Task) -> str:  # O(block bytes)
    """Whitespace-insensitive digest of one task block."""
    return hashlib.sha1(" ".join(task.text.split()).encode("utf-8")).hexdigest()[:12]


def dispatched_tasks(run_dir) -> set[str]:  # O(c + pool)
    """Task ids already sent to an agent: the progress ledger and the pool."""
    import base
    out: set[str] = set()
    ledger = run_dir / "progress.md"
    if ledger.is_file():
        from checkpoint import LEDGER_LINE
        text = ledger.read_text(encoding="utf-8", errors="replace")
        out.update(m.group(1) for ln in text.splitlines() if (m := LEDGER_LINE.match(ln)))
        for line in re.findall(r"^[-*+ ]*unblocked:\s*dispatched\s+((?:T\d+[A-Za-z]?\s*)+)", text, re.M):
            out.update(line.split())
    pool = base.read_json(run_dir / "pool.json", {})
    for ag in pool.get("agents", []) if isinstance(pool, dict) else []:
        out.update(x for x in ag.get("tasks") or [] if isinstance(x, str))
        cur = ag.get("current") or []  # "Tn", a batch "Tn,Tm", or a list
        out.update(x.strip() for x in (cur if isinstance(cur, list) else str(cur).split(",")) if x.strip())
    return out


def stable_partition(plan: Plan, issues: list, warnings: list[str]):  # O(n + issues + e*n)
    """STABLE: no task-scoped issue of its own, no waited edge to an unstable task, not on a flagged CHAIN.
    Issues carry their subject tasks (Issue.tasks); plan-scoped ones never block a stable task."""
    ids = [t.id for t in plan.tasks]
    reasons: dict[str, list[str]] = {i: [] for i in ids}
    plan_issues: list[str] = []
    for iss in list(issues) + [w for w in warnings if w.startswith("MISSED TARGET")]:
        scope = getattr(iss, "scope", "plan")
        subj = [x for x in getattr(iss, "tasks", ()) if x in reasons]
        if scope == "plan" or not subj:
            plan_issues.append(str(iss))
        text = "on flagged CHAIN" if getattr(iss, "chain", False) else str(iss)
        for x in (subj if getattr(iss, "chain", False) or scope == "task" else []):
            reasons[x].append(text)
    try:
        graph = waited_graph(plan)
    except PlanError:
        graph = {}
    changed = True
    while changed:
        changed = False
        for t in ids:
            bad = [d for d in graph.get(t, []) if reasons.get(d)]
            if bad and not any(r.startswith("depends on unstable") for r in reasons[t]):
                reasons[t].append("depends on unstable " + ", ".join(bad))
                changed = True
    stable = [t for t in ids if not reasons[t]]
    fix = [{"task": t, "reasons": reasons[t]} for t in ids if reasons[t]]
    return stable, fix, plan_issues
