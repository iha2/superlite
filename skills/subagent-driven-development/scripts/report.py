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
Feature flow (`.superlite/feature.json` from `sl proto`): the prototype and the integration show as two parts, each with
wall-clock and agent cost, projected and actual, plus the prototype's cost as a share of the integration (cap 25%); the
prototype numbers come from its own build report (saved into feature.json at try-out and discard).
Prints a short summary (default) or the full text (--full); always writes the full report and json next to the plan (`<plan>.report.md`/`.json`, one pair per build) and a copy of the latest in <dir>. Stdlib only.
"""
# Complexity notation:
#   M = metrics lines, B = bytes of agent transcripts, n = tasks, e = dependency edges, I = incident lines
import argparse  # O(1)
import hashlib  # O(1)
import json  # O(1)
import sys  # O(1)
from datetime import datetime  # O(1)
from pathlib import Path  # O(1)

HERE = Path(__file__).resolve().parent  # O(1)
if str(HERE) not in sys.path:  # O(1)
    sys.path.insert(0, str(HERE))  # O(1)

import metrics  # noqa: E402  | O(1)
import plan_tools as pt  # noqa: E402  | O(1)
import predict  # noqa: E402  | O(1)

PROJECTION = "projection.json"  # O(1)
TOP_MISSES = 5  # O(1)
PROTO_CAP_PCT = 25  # the prototype's budget: a quarter of the integration estimate | O(1)


def _sha(path) -> str:  # O(file)
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]  # O(file)


def build_projection(plan_path) -> dict:  # O(n*(n + e) + n*M)
    """The forecast: per-task minutes and cost, run totals, wall-clock (CPM build minutes) and the critical path."""
    plan = pt.Plan.load(str(plan_path))  # O(L)
    rep = pt.schedule_report(plan, pt.build_parser().parse_args(["schedule", "--plan", "x"]))  # O(n*(n + e))
    pred = predict.predict_plan(plan, predict.load_models(), predict.load_coef())  # O(n*M)
    times = rep["times"]  # O(1)
    tasks = {t: {"pred_min": round(times[t], 3), "model": (pred["tasks"].get(t) or {}).get("model"),  # O(n)
                 "cost_usd": ((pred["tasks"].get(t) or {}).get("cost") or {}).get("total")} for t in times}
    return {"v": 1, "saved": datetime.now().isoformat(timespec="seconds"), "plan_sha": _sha(plan_path),  # O(1)
            "wall_min": round(rep["build_min"], 3), "critical_path": list(rep["critical_path"]),
            "critical_path_min": round(rep["critical_path_min"], 3), "peak_width": rep["peak_width"],
            "sum_task_min": round(sum(times.values()), 3), "cost_usd": pred["totals"]["cost_usd"]["total"],
            "agents": len(times), "tasks": tasks}


def save_projection(plan_path, sl_dir, force=False) -> Path | None:  # O(n*(n + e)) once, O(1) after
    """Write `.superlite/projection.json` once (the first successful stable lint); later calls keep the forecast."""
    out = Path(sl_dir) / PROJECTION  # O(1)
    if out.is_file() and not force:  # O(1)
        return None  # O(1)
    out.parent.mkdir(parents=True, exist_ok=True)  # O(1)
    out.write_text(json.dumps(build_projection(plan_path), indent=1), encoding="utf-8")  # O(n)
    return out  # O(1)


def load_projection(plan_path, sl_dir) -> tuple[dict | None, str]:  # O(file) saved; O(n*(n + e)) recomputed
    """(projection, note). The saved forecast first; else recomputed from the plan now, with a note."""
    p = Path(sl_dir) / PROJECTION  # O(1)
    try:  # O(1)
        saved = json.loads(p.read_text(encoding="utf-8"))  # O(file)
        note = "projection: fixed forecast saved at lint " + str(saved.get("saved", ""))  # O(1)
        if plan_path and Path(plan_path).is_file() and saved.get("plan_sha") not in (None, _sha(plan_path)):  # O(file)
            note += " (plan edited since)"  # O(1)
        return saved, note  # O(1)
    except (OSError, ValueError):  # O(1)
        pass  # O(1)
    try:  # O(1)
        return build_projection(plan_path), "projection: no saved forecast, recomputed from the plan as it is now"  # O(n*(n + e))
    except Exception as e:  # noqa: BLE001 | O(1)
        return None, f"projection: n/a ({e})"  # O(1)


def delta(proj, act) -> dict:  # O(1)
    """Absolute and percent difference actual - projected; None where the projection is missing or zero."""
    if proj is None or act is None:  # O(1)
        return {"abs": None, "pct": None}  # O(1)
    d = act - proj  # O(1)
    return {"abs": round(d, 4), "pct": round(100.0 * d / proj, 1) if proj else None}  # O(1)


def peak_concurrency(rows) -> int:  # O(n log n)
    ev = []  # O(1)
    for r in rows:  # O(n)
        if r["start_s"] is not None and r["done_s"] is not None:  # O(1)
            ev += [(r["start_s"], 1), (r["done_s"], -1)]  # O(1)
    cur = peak = 0  # O(1)
    for _, d in sorted(ev, key=lambda x: (x[0], x[1])):  # ends sort before starts at a tie | O(n log n)
        cur += d  # O(1)
        peak = max(peak, cur)  # O(1)
    return peak  # O(1)


def _read_json(p):  # O(file)
    try:  # O(1)
        return json.loads(Path(p).read_text(encoding="utf-8"))  # O(file)
    except (OSError, ValueError):  # O(1)
        return None  # O(1)


def feature_parts(sl_dir, headline) -> dict | None:  # O(file)
    """Feature flow only: {topic, prototype, integration, prototype_tasks, share_pct, cap_pct}; None without feature.json."""
    st = _read_json(Path(sl_dir) / "feature.json")  # O(file)
    if not isinstance(st, dict) or not st.get("topic"):  # O(1)
        return None  # O(1)
    import proto  # O(1)
    rep = proto.proto_report(st) or {}  # O(file)
    part = lambda h: {k: {"projected": (h.get(src) or {}).get("projected"), "actual": (h.get(src) or {}).get("actual")}  # noqa: E731 | O(1)
                      for k, src in (("wall_min", "wall_min"), ("cost_usd", "cost_usd_agents"))}
    pp, ip = part(rep.get("headline") or {}), part(headline)  # O(1)
    pc = pp["cost_usd"]["actual"] if pp["cost_usd"]["actual"] is not None else pp["cost_usd"]["projected"]  # O(1)
    ic = ip["cost_usd"]["actual"] or ip["cost_usd"]["projected"]  # O(1)
    return {"topic": st["topic"], "status": st.get("status"), "prototype": pp, "integration": ip,
            "prototype_tasks": rep.get("tasks"), "share_pct": round(100.0 * pc / ic, 1) if pc is not None and ic else None,
            "cap_pct": PROTO_CAP_PCT}  # O(1)


def feature_lines(f, indent="") -> list[str]:  # O(1)
    """Two lines, prototype then integration: time and cost, projected -> actual, and the prototype's share."""
    out = []  # O(1)
    for name in ("prototype", "integration"):  # O(1)
        p = f[name]  # O(1)
        w, k = p["wall_min"], p["cost_usd"]  # O(1)
        out.append(f"{indent}{name:12}time {_num(w['projected'], '.1f')} -> {_num(w['actual'], '.1f')} min, "
                   f"cost ${_num(k['projected'], '.2f')} -> ${_num(k['actual'], '.2f')}")  # O(1)
    if f["share_pct"] is not None:  # O(1)
        out[0] += f" ({f['share_pct']:.0f}% of the integration, cap {f['cap_pct']}%)"  # O(1)
    return out  # O(1)


def collect(plan_path, sl_dir) -> dict:  # O(M + B + n*(n + e))
    sl = Path(sl_dir)  # O(1)
    rows = metrics.task_records(sl, plan_path)  # O(M + B)
    co = metrics.coordinator_record(sl)  # O(B)
    proj, note = load_projection(plan_path, sl)  # O(n*(n + e))
    ptasks = (proj or {}).get("tasks") or {}  # O(1)
    mins = lambda r: (r["duration_s"] or 0) / 60.0  # noqa: E731 | O(1)
    done = [r["done_s"] for r in rows if r["done_s"] is not None]  # O(n)
    wall = round(max(done) / 60.0, 3) if done else None  # t0 is the first agent launch | O(n)
    agents_usd = sum(r["cost_usd"] for r in rows)  # O(n)
    rework_usd = sum(r["rework_cost"] or 0 for r in rows)  # O(n)
    coord_usd = co["cost_usd"] if co else None  # O(1)
    mix: dict[str, dict] = {}  # O(1)
    for r in rows:  # O(n)
        m = mix.setdefault(r["model"] or "unknown", {"tasks": 0, "cost_usd": 0.0})  # O(1)
        m["tasks"] += 1  # O(1)
        m["cost_usd"] += r["cost_usd"] + (r["rework_cost"] or 0)  # O(1)
    tests = _read_json(sl / "test-summary.json")  # O(file)
    import finish  # O(1)
    incidents = finish.open_incidents(sl / "incidents.jsonl")  # O(I)
    ftp = [r["first_time_pass"] for r in rows if r["first_time_pass"] is not None]  # O(n)
    per = []  # O(n)
    for r in rows:  # O(n)
        p = (ptasks.get(r["task"]) or {}).get("pred_min")  # O(1)
        per.append({"task": r["task"], "model": r["model"], "pred_min": p, "actual_min": round(mins(r), 3),
                    "delta": delta(p, mins(r)), "cost_usd": round(r["cost_usd"] + (r["rework_cost"] or 0), 6),
                    "first_time_pass": r["first_time_pass"]})  # O(1)
    per.sort(key=lambda x: (x["delta"]["abs"] is None, -abs(x["delta"]["abs"] or 0.0), x["task"]))  # biggest misses first | O(n log n)
    headline = {
        "wall_min": {"projected": (proj or {}).get("wall_min"), "actual": wall},
        "cost_usd_agents": {"projected": (proj or {}).get("cost_usd"), "actual": round(agents_usd + rework_usd, 6)},
        "task_min": {"projected": (proj or {}).get("sum_task_min"), "actual": round(sum(mins(r) for r in rows), 3)},
        "agents": {"projected": (proj or {}).get("agents"), "actual": len(metrics.agent_records(sl))},
    }  # O(1)
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
    }  # O(1)


def _num(v, fmt) -> str:  # O(1)
    return "n/a" if v is None else format(v, fmt)  # O(1)


def _delta(d, unit="", fmt="+.1f") -> str:  # O(1)
    if d["abs"] is None:  # O(1)
        return "n/a"  # O(1)
    return f"{format(d['abs'], fmt)}{unit} ({format(d['pct'], '+.0f') + '%' if d['pct'] is not None else 'n/a'})"  # O(1)


def takeaway(c) -> str:  # O(n)
    h, parts = c["headline"], []  # O(1)
    w = delta(h["wall_min"]["projected"], h["wall_min"]["actual"])["pct"]  # O(1)
    k = delta(h["cost_usd_agents"]["projected"], h["cost_usd_agents"]["actual"])["pct"]  # O(1)
    if w is not None:  # O(1)
        parts.append(f"ran {abs(w):.0f}% {'faster' if w < 0 else 'slower'} than projected" if round(w) else "ran on projection")  # O(1)
    if k is not None:  # O(1)
        parts.append(f"cost {abs(k):.0f}% {'under' if k < 0 else 'over'}" if round(k) else "cost on projection")  # O(1)
    miss = next((t for t in c["tasks"] if t["delta"]["abs"] is not None), None)  # O(n)
    if miss:  # O(1)
        parts.append(f"biggest miss {miss['task']}")  # O(1)
    return "; ".join(parts) if parts else "no projection to compare against"  # O(1)


def render(c) -> str:  # O(n)
    h, cs, q = c["headline"], c["cost_split"], c["quality"]  # O(1)
    out = ["# superlite build report", "", c["projection_note"], "",  # O(1)
           f"{'':24}{'Projected':>10}{'Actual':>10}  Delta"]  # O(1)
    for label, key, fmt, unit in (("wall-clock (min)", "wall_min", ".1f", ""), ("agent cost (USD, +rework)", "cost_usd_agents", ".2f", ""),
                                  ("task-minutes", "task_min", ".1f", ""), ("agents", "agents", "d", "")):  # O(1)
        v = h[key]  # O(1)
        out.append(f"{label:24}{_num(v['projected'], fmt):>10}{_num(v['actual'], fmt):>10}  "
                   + _delta(delta(v["projected"], v["actual"]), unit, "+.2f" if key == "cost_usd_agents" else "+.1f" if fmt != "d" else "+d"))  # O(1)
    if c.get("feature"):  # O(1)
        out += [f"feature flow ({c['feature']['topic']}): two builds"] + feature_lines(c["feature"], "  ")  # O(1)
    out.append(f"cost split: agents ${cs['agents_usd']:.2f}, rework ${cs['rework_usd']:.2f}, coordinator "
               f"{'n/a' if cs['coordinator_usd'] is None else '$%.2f' % cs['coordinator_usd']}, total ${cs['total_usd']:.2f}")  # O(1)
    cp = c["critical_path"]  # O(1)
    out.append(f"concurrency: peak {c['concurrency']['peak']} at once (projected {_num(c['concurrency']['projected_peak'], 'd')}); "
               f"critical path actual {' '.join(cp['actual']) or 'n/a'}; projected {' > '.join(cp['projected'] or []) or 'n/a'}"
               + (f" ({cp['projected_min']:.1f} min)" if cp["projected_min"] is not None else ""))  # O(1)
    out.append("model mix: " + (", ".join(f"{m} {v['tasks']} tasks ${v['cost_usd']:.2f}" for m, v in sorted(c["model_mix"].items())) or "n/a")
               + "".join(f", coordinator {m} ${v:.2f}" for m, v in (c["coordinator_cost_by_model"] or {}).items()))  # O(1)
    t = q["tests"]  # O(1)
    tline = f"tests {t.get('passed', '?')} passed, {t.get('failed', '?')} failed" if isinstance(t, dict) else "tests n/a (no test-summary.json)"  # O(1)
    out.append(f"quality: {tline}; first-time pass "
               + (f"{q['first_time_pass']}/{q['first_time_n']} ({100 * q['first_time_pass'] // q['first_time_n']}%)" if q["first_time_n"] else "n/a")
               + f"; rework dispatches {q['rework_dispatches']}; open incidents {q['open_incidents']}")  # O(1)
    out += ["", f"{'task':6}{'model':8}{'pred':>7}{'actual':>8}  {'delta':14}{'cost':>8}"]  # O(1)
    for r in c["tasks"][:TOP_MISSES]:  # O(1)
        out.append(f"{r['task']:6}{(r['model'] or '?'):8}{_num(r['pred_min'], '.1f'):>7}{r['actual_min']:>8.1f}  "
                   f"{_delta(r['delta']):14}{'$%.3f' % r['cost_usd']:>8}" + ("  first-time miss" if r["first_time_pass"] is False else ""))  # O(1)
    if len(c["tasks"]) > TOP_MISSES:  # O(1)
        out.append(f"(+{len(c['tasks']) - TOP_MISSES} smaller misses; `sl report --json` lists all)")  # O(1)
    out += ["", "takeaway: " + takeaway(c)]  # O(1)
    return "\n".join(out) + "\n"  # O(n)


def summary(c, path) -> str:  # O(n)
    """At most 10 lines: headline, the two feature-flow parts when there are any, tests and first-time pass, the biggest miss, the takeaway and the path of the full report."""
    h, q = c["headline"], c["quality"]  # O(1)
    out = ["build report (projected -> actual)"]  # O(1)
    for label, key, fmt, d in (("wall-clock min", "wall_min", ".1f", "+.1f"), ("agent cost USD", "cost_usd_agents", ".2f", "+.2f"),
                               ("agents", "agents", "d", "+d")):  # O(1)
        v = h[key]  # O(1)
        out.append(f"  {label:15}{_num(v['projected'], fmt):>7} -> {_num(v['actual'], fmt):>7}  {_delta(delta(v['projected'], v['actual']), '', d)}")  # O(1)
    if c.get("feature"):  # O(1)
        out += feature_lines(c["feature"], "  ")  # O(1)
    t = q["tests"]  # O(1)
    out.append(f"  tests {t.get('passed', '?')} passed, {t.get('failed', '?')} failed" if isinstance(t, dict) else "  tests n/a")  # O(1)
    out[-1] += ("; first-time pass " + (f"{q['first_time_pass']}/{q['first_time_n']}" if q["first_time_n"] else "n/a")
                + f"; rework {q['rework_dispatches']}; open incidents {q['open_incidents']}")  # O(1)
    miss = next((r for r in c["tasks"] if r["delta"]["abs"] is not None), None)  # O(n)
    if miss:  # O(1)
        out.append(f"  biggest miss {miss['task']}: predicted {_num(miss['pred_min'], '.1f')} actual {miss['actual_min']:.1f} min {_delta(miss['delta'])}")  # O(1)
    out += ["  takeaway: " + takeaway(c), f"Full report: {path}"]  # O(1)
    return "\n".join(out) + "\n"  # O(1)


def stored_paths(plan_path) -> tuple[Path, Path]:  # O(1)
    """`<plan dir>/<plan basename>.report.md` and `.report.json`: one pair per build, next to the archived plan."""
    p = Path(plan_path)  # O(1)
    return p.with_name(p.stem + ".report.md"), p.with_name(p.stem + ".report.json")  # O(1)


def write_report(plan_path, sl_dir) -> tuple[dict, Path]:  # O(M + B + n*(n + e))
    """Collect and write the full report and its json next to the plan, plus a copy of the latest as `<dir>/report.md`
    and `report.json`; return (data, per-plan report.md path)."""
    c = collect(plan_path, sl_dir)  # O(M + B + n*(n + e))
    md, js = stored_paths(plan_path)  # O(1)
    text, data = render(c).encode("utf-8"), json.dumps(c, indent=1).encode("utf-8")  # O(n)
    for m, j in ((md, js), (Path(sl_dir) / "report.md", Path(sl_dir) / "report.json")):  # O(1) iterations
        m.parent.mkdir(parents=True, exist_ok=True)  # O(1)
        m.write_bytes(text)  # O(n)
        j.write_bytes(data)  # O(n)
    return c, md  # O(1)


def list_reports(plans_dir) -> str:  # O(R log R)
    """Stored reports under plans_dir, newest first: plan name, date, wall-clock and cost deltas."""
    found = sorted(Path(plans_dir).glob("*.report.json"), key=lambda f: -f.stat().st_mtime)  # O(R log R)
    out = []  # O(1)
    for f in found:  # O(R)
        c = _read_json(f) or {}  # O(file)
        h = c.get("headline") or {}  # O(1)
        w, k = (h.get(x) or {} for x in ("wall_min", "cost_usd_agents"))  # O(1)
        out.append(f"{f.name[:-len('.report.json')]}  {datetime.fromtimestamp(f.stat().st_mtime):%Y-%m-%d}  "
                   f"time {_delta(delta(w.get('projected'), w.get('actual')), '', '+.1f')}  "
                   f"cost {_delta(delta(k.get('projected'), k.get('actual')), '', '+.2f')}")  # O(1)
    return "\n".join(out) + "\n" if out else f"no stored reports in {plans_dir}\n"  # O(R)


def show_report(name, plans_dir, full=False) -> str:  # O(file)
    """A stored build's summary (or its full file); NAME is a plan name or basename, with or without extension."""
    stem = Path(name).name
    for suf in (".md", ".report", ".json"):  # O(1)
        stem = stem[:-len(suf)] if stem.endswith(suf) else stem  # O(1)
    md, js = stored_paths(Path(plans_dir) / (stem + ".md"))  # O(1)
    if not md.is_file() or not js.is_file():  # O(1)
        return f"report: no stored report for {stem} in {plans_dir}" + chr(10)  # O(1)
    return md.read_bytes().decode("utf-8") if full else summary(_read_json(js), md.as_posix())  # O(file)


def report_text(plan_path, sl_dir) -> str:  # O(M + B + n*(n + e))
    """The summary block (what finish prints); the full report is saved next to it."""
    c, md = write_report(plan_path, sl_dir)  # O(M + B + n*(n + e))
    return summary(c, md.as_posix())  # O(n)


def main(argv=None):  # O(M + B + n*(n + e))
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])  # O(1)
    ap.add_argument("--plan", help="the plan of the build to report on")  # O(1)
    ap.add_argument("--dir", default=".superlite", help="the .superlite folder itself")  # O(1)
    ap.add_argument("--full", action="store_true", help="print the full report instead of the summary")  # O(1)
    ap.add_argument("--json", action="store_true")  # O(1)
    ap.add_argument("--list", action="store_true", help="list the stored reports, newest first")  # O(1)
    ap.add_argument("--show", metavar="NAME", help="print a stored build's summary (with --full: its file)")  # O(1)
    ap.add_argument("--plans", default="docs/plans", help="where the plans and their stored reports are")  # O(1)
    a = ap.parse_args(argv)  # O(1)
    sys.stdout.reconfigure(encoding="utf-8")  # O(1)
    if a.list or a.show:  # O(1)
        sys.stdout.write(list_reports(a.plans) if a.list else show_report(a.show, a.plans, a.full))  # O(file)
        return 0  # O(1)
    if not a.plan:  # O(1)
        ap.error("--plan is required (or --list / --show NAME)")  # O(1)
    if not (Path(a.dir) / "metrics.jsonl").is_file():  # O(1)
        print(f"report: no metrics.jsonl in {a.dir} (--dir is the .superlite folder itself)", file=sys.stderr)  # O(1)
        return 2  # O(1)
    c, md = write_report(a.plan, a.dir)  # O(M + B)
    sys.stdout.write(json.dumps(c, indent=1) + "\n" if a.json else render(c) if a.full else summary(c, md.as_posix()))  # O(n)
    return 0  # O(1)


if __name__ == "__main__":  # O(1)
    sys.exit(main())  # O(1)
