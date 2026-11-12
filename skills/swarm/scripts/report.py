#!/usr/bin/env python3
"""End-of-build report: actual metrics next to the projected ones.

    report.py --plan PLAN.md [--dir .superlite] [--full | --json]
    report.py --list [--plans docs/plans]  |  --show NAME [--full]

Actual: metrics.task_records / coordinator_record (agent transcripts), test-summary.json, incidents.jsonl.
Projected: `.superlite/projection.json`, saved by `plan_tools lint --stable` (a fixed forecast); without it the
projection is recomputed from the plan as it is now, and the report says so. Projected wall-clock is the
critical path of the task graph under the predictor's estimates (plan_tools.schedule_report: parse_taskgraph +
effective_times, unlimited-lane CPM build minutes); the closing test tail is not counted, as actual wall-clock
runs from the first agent launch to the last finished task or fix. Anything with no projection prints `n/a`.
Feature flow (`.superlite/feature.json` from `superlite-cli proto`): the prototype and the integration show as two parts, each with
wall-clock and agent cost, projected and actual, plus the prototype's cost as a share of the integration (cap 25%); the
prototype numbers come from its own build report (saved into feature.json at try-out and discard).
Prints a short summary (default) or the full text (--full); always writes the full report and json next to the plan (`<plan>.report.md`/`.json`, one pair per build) and a copy of the latest in <dir>. Stdlib only.
"""
import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import base  # noqa: E402
import cost  # noqa: E402
import metrics  # noqa: E402
import plan_tools as pt  # noqa: E402
import planfile  # noqa: E402
import predict  # noqa: E402
import state  # noqa: E402

PROJECTION = "projection.json"
TOP_MISSES = 5
PROTO_CAP_PCT = 25  # the prototype's budget: a quarter of the integration estimate


def _sha(path) -> str:  # O(file)
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def build_projection(plan_path) -> dict:  # O(n*(n + e) + n*M)
    """The forecast: per-task minutes and cost, run totals, wall-clock (CPM build minutes) and the critical path."""
    plan = pt.Plan.load(str(plan_path))
    rep = pt.schedule_report(plan, pt.build_parser().parse_args(["schedule", "--plan", "x"]))
    pred = predict.predict_plan(plan, predict.load_models(), predict.load_coef())
    times = rep["times"]
    tasks = {t: {"pred_min": round(times[t], 3), "model": (pred["tasks"].get(t) or {}).get("model"),
                 "cost_usd": ((pred["tasks"].get(t) or {}).get("cost") or {}).get("total")} for t in times}
    return {"v": 1, "saved": datetime.now().isoformat(timespec="seconds"), "plan_sha": _sha(plan_path),
            "wall_min": round(rep["build_min"], 3), "critical_path": list(rep["critical_path"]),
            "critical_path_min": round(rep["critical_path_min"], 3), "peak_width": rep["peak_width"],
            "sum_task_min": round(sum(times.values()), 3), "cost_usd": pred["totals"]["cost_usd"]["total"],
            "agents": len(times), "tasks": tasks}


def save_projection(plan_path, sl_dir, force=False) -> Path | None:  # O(n*(n + e)) once, O(1) after
    """Write `.superlite/projection.json` once (the first successful stable lint); later calls keep the forecast."""
    out = Path(sl_dir) / PROJECTION
    if out.is_file() and not force:
        return None
    base.write_json(out, build_projection(plan_path))
    return out


def load_projection(plan_path, sl_dir) -> tuple[dict | None, str]:  # O(file) saved; O(n*(n + e)) recomputed
    """(projection, note). The saved forecast first; else recomputed from the plan now, with a note."""
    p = Path(sl_dir) / PROJECTION
    saved = base.read_json(p, None)
    if isinstance(saved, dict):
        note = "projection: fixed forecast saved at lint " + str(saved.get("saved", ""))
        if plan_path and Path(plan_path).is_file() and saved.get("plan_sha") not in (None, _sha(plan_path)):
            note += " (plan edited since)"
        return saved, note
    try:
        return build_projection(plan_path), "projection: no saved forecast, recomputed from the plan as it is now"
    except (OSError, ValueError, KeyError, TypeError, ArithmeticError, planfile.PlanError, predict.PredictError) as e:
        return None, f"projection: n/a ({e})"


def delta(proj, act) -> dict:
    """Absolute and percent difference actual - projected; None where the projection is missing or zero."""
    if proj is None or act is None:
        return {"abs": None, "pct": None}
    d = act - proj
    return {"abs": round(d, 4), "pct": round(100.0 * d / proj, 1) if proj else None}


def peak_concurrency(rows) -> int:  # O(n log n)
    ev = []
    for r in rows:
        if r["start_s"] is not None and r["done_s"] is not None:
            ev += [(r["start_s"], 1), (r["done_s"], -1)]
    cur = peak = 0
    for _, d in sorted(ev, key=lambda x: (x[0], x[1])):  # ends sort before starts at a tie
        cur += d
        peak = max(peak, cur)
    return peak


def _read_json(p):  # O(file)
    return base.read_json(Path(p), None)


def feature_parts(sl_dir, headline) -> dict | None:  # O(file)
    """Feature flow only: {topic, status, run_id, sandbox, prototype, integration, prototype_tasks, share_pct, cap_pct}; None without feature.json."""
    st = _read_json(Path(sl_dir) / "feature.json")
    if not isinstance(st, dict) or not st.get("topic"):
        return None
    import proto
    rep = proto.proto_report(st) or {}
    part = lambda h: {k: {"projected": (h.get(src) or {}).get("projected"), "actual": (h.get(src) or {}).get("actual")}  # noqa: E731
                      for k, src in (("wall_min", "wall_min"), ("cost_usd", "cost_usd_agents"))}
    pp, ip = part(rep.get("headline") or {}), part(headline)
    pc = pp["cost_usd"]["actual"] if pp["cost_usd"]["actual"] is not None else pp["cost_usd"]["projected"]
    ic = ip["cost_usd"]["actual"] or ip["cost_usd"]["projected"]
    import sandbox
    return {"topic": st["topic"], "status": st.get("status"), "run_id": st.get("run_id"), "sandbox": sandbox.open_sandbox(Path(sl_dir)),
            "prototype": pp, "integration": ip,
            "prototype_tasks": rep.get("tasks"), "share_pct": round(100.0 * pc / ic, 1) if pc is not None and ic else None,
            "cap_pct": PROTO_CAP_PCT}


def feature_lines(f, indent="") -> list[str]:
    """Two lines, prototype then integration: time and cost, projected -> actual, and the prototype's share."""
    out = []
    for name in ("prototype", "integration"):
        p = f[name]
        w, k = p["wall_min"], p["cost_usd"]
        out.append(f"{indent}{name:12}time {_num(w['projected'], '.1f')} -> {_num(w['actual'], '.1f')} min, "
                   f"cost ${_num(k['projected'], '.2f')} -> ${_num(k['actual'], '.2f')}")
    if f["share_pct"] is not None:
        out[0] += f" ({f['share_pct']:.0f}% of the integration, cap {f['cap_pct']}%)"
    return out


def collect(plan_path, sl_dir) -> dict:  # O(M + B + n*(n + e))
    sl = Path(sl_dir)
    rows = metrics.task_records(sl, plan_path)
    co = metrics.coordinator_record(sl)
    proj, note = load_projection(plan_path, sl)
    ptasks = (proj or {}).get("tasks") or {}
    mins = lambda r: (r["duration_s"] or 0) / 60.0  # noqa: E731
    done = [r["done_s"] for r in rows if r["done_s"] is not None]
    wall = round(max(done) / 60.0, 3) if done else None  # t0 is the first agent launch
    agents_usd = sum(r["cost_usd"] for r in rows)
    rework_usd = sum(r["rework_cost"] or 0 for r in rows)
    coord_usd = co["cost_usd"] if co else None
    mix: dict[str, dict] = {}
    for r in rows:
        m = mix.setdefault(r["model"] or "unknown", {"tasks": 0, "cost_usd": 0.0})
        m["tasks"] += 1
        m["cost_usd"] += r["cost_usd"] + (r["rework_cost"] or 0)
    tests = _read_json(sl / "test-summary.json")
    import finish
    incidents = finish.open_incidents(sl / "incidents.jsonl")
    ftp = [r["first_time_pass"] for r in rows if r["first_time_pass"] is not None]
    per = []
    for r in rows:
        p = (ptasks.get(r["task"]) or {}).get("pred_min")
        per.append({"task": r["task"], "model": r["model"], "pred_min": p, "actual_min": round(mins(r), 3),
                    "delta": delta(p, mins(r)), "cost_usd": round(r["cost_usd"] + (r["rework_cost"] or 0), 6),
                    "first_time_pass": r["first_time_pass"]})
    per.sort(key=lambda x: (x["delta"]["abs"] is None, -abs(x["delta"]["abs"] or 0.0), x["task"]))  # biggest misses first
    headline = {
        "wall_min": {"projected": (proj or {}).get("wall_min"), "actual": wall},
        "cost_usd_agents": {"projected": (proj or {}).get("cost_usd"), "actual": round(agents_usd + rework_usd, 6)},
        "task_min": {"projected": (proj or {}).get("sum_task_min"), "actual": round(sum(mins(r) for r in rows), 3)},
        "agents": {"projected": (proj or {}).get("agents"), "actual": len(metrics.agent_records(sl))},
    }
    return {
        "projection_note": note,
        "headline": headline,
        "feature": feature_parts(sl, headline),
        "cost_split": {"agents_usd": round(agents_usd, 6), "rework_usd": round(rework_usd, 6), "coordinator_usd": coord_usd,
                       "total_usd": round(agents_usd + rework_usd + (coord_usd or 0.0), 6)},
        "concurrency": {"peak": peak_concurrency(rows), "projected_peak": (proj or {}).get("peak_width")},
        "critical_path": {"actual": [r["task"] for r in rows if r.get("on_critical_path")],
                          "projected": (proj or {}).get("critical_path"), "projected_min": (proj or {}).get("critical_path_min")},
        "model_mix": mix, "coordinator_cost_by_model": (co or {}).get("cost_by_model"),
        "quality": {"tests": tests, "first_time_pass": sum(1 for x in ftp if x), "first_time_n": len(ftp),
                    "rework_dispatches": sum(r["rework_dispatches"] for r in rows), "open_incidents": len(incidents)},
        "tasks": per,
        "cost_table": cost.est_summary(sl),  # additive: est agent table / cost-summary.json
    }


def _num(v, fmt) -> str:
    return "n/a" if v is None else format(v, fmt)


def _delta(d, unit="", fmt="+.1f") -> str:
    if d["abs"] is None:
        return "n/a"
    return f"{format(d['abs'], fmt)}{unit} ({format(d['pct'], '+.0f') + '%' if d['pct'] is not None else 'n/a'})"


def takeaway(c) -> str:  # O(n)
    h, parts = c["headline"], []
    w = delta(h["wall_min"]["projected"], h["wall_min"]["actual"])["pct"]
    k = delta(h["cost_usd_agents"]["projected"], h["cost_usd_agents"]["actual"])["pct"]
    if w is not None:
        parts.append(f"ran {abs(w):.0f}% {'faster' if w < 0 else 'slower'} than projected" if round(w) else "ran on projection")
    if k is not None:
        parts.append(f"cost {abs(k):.0f}% {'under' if k < 0 else 'over'}" if round(k) else "cost on projection")
    miss = next((t for t in c["tasks"] if t["delta"]["abs"] is not None), None)
    if miss:
        parts.append(f"biggest miss {miss['task']}")
    return "; ".join(parts) if parts else "no projection to compare against"


def render(c) -> str:  # O(n)
    h, cs, q = c["headline"], c["cost_split"], c["quality"]
    out = ["# superlite build report", "", c["projection_note"], "",
           f"{'':24}{'Projected':>10}{'Actual':>10}  Delta"]
    for label, key, fmt, unit in (("wall-clock (min)", "wall_min", ".1f", ""), ("agent cost (USD, +rework)", "cost_usd_agents", ".2f", ""),
                                  ("task-minutes", "task_min", ".1f", ""), ("agents", "agents", "d", "")):
        v = h[key]
        out.append(f"{label:24}{_num(v['projected'], fmt):>10}{_num(v['actual'], fmt):>10}  "
                   + _delta(delta(v["projected"], v["actual"]), unit, "+.2f" if key == "cost_usd_agents" else "+.1f" if fmt != "d" else "+d"))
    if c.get("feature"):
        out += [f"feature flow ({c['feature']['topic']}): two builds"] + feature_lines(c["feature"], "  ")
    out.append(f"cost split: agents ${cs['agents_usd']:.2f}, rework ${cs['rework_usd']:.2f}, coordinator "
               f"{'n/a' if cs['coordinator_usd'] is None else '$%.2f' % cs['coordinator_usd']}, total ${cs['total_usd']:.2f}")
    cp = c["critical_path"]
    out.append(f"concurrency: peak {c['concurrency']['peak']} at once (projected {_num(c['concurrency']['projected_peak'], 'd')}); "
               f"critical path actual {' '.join(cp['actual']) or 'n/a'}; projected {' > '.join(cp['projected'] or []) or 'n/a'}"
               + (f" ({cp['projected_min']:.1f} min)" if cp["projected_min"] is not None else ""))
    out.append("model mix: " + (", ".join(f"{m} {v['tasks']} tasks ${v['cost_usd']:.2f}" for m, v in sorted(c["model_mix"].items())) or "n/a")
               + "".join(f", coordinator {m} ${v:.2f}" for m, v in (c["coordinator_cost_by_model"] or {}).items()))
    t = q["tests"]
    tline = f"tests {t.get('passed', '?')} passed, {t.get('failed', '?')} failed" if isinstance(t, dict) else "tests n/a (no test-summary.json)"
    out.append(f"quality: {tline}; first-time pass "
               + (f"{q['first_time_pass']}/{q['first_time_n']} ({100 * q['first_time_pass'] // q['first_time_n']}%)" if q["first_time_n"] else "n/a")
               + f"; rework dispatches {q['rework_dispatches']}; open incidents {q['open_incidents']}")
    out += ["", f"{'task':6}{'model':8}{'pred':>7}{'actual':>8}  {'delta':14}{'cost':>8}"]
    for r in c["tasks"][:TOP_MISSES]:
        out.append(f"{r['task']:6}{(r['model'] or '?'):8}{_num(r['pred_min'], '.1f'):>7}{r['actual_min']:>8.1f}  "
                   f"{_delta(r['delta']):14}{'$%.3f' % r['cost_usd']:>8}" + ("  first-time miss" if r["first_time_pass"] is False else ""))
    if len(c["tasks"]) > TOP_MISSES:
        out.append(f"(+{len(c['tasks']) - TOP_MISSES} smaller misses; `superlite-cli report --json` lists all)")
    if c.get("cost_table"):  # additive agent cost table
        ct = c["cost_table"]
        out += ["", "agent cost table:", "| agent | model | wall_min | tokens | usd |",
                "| --- | --- | --- | --- | --- |"]
        for r in ct.get("rows") or []:
            out.append(f"| {r['agent']} | {r['model']} | {r['wall_min']} | {r['tokens']} | {r['usd']} |")
        out.append(f"| totals |  | {ct.get('wall_min')} | {ct.get('tokens')} | {ct.get('usd')} |")
        out.append(f"cost source: {ct.get('source', 'est')}")
    out += ["", "takeaway: " + takeaway(c)]
    return "\n".join(out) + "\n"


def summary(c, path) -> str:  # O(n)
    """At most 10 lines: headline, the two feature-flow parts when there are any, tests and first-time pass, the biggest miss, the takeaway and the path of the full report."""
    h, q = c["headline"], c["quality"]
    out = ["build report (projected -> actual)"]
    for label, key, fmt, d in (("wall-clock min", "wall_min", ".1f", "+.1f"), ("agent cost USD", "cost_usd_agents", ".2f", "+.2f"),
                               ("agents", "agents", "d", "+d")):
        v = h[key]
        out.append(f"  {label:15}{_num(v['projected'], fmt):>7} -> {_num(v['actual'], fmt):>7}  {_delta(delta(v['projected'], v['actual']), '', d)}")
    if c.get("feature"):
        out += feature_lines(c["feature"], "  ")
    t = q["tests"]
    out.append(f"  tests {t.get('passed', '?')} passed, {t.get('failed', '?')} failed" if isinstance(t, dict) else "  tests n/a")
    out[-1] += ("; first-time pass " + (f"{q['first_time_pass']}/{q['first_time_n']}" if q["first_time_n"] else "n/a")
                + f"; rework {q['rework_dispatches']}; open incidents {q['open_incidents']}")
    miss = next((r for r in c["tasks"] if r["delta"]["abs"] is not None), None)
    if miss:
        out.append(f"  biggest miss {miss['task']}: predicted {_num(miss['pred_min'], '.1f')} actual {miss['actual_min']:.1f} min {_delta(miss['delta'])}")
    out += ["  takeaway: " + takeaway(c), f"Full report: {path}"]
    return "\n".join(out) + "\n"


def stored_paths(plan_path) -> tuple[Path, Path]:
    """`<plan dir>/<plan basename>.report.md` and `.report.json`: one pair per build, next to the archived plan."""
    p = Path(plan_path)
    return p.with_name(p.stem + ".report.md"), p.with_name(p.stem + ".report.json")


def write_report(plan_path, sl_dir) -> tuple[dict, Path]:  # O(M + B + n*(n + e))
    """Collect and write the full report and its json next to the plan, plus a copy of the latest as `<dir>/report.md`
    and `report.json`; return (data, per-plan report.md path)."""
    c = collect(plan_path, sl_dir)
    md, js = stored_paths(plan_path)
    text, data = render(c).encode("utf-8"), json.dumps(c, indent=1).encode("utf-8")
    for m, j in ((md, js), (Path(sl_dir) / "report.md", Path(sl_dir) / "report.json")):
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_bytes(text)
        j.write_bytes(data)
    return c, md


def list_reports(plans_dir) -> str:  # O(R log R)
    """Stored reports under plans_dir, newest first: plan name, date, wall-clock and cost deltas."""
    found = sorted(Path(plans_dir).glob("*.report.json"), key=lambda f: -f.stat().st_mtime)
    out = []
    for f in found:
        c = _read_json(f) or {}
        h = c.get("headline") or {}
        w, k = (h.get(x) or {} for x in ("wall_min", "cost_usd_agents"))
        out.append(f"{f.name[:-len('.report.json')]}  {datetime.fromtimestamp(f.stat().st_mtime):%Y-%m-%d}  "
                   f"time {_delta(delta(w.get('projected'), w.get('actual')), '', '+.1f')}  "
                   f"cost {_delta(delta(k.get('projected'), k.get('actual')), '', '+.2f')}")
    return "\n".join(out) + "\n" if out else f"no stored reports in {plans_dir}\n"


def show_report(name, plans_dir, full=False) -> str:  # O(file)
    """A stored build's summary (or its full file); NAME is a plan name or basename, with or without extension."""
    stem = Path(name).name
    for suf in (".md", ".report", ".json"):
        stem = stem[:-len(suf)] if stem.endswith(suf) else stem
    md, js = stored_paths(Path(plans_dir) / (stem + ".md"))
    if not md.is_file() or not js.is_file():
        return f"report: no stored report for {stem} in {plans_dir}" + chr(10)
    return md.read_bytes().decode("utf-8") if full else summary(_read_json(js), md.as_posix())


def report_text(plan_path, sl_dir) -> str:  # O(M + B + n*(n + e))
    """The summary block (what finish prints); the full report is saved next to it."""
    c, md = write_report(plan_path, sl_dir)
    return summary(c, md.as_posix())


def archived_metrics_dir(plan_path, sl_dir) -> Path | None:
    """Newest archived metrics for this checkout and plan, if the live run has been finished."""
    checkout = Path(sl_dir).resolve().parent
    try:
        import run_archive
        target = Path(plan_path).resolve()
        best = None
        for meta_path in run_archive.runs_dir(checkout).glob("*/run.json"):
            meta = base.read_json(meta_path, None)
            if not isinstance(meta, dict) or not isinstance(meta.get("checkout"), str) or not isinstance(meta.get("plan"), str):
                continue
            if Path(meta["checkout"]).resolve() != checkout:
                continue
            saved_plan = Path(meta["plan"])
            if saved_plan.resolve() != target and (checkout / saved_plan).resolve() != target:
                continue
            metrics_path = meta_path.parent / "metrics.jsonl"
            if not metrics_path.is_file():
                continue
            key = (str(meta.get("archived_at") or ""), str(meta.get("id") or ""))
            if best is None or key > best[0]:
                best = (key, meta_path.parent)
        return best[1] if best else None
    except (OSError, ValueError, base.GitError):
        return None


def main(argv=None):  # O(M + B + n*(n + e))
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--plan", help="the plan of the build to report on")
    ap.add_argument("--dir", default=state.DIR_NAME, help=f"the {state.DIR_NAME} folder itself")
    ap.add_argument("--full", action="store_true", help="print the full report instead of the summary")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--list", action="store_true", help="list the stored reports, newest first")
    ap.add_argument("--show", metavar="NAME", help="print a stored build's summary (with --full: its file)")
    ap.add_argument("--plans", default="docs/plans", help="where the plans and their stored reports are")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    if a.list or a.show:
        sys.stdout.write(list_reports(a.plans) if a.list else show_report(a.show, a.plans, a.full))
        return 0
    if not a.plan:
        ap.error("--plan is required (or --list / --show NAME)")
    sl_dir = Path(a.dir)
    if not (sl_dir / "metrics.jsonl").is_file():
        sl_dir = archived_metrics_dir(a.plan, sl_dir)
        if sl_dir is None:
            print(f"report: no metrics.jsonl in {a.dir} (--dir is the {state.DIR_NAME} folder itself)", file=sys.stderr)
            return 2
    c, md = write_report(a.plan, sl_dir)
    sys.stdout.write(json.dumps(c, indent=1) + "\n" if a.json else render(c) if a.full else summary(c, md.as_posix()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
