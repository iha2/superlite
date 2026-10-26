#!/usr/bin/env python3
"""Build shape: contract examples, removable edges, prune, chain ids, the `shape:` line.

    shape.py prune --plan P [--dir .superlite]     (also: plan_tools prune --plan P)

A task's `e.g.` lines under Produces give hand-built examples of what the producer returns
(`e.g. parse("1+2") -> Binary("+", Num(1), Num(2))`: a call and its result, language-neutral). A consumer is
built and tested against those examples (its fake returns them; the producer's tests assert the same ones),
so it does not have to wait for the producer. Library, used by `plan_tools lint` and `schedule`:
example_issues, edge_notes, removable_edges, split_notes, chain_id, shape_line, prune. Stdlib only.
"""
# Complexity notation:
#   n = tasks, e = edges, q = Consumes items overall, L = plan lines
import argparse  # O(1)
import hashlib  # O(1)
import re  # O(1)
import sys  # O(1)
from pathlib import Path  # O(1)

sys.path.insert(0, str(Path(__file__).resolve().parent))  # O(1)
import plan_tools as pt  # noqa: E402 | O(1)

PRIMITIVE = re.compile(r"^(none|void|unit|null|undefined|bool|boolean|int|integer|float|number|str|string|bytes|any)$", re.I)  # O(1)
CALL = re.compile(r"^`?([A-Za-z_][\w.]*)\s*\(")  # O(1)


def structured(ret) -> bool:  # O(s)
    """A return type that is more than a primitive: it needs an example to be faked."""
    return bool(ret) and not PRIMITIVE.match(re.sub(r"\s+", "", ret))  # O(s)


def example_names(task) -> set:  # O(k*s)
    out = set()  # O(1)
    for ex in task.examples:  # O(k)
        m = CALL.match(pt.strip_ticks(ex))  # O(s)
        if m:  # O(1)
            out.update((m.group(1), m.group(1).rsplit(".", 1)[-1]))  # O(1)
    return out  # O(1)


def has_example(task, sig) -> bool:  # O(k*s)
    names = example_names(task)  # O(k*s)
    return sig["name"] in names or sig["member"] in names  # O(1)


def resolve_consumes(plan):  # O(n*q)
    """[(consumer id, consumer sig, [(producer id, producer sig)])] using the lint's matching (name, then Task-id tail)."""
    produced: dict = {}  # O(1)
    for t in plan.tasks:  # O(n)
        for item in t.produces:  # O(q)
            sig = pt.parse_signature(item)  # O(s)
            if sig:  # O(1)
                produced.setdefault(pt.sig_key(sig), []).append((t.id, sig))  # O(1)
    out = []  # O(1)
    for t in plan.tasks:  # O(n)
        for item in t.consumes:  # O(q)
            sig = pt.parse_signature(item)  # O(s)
            if not sig:  # O(1)
                continue  # O(1)
            ref = re.search(pt.REF_TAIL, pt.strip_ticks(item))  # O(s)
            refs = re.findall(pt.TASK_ID, ref.group(1)) if ref else []  # O(s)
            cands = produced.get(pt.sig_key(sig), [])  # O(1)
            if refs:  # O(1)
                cands = [c for c in cands if c[0] in refs]  # O(cands)
            out.append((t.id, sig, cands))  # O(1)
    return out  # O(q)


def example_issues(plan) -> list:  # O(n*q)
    """EXAMPLE: a consumer of a structured Produces without an `e.g.` line is a fix task until the producer has one."""
    issues, seen = [], set()  # O(1)
    for cid, sig, cands in resolve_consumes(plan):  # O(q)
        if not cands:  # lint reports the missing Produces on its own | O(1)
            continue  # O(1)
        pid, psig = cands[0]  # O(1)
        for c_id, c_sig in cands:  # the candidate whose parameters match, as the lint does | O(cands)
            if pt.param_names(c_sig["args"]) == pt.param_names(sig["args"]):  # O(s)
                pid, psig = c_id, c_sig  # O(1)
                break  # O(1)
        if structured(psig["ret"]) and not has_example(plan.by_id[pid], psig) and (cid, psig["name"]) not in seen:  # O(k*s)
            seen.add((cid, psig["name"]))  # O(1)
            issues.append(pt.Issue(f"EXAMPLE: {cid} consumes {psig['name']} but its Produces line has no e.g.", [cid]))  # O(1)
    return issues  # O(q)


def removable_edges(plan, graph, skip=()) -> list:  # O(e*q)
    """[(producer, consumer)] edges Tb -> Ta where every interface the consumer takes from the producer has an example.
    An edge that carries no consumed interface is never removable (its tests may run the producer's code)."""
    by_pair: dict = {}  # O(1)
    for cid, sig, cands in resolve_consumes(plan):  # O(q)
        for pid, psig in cands:  # O(cands)
            by_pair.setdefault((pid, cid), []).append((psig, has_example(plan.by_id[pid], psig)))  # O(k*s)
    out = []  # O(1)
    for b, deps in graph.items():  # O(n)
        for a in deps:  # O(deg)
            items = by_pair.get((a, b))  # O(1)
            if items and all(ok for _, ok in items) and b not in skip and a in plan.by_id:  # O(items)
                out.append((a, b))  # O(1)
    return out  # O(e)


def drop(graph, edges) -> dict:  # O(n + e)
    gone = set(edges)  # O(e)
    return {t: [d for d in deps if (d, t) not in gone] for t, deps in graph.items()}  # O(n + e)


def _path_text(rep) -> str:  # O(path)
    return f"{' -> '.join(rep['critical_path'])} {rep['critical_path_min']:.1f} min"  # O(path)


def edge_notes(plan, graph, rep) -> list:  # O(e*(n*(n + e)))
    """One advisory EDGE line per removable edge, with the critical path once that edge is dropped."""
    out = []  # O(1)
    for a, b in removable_edges(plan, graph):  # O(e*q)
        sim = pt.simulate(rep["times"], drop(graph, [(a, b)]), rep["cap"])  # O(n*(n + e))
        out.append(f"EDGE: {b} waits for {a} only for interfaces that have examples; drop the edge "  # O(1)
                   f"(critical path {' -> '.join(sim['critical_path'])} {sim['critical_path_min']:.1f} min)")
    return out  # O(e)


def split_notes(plan) -> list:  # O(n*q)
    """NOTE for a task of two or more components that consumes independent interfaces (different modules)."""
    out = []  # O(1)
    for t in plan.tasks:  # O(n)
        comps = t.explicit_features.get("components", 1)  # O(k*s)
        mods = {}  # O(1)
        for item in t.consumes:  # O(q)
            sig = pt.parse_signature(item)  # O(s)
            if sig:  # O(1)
                mods.setdefault(".".join(sig["module"]) or sig["cls"] or sig["name"], sig["name"])  # O(1)
        if comps >= 2 and len(mods) >= 2:  # O(1)
            out.append(f"NOTE: {t.id} has {comps} components and consumes independent interfaces ({', '.join(sorted(mods.values()))}); "
                       "consider splitting it so each part starts when its own producer is done")  # O(1)
    return out  # O(n)


def chain_id(cp, ctimes) -> str:  # O(path)
    """Short hash of the chain's task ids and predicted minutes: a ruling written before lint cannot match it."""
    key = "|".join(cp) + ":" + "|".join(f"{m:.1f}" for m in ctimes)  # O(path)
    return "chain-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:4]  # O(path)


def shape_line(plan, graph, rep) -> str:  # O(e*n*(n + e))
    """shape: chain length, peak width, removable edges, predicted build minutes with and without them."""
    rem = removable_edges(plan, graph)  # O(e*q)
    cp = rep["critical_path"]  # O(1)
    line = (f"shape: chain {len(cp)} tasks {rep['critical_path_min']:.1f} min, peak width {rep['peak_width']}, "  # O(1)
            f"removable edges {len(rem)}, predicted build {rep['build_min']:.1f} min")
    if rem:  # O(1)
        sim = pt.simulate(rep["times"], drop(graph, rem), rep["cap"])  # O(n*(n + e))
        line += f" ({sim['build_min']:.1f} min without them)"  # O(1)
    return line  # O(1)


def _rewrite(lines, removed):  # O(L)
    """Graph lines without the removed edges: chains and `&` groups are expanded to pair lines, other lines kept."""
    out, kept_nodes = [], set()  # O(1)
    for raw in lines:  # O(L)
        ln = raw.strip()  # O(s)
        if "-->" not in ln or ln.startswith("%%"):  # O(1)
            out.append(raw)  # O(1)
            continue  # O(1)
        parts = re.split(r"\s*-->\s*(?:\|[^|]*\|\s*)?", ln)  # O(s)
        groups = [[(pt._node_token(p), p.strip()) for p in part.split("&") if p.strip()] for part in parts]  # O(s)
        indent = raw[:len(raw) - len(raw.lstrip())]  # O(1)
        emitted = False  # O(1)
        for left, right in zip(groups, groups[1:]):  # O(parts)
            for a_id, a_raw in left:  # O(left)
                for b_id, b_raw in right:  # O(right)
                    if (a_id, b_id) in removed:  # O(1)
                        continue  # O(1)
                    out.append(f"{indent}{a_raw} --> {b_raw}")  # O(1)
                    kept_nodes.update((a_id, b_id))  # O(1)
                    emitted = True  # O(1)
        if not emitted:  # every edge of the line is gone: keep its nodes so they stay in the graph | O(1)
            for g in groups:  # O(parts)
                for n_id, n_raw in g:  # O(g)
                    if n_id not in kept_nodes:  # O(1)
                        out.append(f"{indent}{n_raw}")  # O(1)
    return out  # O(L)


def prune(plan_path: str, args=None, sl_dir=None) -> list:  # O(e*n*(n + e))
    """Rewrite the taskgraph without the removable edges whose consumer is not dispatched, then recompute the schedule.
    Returns the pruned edges."""
    plan = pt.Plan.load(plan_path)  # O(L)
    graph = pt.parse_taskgraph(plan)  # O(L*s)
    sl = Path(sl_dir) if sl_dir else pt.stable_root(plan_path) / ".superlite"  # O(1)
    sent = pt.dispatched_tasks(sl)  # tasks already dispatched are frozen: their edges stay | O(c)
    edges = removable_edges(plan, graph, skip=sent)  # O(e*q)
    if edges:  # O(1)
        removed = set(edges)  # O(e)
        blocks = pt.find_taskgraph_blocks(plan)  # O(L)
        for b, j in reversed(blocks):  # last block first so earlier line numbers hold | O(blocks)
            first, last = b.start + 1 + j + 1, b.end  # graph lines after the `%% taskgraph` marker | O(1)
            plan.lines[first:last] = _rewrite(plan.lines[first:last], removed)  # O(L)
        plan.path.write_bytes(plan.to_text().encode("utf-8"))  # O(C)
    args = args or pt.build_parser().parse_args(["schedule", "--plan", plan_path, "--insert"])  # O(1)
    args.insert, args.json = True, False  # O(1)
    fresh = pt.Plan.load(plan_path)  # O(L)
    pt.insert_schedule(fresh, pt.schedule_report(fresh, args))  # O(n*(n + e))
    return edges  # O(e)


def main(argv=None):  # O(prune)
    ap = argparse.ArgumentParser(description=__doc__.split(chr(10))[0])  # O(1)
    ap.add_argument("cmd", choices=["prune"])  # O(1)
    ap.add_argument("--plan", required=True)  # O(1)
    ap.add_argument("--dir")  # O(1)
    a = ap.parse_args(argv)  # O(1)
    edges = prune(a.plan, None, a.dir)  # O(prune)
    print("pruned: " + (", ".join(f"{x}->{y}" for x, y in edges) if edges else "no removable edges"))  # O(e)
    return 0  # O(1)


if __name__ == "__main__":  # O(1)
    sys.exit(main())  # O(1)
