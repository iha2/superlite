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
# Complexity notation:
#   n = tasks, e = edges, M = metrics lines
import argparse  # O(1)
import json  # O(1)
import re  # O(1)
import sys  # O(1)
import time  # O(1)
from pathlib import Path  # O(1)

sys.path.insert(0, str(Path(__file__).resolve().parent))  # O(1)

WARN_AT = 0.70  # O(1)


def header_budget(plan) -> float | None:  # O(C)
    m = re.search(r"^\*\*Budget-usd:\*\*\s*\$?\s*(\d+(?:\.\d+)?)", plan.text, re.M)  # O(C)
    return float(m.group(1)) if m else None  # O(1)


def committed(sl: Path) -> set:  # O(ledger)
    f = sl / "progress.md"  # O(1)
    if not f.is_file():  # O(1)
        return set()  # O(1)
    from checkpoint import LEDGER_LINE  # O(1) lazy: the one ledger regex (checkpoint or old commit lines)
    return {m.group(1) for ln in f.read_text(encoding="utf-8", errors="replace").splitlines() if (m := LEDGER_LINE.match(ln))}  # O(ledger)


def compute(plan_path, sl_dir=".superlite", now=None) -> dict:  # O(M + B + n*(n + e))
    import history  # O(1)
    import metrics  # O(1)
    import plan_tools as pt  # O(1)
    sl = Path(sl_dir)  # O(1)
    plan = pt.Plan.load(str(plan_path))  # O(L)
    rows = metrics.task_records(sl, plan_path)  # O(M + B)
    agents = metrics.agent_records(sl)  # O(M + B)
    starts = [a["first_abs"] if a.get("first_abs") else a["start_ms"] / 1000.0 for a in agents if a.get("first_abs") or a["start_ms"]]  # O(A)
    now = now if now is not None else time.time()  # O(1)
    elapsed = max(0.0, now - min(starts)) if starts else 0.0  # O(1)
    co = metrics.coordinator_record(sl)  # O(B) the main session's own spend
    agents_usd = metrics.agents_cost(sl)  # O(M + B) every agent segment, mapped to a task or not
    co_usd = co["cost_usd"] if co else 0.0  # O(1)
    spent = agents_usd + co_usd  # coordinator + agents | O(1)
    args = pt.build_parser().parse_args(["schedule", "--plan", str(plan_path)])  # O(1)
    rep = pt.schedule_report(plan, args)  # O(n*(n + e))
    graph, pred = rep["graph"], dict(rep["times"])  # O(n)
    stats = history.project_stats(sl)  # O(file)
    done = committed(sl) | {r["task"] for r in rows if r["status"] in ("DONE", "DONE_WITH_CONCERNS") and r["complete"]}  # O(n)
    inflight = {r["task"]: r for r in rows if r["task"] not in done and r["start_s"] is not None}  # O(n)
    flags = history.predicted_flags(plan)  # O(n*(n + e))
    for t, f in flags.items():  # history-backed minutes where a band has enough samples | O(n)
        task = plan.by_id.get(t)  # O(1)
        if not task:  # O(1)
            continue  # O(1)
        ft = task.explicit_features  # O(k*s)
        b = (stats.get("bands") or {}).get(history.band_of(ft.get("kind", "design"), ft.get("loc"), ft.get("tests")))  # O(1)
        if b and b.get("n", 0) >= 5 and b.get("mean_s"):  # O(1)
            pred[t] = b["mean_s"] / 60.0  # O(1)
    first0 = min((r["start_s"] for r in rows if r["start_s"] is not None), default=0.0)  # O(n)
    rem: dict[str, float] = {}  # remaining minutes of each unfinished task | O(1)
    for t in graph:  # O(n)
        if t in done:  # O(1)
            continue  # O(1)
        spent_min = ((elapsed - (inflight[t]["start_s"] - first0)) / 60.0) if t in inflight else 0.0  # O(1)
        rem[t] = max(0.1, pred.get(t, 0.0) - max(0.0, spent_min))  # O(1)
    best: dict[str, tuple] = {}  # longest remaining path ending at t: (minutes, path) | O(1)

    def path(t):  # O(deg) memoized
        if t not in best:  # O(1)
            ds = [path(d) for d in graph.get(t, []) if d in rem]  # O(deg)
            top = max(ds, default=(0.0, []))  # O(deg)
            best[t] = (top[0] + rem[t], top[1] + [t])  # O(len path)
        return best[t]  # O(1)

    crit = max((path(t) for t in rem), default=(0.0, []))  # O(n)
    projected = elapsed / 60.0 + crit[0]  # O(1)
    finished_cost = [r["cost_usd"] for r in rows if r["task"] in done and r["cost_usd"]]  # O(n)
    per_task = (sum(finished_cost) / len(finished_cost)) if finished_cost else (  # O(n)
        (stats.get("tiers") or {}).get("sonnet", {}).get("mean_cost"))
    left = [t for t in rem if t not in inflight]  # O(n)
    proj_cost = spent + (per_task or 0.0) * len(left) if per_task is not None or not left else None  # O(1)
    target, cap = plan.header_target(), header_budget(plan)  # O(C)
    out = {"coordinator_usd": round(co_usd, 3), "agents_usd": round(agents_usd, 3), "coordinator": co,  # O(1)
           "elapsed_min": round(elapsed / 60.0, 1), "cost_usd": round(spent, 3), "projected_min": round(projected, 1),
           "projected_usd": None if proj_cost is None else round(proj_cost, 3), "target_min": target, "cap_usd": cap,
           "late_path": crit[1], "done": len(done), "remaining": len(rem), "stats": f"n={stats.get('rows', 0)}"}  # O(1)
    out["pct_cap"] = round(max(spent, proj_cost or 0.0) / cap, 3) if cap else None  # O(1)
    out["state"] = ("over" if cap and spent > cap else "warn" if cap and (proj_cost or spent) >= WARN_AT * cap else "ok")  # O(1)
    out["hint"] = None  # O(1)
    if co and co["turns"] >= 30 and co["context_median"] >= 60_000 and spent and co_usd / spent >= 0.4:  # O(1)
        out["hint"] = (f"HINT: the coordinator is {100 * co_usd / spent:.0f}% of the cost ({co['turns']} turns, median context "  # O(1)
                       f"{co['context_median'] // 1000}k): fewer, larger turns (batch dispatches, no polling) cut it")
    out["late"] = bool(target and projected > target + 1e-9)  # O(1)
    return out  # O(1)


def line(b: dict) -> str:  # O(1)
    cap_txt = " of $%g cap" % b["cap_usd"] if b["cap_usd"] else ""  # O(1)
    s = (f"budget: coordinator ${b['coordinator_usd']:.2f} | agents ${b['agents_usd']:.2f} | total ${b['cost_usd']:.2f}{cap_txt}, "  # O(1)
         f"elapsed {b['elapsed_min']} min, projected finish {b['projected_min']} min")
    if b["target_min"] is not None:  # O(1)
        s += f" (target {b['target_min']:g}, {b['projected_min'] - b['target_min']:+.1f})"  # O(1)
    if b["projected_usd"] is not None:  # O(1)
        s += f", projected cost ${b['projected_usd']:.2f}"  # O(1)
    if b["cap_usd"]:  # O(1)
        s += f" ({100 * (b['pct_cap'] or 0):.0f}% of cap)"  # O(1)
    s += f" [stats {b['stats']}]"  # O(1)
    if b.get("hint"):  # O(1)
        s += " " + b["hint"]  # O(1)
    if b["state"] == "warn":  # O(1)
        s += " WARN: 70% of the cap reached"  # O(1)
    if b["state"] == "over":  # O(1)
        s += (" OVER CAP: start no optional work (extra review rounds, fix rounds beyond the first); "  # O(1)
              "finish the tasks in flight, checkpoint them, run finish")
    if b["late"] and b["late_path"]:  # O(1)
        s += f" LATE: critical path {' -> '.join(b['late_path'])}"  # O(1)
    return s  # O(1)


def maybe_print(plan_path, sl_dir=".superlite") -> None:  # O(compute)
    """For dispatch and lint: print the line only when it changed since the last time."""
    if not (Path(sl_dir) / "metrics.jsonl").is_file():  # nothing measured yet: nothing to say | O(1)
        return  # O(1)
    try:  # a missing metrics file or plan never breaks the caller | O(1)
        text = line(compute(plan_path, sl_dir))  # O(compute)
    except Exception:  # noqa: BLE001 | O(1)
        return  # O(1)
    st = Path(sl_dir) / "budget.state"  # O(1)
    key = re.sub(r"elapsed [\d.]+ min|projected finish [\d.]+ min|, [+-][\d.]+\)", "", text)  # clock drift alone is not a change | O(len)
    try:  # O(1)
        if st.is_file() and st.read_text(encoding="utf-8") == key:  # O(1)
            return  # O(1)
        st.parent.mkdir(parents=True, exist_ok=True)  # O(1)
        st.write_text(key, encoding="utf-8")  # O(1)
    except OSError:  # O(1)
        pass  # O(1)
    print(text)  # O(1)


def main(argv=None):  # O(compute)
    ap = argparse.ArgumentParser(description=__doc__.split(chr(10))[0])  # O(1)
    ap.add_argument("--plan", required=True)  # O(1)
    ap.add_argument("--dir", default=".superlite")  # O(1)
    ap.add_argument("--changed", action="store_true")  # O(1)
    ap.add_argument("--json", action="store_true")  # O(1)
    a = ap.parse_args(argv)  # O(1)
    if a.dir == ".superlite":  # O(1)
        try:  # O(1)
            import session as sl_session  # O(1)
            a.dir = sl_session.display_path(sl_session.state_dir(a.plan))  # O(S)
        except Exception:  # noqa: BLE001 | keep flat default | O(1)
            pass  # O(1)
    sys.stdout.reconfigure(encoding="utf-8")  # O(1)
    if a.changed:  # O(1)
        maybe_print(a.plan, a.dir)  # O(compute)
        return 0  # O(1)
    b = compute(a.plan, a.dir)  # O(compute)
    print(json.dumps(b) if a.json else line(b))  # O(1)
    return 0  # O(1)


if __name__ == "__main__":  # O(1)
    sys.exit(main())  # O(1)
