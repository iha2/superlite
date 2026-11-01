#!/usr/bin/env python3
"""One line of run-time control: elapsed, cost so far, projected finish and cost against target and cap.

    budget.py --plan P [--dir .superlite] [--changed] [--json]

Reads `.superlite/metrics.jsonl` (via metrics.py), the ledger's checkpoints and the plan header
(`**Target-min:** 11`, optional `**Budget-usd:** 3.00`). Projection: elapsed + the longest remaining path
through unfinished tasks (predicted minutes from `schedule`, history-backed when stats exist, minus time
already spent on in-flight tasks); cost: spent + mean cost of finished tasks (else the history band mean)
per unfinished task. Soft controls only: at 70% of the cap it warns; over the cap it says to stop optional
work (extra review rounds, fix rounds beyond the first) and names the next step; when the projected time
overruns the target it names the late critical-path task. `--changed` prints only when the line differs
from the last printed one (`.superlite/budget.state`), for dispatch and lint. Always exit 0. Stdlib only.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import state  # noqa: E402

WARN_AT = 0.70


def header_budget(plan) -> float | None:  # O(C)
    m = re.search(r"^\*\*Budget-usd:\*\*\s*\$?\s*(\d+(?:\.\d+)?)", plan.text, re.M)
    return float(m.group(1)) if m else None


def checkpointed(sl: Path) -> set:  # O(ledger)
    f = sl / "progress.md"
    if not f.is_file():
        return set()
    from checkpoint import LEDGER_LINE
    return {m.group(1) for ln in f.read_text(encoding="utf-8", errors="replace").splitlines() if (m := LEDGER_LINE.match(ln))}


def compute(plan_path, sl_dir=state.DIR_NAME, now=None) -> dict:  # O(M + B + n*(n + e))
    import history
    import metrics
    import plan_tools as pt
    sl = Path(sl_dir)
    plan = pt.Plan.load(str(plan_path))
    rows = metrics.task_records(sl, plan_path)
    agents = metrics.agent_records(sl)
    starts = [a["first_abs"] if a.get("first_abs") else a["start_ms"] / 1000.0 for a in agents if a.get("first_abs") or a["start_ms"]]
    now = now if now is not None else time.time()
    elapsed = max(0.0, now - min(starts)) if starts else 0.0
    co = metrics.coordinator_record(sl)
    agents_usd = metrics.agents_cost(sl)
    co_usd = co["cost_usd"] if co else 0.0
    spent = agents_usd + co_usd  # coordinator + agents
    args = pt.build_parser().parse_args(["schedule", "--plan", str(plan_path)])
    rep = pt.schedule_report(plan, args)
    graph, pred = rep["graph"], dict(rep["times"])
    stats = history.project_stats(sl)
    done = checkpointed(sl) | {r["task"] for r in rows if r["status"] in ("DONE", "DONE_WITH_CONCERNS") and r["complete"]}
    inflight = {r["task"]: r for r in rows if r["task"] not in done and r["start_s"] is not None}
    flags = history.predicted_flags(plan)
    for t, f in flags.items():  # history-backed minutes where a band has enough samples
        task = plan.by_id.get(t)
        if not task:
            continue
        ft = task.explicit_features
        b = (stats.get("bands") or {}).get(history.band_of(ft.get("kind", "design"), ft.get("loc"), ft.get("tests")))
        if b and b.get("n", 0) >= 5 and b.get("mean_s"):
            pred[t] = b["mean_s"] / 60.0
    first0 = min((r["start_s"] for r in rows if r["start_s"] is not None), default=0.0)
    rem: dict[str, float] = {}  # remaining minutes of each unfinished task
    for t in graph:
        if t in done:
            continue
        spent_min = ((elapsed - (inflight[t]["start_s"] - first0)) / 60.0) if t in inflight else 0.0
        rem[t] = max(0.1, pred.get(t, 0.0) - max(0.0, spent_min))
    best: dict[str, tuple] = {}  # longest remaining path ending at t: (minutes, path)

    def path(t):  # O(deg) memoized
        if t not in best:
            ds = [path(d) for d in graph.get(t, []) if d in rem]
            top = max(ds, default=(0.0, []))
            best[t] = (top[0] + rem[t], top[1] + [t])
        return best[t]

    crit = max((path(t) for t in rem), default=(0.0, []))
    projected = elapsed / 60.0 + crit[0]
    finished_cost = [r["cost_usd"] for r in rows if r["task"] in done and r["cost_usd"]]
    per_task = (sum(finished_cost) / len(finished_cost)) if finished_cost else (
        (stats.get("tiers") or {}).get("sonnet", {}).get("mean_cost"))
    left = [t for t in rem if t not in inflight]
    proj_cost = spent + (per_task or 0.0) * len(left) if per_task is not None or not left else None
    target, cap = plan.header_target(), header_budget(plan)
    out = {"coordinator_usd": round(co_usd, 3), "agents_usd": round(agents_usd, 3), "coordinator": co,
           "elapsed_min": round(elapsed / 60.0, 1), "cost_usd": round(spent, 3), "projected_min": round(projected, 1),
           "projected_usd": None if proj_cost is None else round(proj_cost, 3), "target_min": target, "cap_usd": cap,
           "late_path": crit[1], "done": len(done), "remaining": len(rem), "stats": f"n={stats.get('rows', 0)}"}
    out["pct_cap"] = round(max(spent, proj_cost or 0.0) / cap, 3) if cap else None
    out["state"] = ("over" if cap and spent > cap else "warn" if cap and (proj_cost or spent) >= WARN_AT * cap else "ok")
    out["hint"] = None
    if co and co["turns"] >= 30 and co["context_median"] >= 60_000 and spent and co_usd / spent >= 0.4:
        out["hint"] = (f"HINT: the coordinator is {100 * co_usd / spent:.0f}% of the cost ({co['turns']} turns, median context "
                       f"{co['context_median'] // 1000}k): fewer, larger turns (batch dispatches, no polling) cut it")
    out["late"] = bool(target and projected > target + 1e-9)
    return out


def line(b: dict) -> str:
    cap_txt = " of $%g cap" % b["cap_usd"] if b["cap_usd"] else ""
    s = (f"budget: coordinator ${b['coordinator_usd']:.2f} | agents ${b['agents_usd']:.2f} | total ${b['cost_usd']:.2f}{cap_txt}, "
         f"elapsed {b['elapsed_min']} min, projected finish {b['projected_min']} min")
    if b["target_min"] is not None:
        s += f" (target {b['target_min']:g}, {b['projected_min'] - b['target_min']:+.1f})"
    if b["projected_usd"] is not None:
        s += f", projected cost ${b['projected_usd']:.2f}"
    if b["cap_usd"]:
        s += f" ({100 * (b['pct_cap'] or 0):.0f}% of cap)"
    s += f" [stats {b['stats']}]"
    if b.get("hint"):
        s += " " + b["hint"]
    if b["state"] == "warn":
        s += " WARN: 70% of the cap reached"
    if b["state"] == "over":
        s += (" OVER CAP: start no optional work (extra review rounds, fix rounds beyond the first); "
              "finish the tasks in flight, checkpoint them, run finish")
    if b["late"] and b["late_path"]:
        s += f" LATE: critical path {' -> '.join(b['late_path'])}"
    return s


def maybe_print(plan_path, sl_dir=state.DIR_NAME) -> None:  # O(compute)
    """For dispatch and lint: print the line only when it changed since the last time."""
    if not (Path(sl_dir) / "metrics.jsonl").is_file():  # nothing measured yet: nothing to say
        return
    import planfile
    import predict
    try:  # a missing metrics file or plan never breaks the caller
        text = line(compute(plan_path, sl_dir))
    except (OSError, ValueError, KeyError, TypeError, ArithmeticError, planfile.PlanError, predict.PredictError):
        return
    st = Path(sl_dir) / "budget.state"
    key = re.sub(r"elapsed [\d.]+ min|projected finish [\d.]+ min|, [+-][\d.]+\)", "", text)  # clock drift alone is not a change
    try:
        if st.is_file() and st.read_text(encoding="utf-8") == key:
            return
        st.parent.mkdir(parents=True, exist_ok=True)
        st.write_text(key, encoding="utf-8")
    except OSError:
        pass
    print(text)


def main(argv=None):  # O(compute)
    ap = argparse.ArgumentParser(description=__doc__.split(chr(10))[0])
    ap.add_argument("--plan", required=True)
    ap.add_argument("--dir", default=state.DIR_NAME)
    ap.add_argument("--changed", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    if a.changed:
        maybe_print(a.plan, a.dir)
        return 0
    b = compute(a.plan, a.dir)
    print(json.dumps(b) if a.json else line(b))
    return 0


if __name__ == "__main__":
    sys.exit(main())
