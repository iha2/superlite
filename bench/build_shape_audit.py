"""Build-shape audit: what the task graph of each plan costs, which edges are removable, and what pruning could save.

    python bench/build_shape_audit.py [--runs DIR] [--write]      # --write: bench/results/build-shape-audit.md

Per plan with a `%% taskgraph`: chain length, peak width, edge classes, and the what-if schedule with removable edges
pruned (`sl plan_tools schedule` on a pruned COPY, never the original). Per real run (stream log + plan): the wait the
edges caused, as an upper bound. Per run: `Ruling: sequential` lines in the first plan draft versus added later.

ASSUMPTIONS (stated, simple):
- Edge P -> C classes. Consumer C's `Consumes` items attributed to P are those tagged `(P)` or, untagged, whose name matches a
  `Produces` item of P (name = last dotted identifier before `(`).
  removable = C lists at least one such item and every one has a `Produces` line in P (C only needs names);
  missing_produces = some consumed item has no `Produces` line in P (fixable by writing one);
  ordering = C consumes nothing from P (an ordering edge such as docs after code, or an undeclared dependency);
  real_ruling = held by a `Ruling: sequential` line of the plan that names both task ids or a package both tasks own files in
  (the consumer's tests need the producer's real code); it is never pruned.
  removable edges are pruned; missing_produces edges are pruned only in the 'with contract examples' what-if.
- Actual wait: edge_wait(task) = its start minus the first build start, for tasks with dependencies: an UPPER BOUND on what
  removing that task's edges could save (it assumes a free agent and no other limit). The pruning upper bound replays the
  run's measured task durations (build + rework) over the pruned graph with unlimited agents and zero start lag.
- Splitting a task with k components into k equal parallel tasks divides its predicted minutes by k (upper bound)."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import harness  # noqa: E402

pt = harness._load_plan_tools()
SL = ROOT / "skills" / "swarm" / "scripts" / "sl"
SEQ = re.compile(r"^\s*[-*]?\s*Ruling:\s*sequential", re.I | re.M)
TAG = re.compile(r"\(\s*(T\d+(?:\s*,\s*T\d+)*)\s*\)\s*$")


# ---- plan graph ----------------------------------------------------------------------------------
def name_key(item: str) -> str | None:
    m = re.match(r"\s*([\w.]+)", item)
    return m.group(1).split(".")[-1] if m else None


def consumed_from(plan, graph: dict, producer: str, consumer: str) -> list[str]:
    """Consumer's Consumes items attributed to the producer (tagged, or untagged with a matching Produces name)."""
    pkeys = {name_key(i) for i in plan.by_id[producer].produces}
    items = []
    for it in plan.by_id[consumer].consumes:
        tag = TAG.search(it)
        if tag:
            if producer in re.findall(r"T\d+", tag.group(1)):
                items.append(it)
        elif name_key(it) in pkeys:
            items.append(it)
    return items


def sequential_rulings(plan) -> list[str]:
    return [ln.strip() for ln in plan.text.splitlines() if SEQ.match(ln)] if hasattr(plan, "text") else []


def packages(task) -> set[str]:
    return {m.group(1).lower() for p in task.owns for m in [re.match(r"(?:src/)?([\w-]+)/", p.replace("\\", "/"))] if m}


def ruled(plan, rulings: list[str], producer: str, consumer: str) -> bool:
    """An edge is held by a `Ruling: sequential` line that names both task ids, or a package both tasks own files in."""
    shared = packages(plan.by_id[producer]) & packages(plan.by_id[consumer])
    for ln in rulings:
        ids = set(re.findall(r"\bT\d+\b", ln))
        if {producer, consumer} <= ids or any(re.search(rf"\b{re.escape(k)}\b", ln, re.I) for k in shared if k not in ("tests", "src")):
            return True
    return False


def classify_edges(plan, graph: dict) -> list[dict]:
    rulings = sequential_rulings(plan)
    out = []
    for consumer, deps in graph.items():
        for producer in deps:
            if producer not in plan.by_id or consumer not in plan.by_id:
                continue
            items = consumed_from(plan, graph, producer, consumer)
            pkeys = {name_key(i) for i in plan.by_id[producer].produces}
            missing = [i for i in items if name_key(i) not in pkeys]
            base = "ordering" if not items else ("missing_produces" if missing else "removable")
            kind = "real_ruling" if ruled(plan, rulings, producer, consumer) else base
            out.append({"producer": producer, "consumer": consumer, "class": kind, "class_without_ruling": base, "items": len(items), "missing": missing})
    return out


def longest_path(graph: dict, w: dict) -> tuple[float, list[str]]:
    best: dict[str, tuple[float, list[str]]] = {}

    def go(t):
        if t not in best:
            pre = max((go(d) for d in graph.get(t, []) if d in w), key=lambda x: x[0], default=(0.0, []))
            best[t] = (pre[0] + w.get(t, 0.0), pre[1] + [t])
        return best[t]
    return max((go(t) for t in graph if t in w), key=lambda x: x[0], default=(0.0, []))


def pruned_text(plan_path: Path, graph: dict, drop: set[tuple[str, str]]) -> str:
    """The plan with the dropped (producer, consumer) edges removed from the `%% taskgraph` block."""
    lines = Path(plan_path).read_text(encoding="utf-8-sig").splitlines()
    i = next(k for k, ln in enumerate(lines) if re.match(r"^\s*%%\s*taskgraph\b", ln))
    j = next((k for k in range(i + 1, len(lines)) if lines[k].lstrip().startswith("```")), len(lines))
    kept = [(p, c) for c, ds in graph.items() for p in ds if (p, c) not in drop]
    linked = {x for e in kept for x in e}
    block = [f"  {p} --> {c}" for p, c in kept] + [f"  {t}" for t in graph if t not in linked]
    return "\n".join(lines[:i + 1] + block + lines[j:]) + "\n"


def schedule(text: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "plan.md"
        p.write_text(text, encoding="utf-8")
        r = subprocess.run(["bash", str(SL), "plan_tools", "schedule", "--plan", str(p), "--json"], capture_output=True, text=True)
    return json.loads(r.stdout)


def plan_report(plan_path: Path) -> dict | None:
    plan = pt.Plan(Path(plan_path).read_text(encoding="utf-8-sig"), Path(plan_path))
    try:
        graph = pt.parse_taskgraph(plan)
    except pt.PlanError:
        return None
    edges = classify_edges(plan, graph)
    by = lambda c: {(e["producer"], e["consumer"]) for e in edges if e["class"] == c}  # noqa: E731
    base = schedule(pruned_text(plan_path, graph, set()))
    times = {t: v["min"] for t, v in base["tasks"].items()}
    chain = longest_path(graph, {t: 1.0 for t in graph})
    pr = schedule(pruned_text(plan_path, graph, by("removable")))
    pc = schedule(pruned_text(plan_path, graph, by("removable") | by("missing_produces")))
    free = {(e["producer"], e["consumer"]) for e in edges if e["class"] == "real_ruling" and e["class_without_ruling"] == "removable"}
    pe = schedule(pruned_text(plan_path, graph, by("removable") | free))   # edge-specific rulings: only genuinely real edges stay held
    split = dict(times)
    for t, task in plan.by_id.items():
        k = task.explicit_features.get("components", 1)
        if t in split and k > 2:
            split[t] = times[t] / k
    split_cp = longest_path(graph, split)[0]
    brief = lambda s: {"critical_path": s["critical_path"], "critical_path_min": round(s["critical_path_min"], 1),  # noqa: E731
                       "build_min": round(s["build_min"], 1), "peak_width": s["peak_width"]}
    return {
        "tasks": len(graph), "edges": len(edges), "chain_tasks": int(chain[0]), "chain": chain[1], "base": brief(base),
        "edge_classes": {c: sum(1 for e in edges if e["class"] == c) for c in ("removable", "missing_produces", "ordering", "real_ruling")},
        "pruned_removable": brief(pr), "pruned_removable_and_missing": brief(pc), "pruned_edge_specific_rulings": brief(pe), "ruled_but_removable": len(free),
        "split_multi_component": {"tasks_split": [t for t, task in plan.by_id.items() if task.explicit_features.get("components", 1) > 2 and t in times],
                                  "critical_path_min_uncapped": round(split_cp, 1),
                                  "baseline_uncapped_min": round(longest_path(graph, times)[0], 1)},
        "edge_list": edges, "graph": graph, "times": times,
    }


# ---- real runs -----------------------------------------------------------------------------------
def replay(graph: dict, w: dict, cap: int, lag: float, order: list[str]) -> float:
    """Span of a list-scheduling replay: tasks start when their dependencies are done (plus the coordinator's measured
    dispatch lag) and an agent is free; `cap` agents; node weight = the task's measured active seconds."""
    done: dict[str, float] = {}
    free = [0.0] * max(1, cap)
    todo = [t for t in order if t in w]
    while todo:
        ready = [t for t in todo if all(d in done for d in graph.get(t, []) if d in w)]
        t = min(ready, key=lambda x: (max([done[d] for d in graph.get(x, []) if d in done] + [0.0]), order.index(x)))
        dep_done = max([done[d] for d in graph.get(t, []) if d in done] + [0.0])
        i = min(range(len(free)), key=free.__getitem__)
        start = max(free[i], dep_done + (lag if graph.get(t) else 0.0))
        done[t] = free[i] = start + w[t]
        todo.remove(t)
    return max(done.values(), default=0.0)


def run_actuals(name: str, rep: dict) -> dict | None:
    """Measured wait and a validated replay. Node weights are ACTIVE seconds (build assignments plus rework), not
    start-to-done spans, which include idle time between resumed assignments. Only edges the run honoured (consumer
    started after its producer was done) are used: the plan file can differ from what was executed."""
    log = harness.RESULTS / f"{name}.jsonl"
    run_dir = harness.RUNS / name
    if not log.is_file() or not run_dir.is_dir():
        return None
    events = harness.read_events(log)
    if not events:
        return None
    disp = harness.dispatch_stats(events, run_dir)["dispatches"]
    tasks, _ = harness.mechanical_score(run_dir, events, disp)
    tasks = [t for t in tasks if t.get("start_s") is not None and t.get("done_s") is not None and t.get("duration_s")]
    if len(tasks) < 2:
        return None
    by = {t["task"]: t for t in tasks}
    first = min(t["start_s"] for t in tasks)
    end = max(t["done_s"] for t in tasks)
    graph = rep["graph"]
    honoured = {t: [d for d in graph.get(t, []) if d in by and by[t]["start_s"] >= by[d]["done_s"] - 1.0] for t in by}
    violated = [(d, t) for t in by for d in graph.get(t, []) if d in by and by[t]["start_s"] < by[d]["done_s"] - 1.0]
    w = {t: round(x["done_s"] - x["start_s"], 1) for t, x in by.items()}   # elapsed seconds: they validate far better than active seconds
    order = sorted(by, key=lambda t: by[t]["start_s"])
    marks = sorted([(x["start_s"], 1) for x in tasks] + [(x["done_s"], -1) for x in tasks], key=lambda m: (m[0], m[1]))
    cur = cap = 0
    for _, dlt in marks:
        cur += dlt
        cap = max(cap, cur)
    lags = sorted(t["start_lag_s"] for t in tasks if t.get("start_lag_s") is not None)
    lag = lags[len(lags) // 2] if lags else 0.0
    removable = {(e["producer"], e["consumer"]) for e in rep["edge_list"] if e["class"] == "removable"}
    pruned = {t: [d for d in ds if (d, t) not in removable] for t, ds in honoured.items()}
    span = end - first
    r0, r1 = replay(honoured, w, cap, lag, order), replay(pruned, w, cap, lag, order)
    err = abs(r0 - span) / span if span else 0.0
    saving = r0 - r1
    return {
        "tasks_built": len(tasks), "plan_tasks": rep["tasks"], "complete": all(t.get("complete") for t in tasks),
        "actual_span_s": round(span, 1), "agents_cap": cap, "median_start_lag_s": round(lag, 1),
        "replay_unpruned_span_s": round(r0, 1), "replay_error_pct": round(100 * err, 1),
        "replay_pruned_span_s": round(r1, 1), "saving_s": round(saving, 1),
        "saving_range_s": [round(max(0.0, saving - err * span), 1), round(saving + err * span, 1)],
        "edges_not_honoured": violated, "removable_edges_in_run": sorted(removable & {(d, t) for t, ds in honoured.items() for d in ds}),
        "active_s_by_task": w, "predicted_min_by_task": {t: rep["times"].get(t) for t in by},
        "predicted_build_min": rep["base"]["build_min"],
        "critical_path_tasks": [t["task"] for t in tasks if t.get("on_critical_path")],
        "slack_s": {t["task"]: t.get("slack_s") for t in tasks},
    }


def rulings(name: str) -> dict | None:
    """`Ruling: sequential` lines in the first plan draft versus added after it, from Write/Edit calls in the transcript."""
    log = harness.RESULTS / f"{name}.jsonl"
    if not log.is_file():
        return None
    cur = first = None
    added = removed = 0
    seen: set[str] = set()
    for e in harness.read_events(log):
        if e.get("type") != "assistant" or e.get("parent_tool_use_id"):
            continue
        for b in (e.get("message") or {}).get("content") or []:
            if b.get("type") != "tool_use" or b.get("name") not in ("Write", "Edit") or b.get("id") in seen:
                continue
            seen.add(b.get("id"))
            inp = b.get("input") or {}
            if not re.search(r"docs/plans/[^/]+\.md$", str(inp.get("file_path", "")).replace("\\", "/")):
                continue
            if b["name"] == "Write":
                n = len(SEQ.findall(inp.get("content") or ""))
                if first is None:
                    first = cur = n
                else:
                    added, removed = added + max(0, n - cur), removed + max(0, cur - n)
                    cur = n
            elif cur is not None:
                d = len(SEQ.findall(inp.get("new_string") or "")) - len(SEQ.findall(inp.get("old_string") or ""))
                added, removed, cur = added + max(0, d), removed + max(0, -d), cur + d
    if first is None:
        return None
    return {"first_draft": first, "added_after": added, "removed_after": removed, "final": cur}


def rulings_in_file(run_dir: Path) -> int | None:
    pp = final_plan(run_dir)
    return len(SEQ.findall(pp.read_text(encoding="utf-8-sig", errors="replace"))) if pp else None


# ---- report --------------------------------------------------------------------------------------
def final_plan(run_dir: Path) -> Path | None:
    plans = sorted((run_dir / "docs" / "plans").glob("*.md"), key=lambda p: (p.stat().st_mtime, p.name))
    withg = [p for p in plans if re.search(r"^\s*%%\s*taskgraph\b", p.read_text(encoding="utf-8-sig", errors="replace"), re.M)]
    return (withg or [None])[-1]


def audit(run_dirs: list[Path]) -> list[dict]:
    out = []
    for d in run_dirs:
        pp = final_plan(d)
        row = {"run": d.name, "plan": None, "actual": None, "rulings": rulings(d.name), "rulings_in_file": rulings_in_file(d)}
        if pp is not None:
            rep = plan_report(pp)
            row["plan"] = rep
            row["actual"] = run_actuals(d.name, rep) if rep else None
        out.append(row)
    return out


def md(rows: list[dict]) -> str:
    L = ["# Build-shape audit (generated by bench/build_shape_audit.py)", "",
         "Heuristic and assumptions are in the script header. Predicted minutes come from `sl plan_tools schedule` on copies of each plan; "
         "'actual' numbers come from the run's transcript and per-task records. Savings are upper bounds with the error range stated beside them.", "",
         "## 1. Plans: chain, width and edge classes", "",
         "| run | tasks | edges | longest chain (tasks) | predicted cp min | peak width | removable | missing Produces | ordering | real (ruling) | cp min pruned (removable) | build min pruned | cp min pruned (+contract examples) | cp min if multi-component split* |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        p = r["plan"]
        if not p:
            L.append(f"| {r['run']} | no plan with a taskgraph | | | | | | | | | | | | |")
            continue
        c = p["edge_classes"]
        L.append(f"| {r['run']} | {p['tasks']} | {p['edges']} | {p['chain_tasks']} | {p['base']['critical_path_min']} | {p['base']['peak_width']} | "
                 f"{c['removable']} | {c['missing_produces']} | {c['ordering']} | {c['real_ruling']} | {p['pruned_removable']['critical_path_min']} | {p['pruned_removable']['build_min']} "
                 f"(was {p['base']['build_min']}) | {p['pruned_removable_and_missing']['critical_path_min']} | {p['split_multi_component']['critical_path_min_uncapped']} "
                 f"(was {p['split_multi_component']['baseline_uncapped_min']}) |")
    L += ["", "*uncapped longest path of predicted minutes with tasks of more than 2 components split into equal parallel parts.", ""]
    L += ["", "## 2. Real runs: validated replay (error range stated)", "",
          "The replay re-runs each run's MEASURED active task seconds over the edges the run honoured, with the run's observed agent cap and median "
          "dispatch lag. It is first run on the unpruned graph and compared with the real build span: that error is the error bar on the saving.", "",
          "| run | tasks built / plan | actual span s | replay (unpruned) s | replay error | agent cap | replay pruned s | saving s (range) | edges not honoured | note |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        a = r["actual"]
        if not a:
            L.append(f"| {r['run']} | no transcript or no task records | | | | | | | | |")
            continue
        L.append(f"| {r['run']} | {a['tasks_built']} / {a['plan_tasks']} | {a['actual_span_s']} | {a['replay_unpruned_span_s']} | {a['replay_error_pct']}% | {a['agents_cap']} | "
                 f"{a['replay_pruned_span_s']} | **{a['saving_s']}** ({a['saving_range_s'][0]} to {a['saving_range_s'][1]}) | {len(a['edges_not_honoured'])} "
                 f"{a['edges_not_honoured'] or ''} | {'' if a['complete'] else 'killed or unfinished: partial'} |")
    cal = [(r["run"], r["plan"]["base"]["build_min"] * 60, r["actual"]["actual_span_s"]) for r in rows if r["plan"] and r["actual"] and r["actual"]["complete"]]
    L += ["", "### Prediction error (why section 1's what-if is only a range)", "",
          "| run | predicted build s | actual span s | actual / predicted |", "|---|---|---|---|"]
    ratios = []
    for run, pred, act in cal:
        ratios.append(act / pred)
        L.append(f"| {run} | {pred:.0f} | {act} | {act / pred:.2f} |")
    if ratios:
        L.append(f"\nOver {len(ratios)} complete runs the real span is {min(ratios):.2f} to {max(ratios):.2f} times the prediction. "
                 f"Section 1 what-if minutes should be read as predicted x [{min(ratios):.2f}, {max(ratios):.2f}]; the replay in the table above "
                 f"is the tighter figure where a run exists.")

    L += ["", "## 3. `Ruling: sequential` lines: first plan draft vs added after lint", "",
          "| run | in first draft | added later (Write/Edit) | removed later | final per transcript | final in plan file | added by other means |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        g = r["rulings"]
        inf = r["rulings_in_file"]
        L.append(f"| {r['run']} | " + (" | ".join(str(g[k]) for k in ("first_draft", "added_after", "removed_after", "final")) +
                 f" | {inf} | {max(0, (inf or 0) - g['final'])} (shell or other edits the transcript shows no Write/Edit for)"
                 if g else f"no plan Write in transcript | | | | {inf} | ") + " |")
    L += ["", "## 4. Which fix would have changed which run", "",
          "| run | removable-edge pruning: predicted cp / build min | + contract examples: predicted cp min | edge-specific chain rulings (ruled edges that pass the removable test are pruned too): predicted cp / build min | splitting multi-component tasks: predicted cp min | replay saving from pruning, s (range) |",
          "|---|---|---|---|---|---|"]
    for r in rows:
        p, g, a = r["plan"], r["rulings"], r["actual"]
        if not p:
            continue
        b = p["base"]
        d1 = round(b["critical_path_min"] - p["pruned_removable"]["critical_path_min"], 1)
        d2 = round(b["critical_path_min"] - p["pruned_removable_and_missing"]["critical_path_min"], 1)
        sp = p["split_multi_component"]
        d4 = round(sp["baseline_uncapped_min"] - sp["critical_path_min_uncapped"], 1)
        n_r = max(r["rulings_in_file"] or 0, g["final"] if g else 0)
        pe = p["pruned_edge_specific_rulings"]
        rul = (f"{n_r} ruling(s) hold {p['edge_classes']['real_ruling']} edge(s), {p['ruled_but_removable']} of them removable: "
               f"{b['critical_path_min'] - pe['critical_path_min']:+.1f} cp (build {b['build_min'] - pe['build_min']:+.1f}) beyond plain pruning "
               f"{b['critical_path_min'] - p['pruned_removable']['critical_path_min']:+.1f}" if n_r else "no sequential ruling: no change")
        L.append(f"| {r['run']} | {d1:+.1f} cp (build {b['build_min'] - p['pruned_removable']['build_min']:+.1f}) | {d2:+.1f} cp | {rul} | {d4:+.1f} cp ({len(sp['tasks_split'])} task(s) split) | "
                 f"{(str(a['saving_s']) + ' (' + str(a['saving_range_s'][0]) + ' to ' + str(a['saving_range_s'][1]) + ')') if a else '-'} |")
    L += ["", "Reading the table: a positive number is minutes (or seconds in the last column) the fix could take off, as a prediction or upper bound. "
          "Zero means the fix would not have changed that run's critical path. The edge-specific column prunes, besides plain removable edges, the ruled edges whose "
          "items all have Produces lines; rulings are mapped to edges by task ids or shared package name (see script header), so it is a prediction under that mapping."]
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runs", type=Path, default=harness.RUNS)
    ap.add_argument("--only", help="comma-separated run names (default: combo-superlite v/w/x, combo-live z)")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    names = a.only.split(",") if a.only else sorted(
        d.name for d in a.runs.iterdir() if re.fullmatch(r"combo-(superlite-[vwxy]\d+|live-z\d+)", d.name))
    rows = audit([a.runs / n for n in names])
    text = md(rows)
    if a.write:
        (harness.RESULTS / "build-shape-audit.md").write_text(text, encoding="utf-8")
        slim = [{k: (v if k != "plan" else {kk: vv for kk, vv in (v or {}).items() if kk not in ("graph", "times")}) for k, v in r.items()} for r in rows]
        (harness.RESULTS / "build-shape-audit.json").write_text(json.dumps(slim, indent=1), encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
