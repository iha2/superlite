#!/usr/bin/env python3
"""Persistent run history: one aggregate row per task per run, and stats derived by feature band.

    history.py write  --plan P [--dir .superlite] [--run ID]   rows of this run -> history, then derive
    history.py read   [--n 20] [--band B]                      last rows (JSON lines)
    history.py derive [--project .superlite]                   rebuild stats.json (global and project)
    history.py import FILE...                                  bench/results/*.json score files -> rows

Files: `$SUPERLITE_HOME/history.jsonl` (default `~/.superlite/`), append-only, plus `stats.json` there and a
per-project copy of both under `.superlite/`. Aggregates only: no code, prompts or file contents.

ROW SCHEMA (v1), one JSON object per line:
  v 1, date (YYYY-MM-DD), run (id), source ("run" | "import"), language (python, typescript, javascript,
  go, rust, csharp, java, ruby, unknown: from the repo's marker files), task (Tn),
  kind (mechanical|design), loc, components, tests (fast|slow), focus (sorted Review Focus categories
  of the task's package), band (see band_of), critical_path (bool; predicted for source=run, actual for
  imports), slack_min (predicted slack in minutes, or actual slack_s / 60 for imports),
  tier (haiku|sonnet), agent_type, model, prime_bytes, duration_s, tokens {in,out,cache_read,cache_write},
  cost_usd, tool_calls, first_time_pass (true|false|null), rework_s, rework_cost, pred_min.
STATS (stats.json): {v, updated, rows, bands: {band: S}, tiers: {tier: S}, bands_by_tier: {tier|band: S}} with
  S = {n, ftp_n, ftp_rate, mean_s, p90_s, mean_cost}. Same rows, same stats (sorted keys, no clock in S).
Stdlib only.
"""
# Complexity notation:
#   R = history rows, n = tasks of the run, w = width of a row
import argparse  # O(1)
import json  # O(1)
import math  # O(1)
import os  # O(1)
import sys  # O(1)
from datetime import date, datetime  # O(1)
from pathlib import Path  # O(1)

sys.path.insert(0, str(Path(__file__).resolve().parent))  # O(1)

MARKERS = [("tsconfig.json", "typescript"), ("package.json", "javascript"), ("go.mod", "go"), ("Cargo.toml", "rust"),  # O(1)
           ("pyproject.toml", "python"), ("setup.py", "python"), ("requirements.txt", "python"), ("pom.xml", "java"),
           ("build.gradle", "java"), ("Gemfile", "ruby")]
SUFFIX_MARKERS = [(".csproj", "csharp"), (".sln", "csharp")]  # O(1)


def home() -> Path:  # O(1)
    return Path(os.environ.get("SUPERLITE_HOME") or Path.home() / ".superlite")  # O(1)


def detect_language(root=".") -> str:  # O(entries of root)
    root = Path(root)  # O(1)
    for name, lang in MARKERS:  # O(markers)
        if (root / name).exists():  # O(1)
            return lang  # O(1)
    try:  # O(1)
        names = [p.name for p in root.iterdir()]  # O(entries)
    except OSError:  # O(1)
        return "unknown"  # O(1)
    for suf, lang in SUFFIX_MARKERS:  # O(1)
        if any(n.endswith(suf) for n in names):  # O(entries)
            return lang  # O(entries)
    return "unknown"  # O(1)


def band_of(kind, loc, tests) -> str:  # O(1)
    """kind/loc band/tests: `mechanical/le150/fast`. loc bands: le60, le150, le300, gt300, unk."""
    lb = "unk" if loc is None else ("le60" if loc <= 60 else "le150" if loc <= 150 else "le300" if loc <= 300 else "gt300")  # O(1)
    return f"{kind or 'design'}/{lb}/{tests or 'fast'}"  # O(1)


def read_rows(path=None) -> list:  # O(R*w)
    p = Path(path) if path else home() / "history.jsonl"  # O(1)
    out = []  # O(1)
    if p.is_file():  # O(1)
        for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():  # O(R)
            try:  # O(1)
                e = json.loads(ln)  # O(w)
            except ValueError:  # O(1)
                continue  # O(1)
            if isinstance(e, dict):  # O(1)
                out.append(e)  # O(1)
    return out  # O(R*w)


def append_rows(rows: list, path=None) -> Path:  # O(n*w)
    p = Path(path) if path else home() / "history.jsonl"  # O(1)
    p.parent.mkdir(parents=True, exist_ok=True)  # O(1)
    with p.open("a", encoding="utf-8") as fh:  # O(1)
        for r in rows:  # O(n)
            fh.write(json.dumps(r, sort_keys=True) + chr(10))  # O(w)
    return p  # O(1)


def _stat(rows: list) -> dict:  # O(n log n)
    durs = sorted(r["duration_s"] for r in rows if isinstance(r.get("duration_s"), (int, float)))  # O(n log n)
    costs = [r["cost_usd"] for r in rows if isinstance(r.get("cost_usd"), (int, float))]  # O(n)
    ftp = [r["first_time_pass"] for r in rows if r.get("first_time_pass") is not None]  # O(n)
    return {"n": len(rows), "ftp_n": len(ftp), "ftp_rate": round(sum(1 for x in ftp if x) / len(ftp), 4) if ftp else None,  # O(n)
            "mean_s": round(sum(durs) / len(durs), 1) if durs else None,
            "p90_s": durs[min(len(durs) - 1, math.ceil(0.9 * len(durs)) - 1)] if durs else None,
            "mean_cost": round(sum(costs) / len(costs), 6) if costs else None}


def derive(rows: list) -> dict:  # O(R log R)
    bands: dict[str, list] = {}  # O(1)
    tiers: dict[str, list] = {}  # O(1)
    both: dict[str, list] = {}  # O(1)
    for r in rows:  # O(R)
        b = r.get("band") or band_of(r.get("kind"), r.get("loc"), r.get("tests"))  # O(1)
        t = r.get("tier") or "unknown"  # O(1)
        bands.setdefault(b, []).append(r)  # O(1)
        tiers.setdefault(t, []).append(r)  # O(1)
        both.setdefault(f"{t}|{b}", []).append(r)  # O(1)
    return {"v": 1, "rows": len(rows), "bands": {k: _stat(v) for k, v in sorted(bands.items())},  # O(R log R)
            "tiers": {k: _stat(v) for k, v in sorted(tiers.items())},
            "bands_by_tier": {k: _stat(v) for k, v in sorted(both.items())}}


def write_stats(rows: list, path) -> dict:  # O(R log R)
    st = derive(rows)  # O(R log R)
    full = dict(st, updated=datetime.now().isoformat(timespec="seconds"))  # O(1)
    p = Path(path)  # O(1)
    p.parent.mkdir(parents=True, exist_ok=True)  # O(1)
    p.write_text(json.dumps(full, indent=1, sort_keys=True), encoding="utf-8")  # O(size)
    return st  # O(1)


def load_stats(path=None) -> dict:  # O(file)
    for p in ([Path(path)] if path else [home() / "stats.json"]):  # O(1)
        try:  # O(1)
            return json.loads(p.read_text(encoding="utf-8"))  # O(file)
        except (OSError, ValueError):  # O(1)
            pass  # O(1)
    return {}  # O(1)


def project_stats(sl_dir) -> dict:  # O(file)
    """The run's own stats first, else the global ones: what lint and dispatch cite."""
    return load_stats(Path(sl_dir) / "stats.json") or load_stats()  # O(file)


def predicted_flags(plan, args=None) -> dict:  # O(n*(n + e))
    """{task: {critical_path, slack_min, pred_min}} from the predicted schedule (unlimited lanes, CPM)."""
    import plan_tools as pt  # O(1)
    args = args or pt.build_parser().parse_args(["schedule", "--plan", "x"])  # O(1)
    rep = pt.schedule_report(plan, args)  # O(n*(n + e))
    graph, times = rep["graph"], rep["times"]  # O(1)
    es: dict[str, float] = {}  # O(1)
    order = list(graph)  # O(n)
    for _ in order:  # O(n) passes of a relaxation (graph is small)
        for t in order:  # O(n)
            es[t] = max([es.get(d, 0.0) + times.get(d, 0.0) for d in graph[t]] or [0.0])  # O(deg)
    total = max((es[t] + times.get(t, 0.0) for t in order), default=0.0)  # O(n)
    succ: dict[str, list] = {t: [] for t in order}  # O(n)
    for t in order:  # O(n)
        for d in graph[t]:  # O(deg)
            succ.setdefault(d, []).append(t)  # O(1)
    lf: dict[str, float] = {}  # O(1)
    for _ in order:  # O(n)
        for t in order:  # O(n)
            lf[t] = min([lf.get(s, total) - times.get(s, 0.0) for s in succ.get(t, [])] or [total])  # O(deg)
    out = {}  # O(1)
    for t in order:  # O(n)
        slack = round(lf[t] - (es[t] + times.get(t, 0.0)), 3)  # O(1)
        out[t] = {"critical_path": t in set(rep["critical_path"]), "slack_min": max(0.0, slack), "pred_min": round(times.get(t, 0.0), 3)}  # O(n)
    return out  # O(n^2)


def run_rows(plan_path, sl_dir=".superlite", run_id=None) -> list:  # O(M + B + n*r)
    """Rows for this run from metrics.jsonl, the plan, routing and the prime files."""
    import metrics  # O(1)
    import plan_tools as pt  # O(1)
    import routing  # O(1)
    sl = Path(sl_dir)  # O(1)
    plan = pt.Plan.load(str(plan_path))  # O(L)
    recs = metrics.task_records(sl, plan_path)  # O(M + B)
    flags = predicted_flags(plan)  # O(n*(n + e))
    tiers = routing.read_tiers(str(plan_path))  # O(file)
    focus = pt.review_focus_cases(plan)  # O(L)
    lang = detect_language(Path(sl).resolve().parent)  # O(entries)
    prime = {t: (sl / name).stat().st_size if (sl / name).is_file() else None for t, name in (("sonnet", "prime.md"), ("haiku", "prime-haiku.md"))}  # O(1)
    rid = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")  # O(1)
    rows = []  # O(1)
    for r in recs:  # O(n)
        task = plan.by_id.get(r["task"])  # O(1)
        parts = {s.lower() for o in (task.owns if task else []) for s in __import__("re").split("[/.]", o)}  # O(o)
        cats = sorted({c for pkg, cs in focus.items() if pkg in parts for c in cs})  # O(pkgs)
        f = flags.get(r["task"], {})  # O(1)
        tier = tiers.get(r["task"]) or ("haiku" if r["model"] == "haiku" else "sonnet")  # O(1)
        tk = r["tokens"]  # O(1)
        rows.append({"v": 1, "date": date.today().isoformat(), "run": rid, "source": "run", "language": lang, "task": r["task"],
                     "kind": r["kind"], "loc": r["loc"], "components": r["components"], "tests": r["tests"], "focus": cats,
                     "band": band_of(r["kind"], r["loc"], r["tests"]), "critical_path": f.get("critical_path"),
                     "slack_min": f.get("slack_min"), "tier": tier, "agent_type": r["agent_type"], "model": r["model"],
                     "prime_bytes": prime.get(tier), "duration_s": r["duration_s"],
                     "tokens": {"in": tk["in"], "out": tk["out"], "cache_read": tk["cache_read"], "cache_write": tk["cache_write"]},
                     "cost_usd": r["cost_usd"], "tool_calls": r["tool_calls"], "first_time_pass": r["first_time_pass"],
                     "rework_s": r["rework_s"], "rework_cost": r["rework_cost"], "pred_min": f.get("pred_min")})  # O(1)
    return rows  # O(n)


def record_run(plan_path, sl_dir=".superlite", run_id=None) -> list:  # O(R + n*w)
    """Append this run's rows (global and project) and refresh both stats files."""
    rows = run_rows(plan_path, sl_dir, run_id)  # O(M + B)
    if rows:  # O(1)
        append_rows(rows)  # O(n*w)
        append_rows(rows, Path(sl_dir) / "history.jsonl")  # O(n*w)
    write_stats(read_rows(), home() / "stats.json")  # O(R log R)
    write_stats(read_rows(Path(sl_dir) / "history.jsonl"), Path(sl_dir) / "stats.json")  # O(R log R)
    return rows  # O(1)


def import_file(path) -> list:  # O(n)
    """Rows from a bench score file (`tasks` records as bench/mechanical.py writes them)."""
    p = Path(path)  # O(1)
    try:  # O(1)
        d = json.loads(p.read_text(encoding="utf-8"))  # O(size)
    except (OSError, ValueError):  # O(1)
        return []  # O(1)
    tasks = d.get("tasks") if isinstance(d, dict) else d  # O(1)
    if not isinstance(tasks, list):  # O(1)
        return []  # O(1)
    run = p.stem  # O(1)
    day = datetime.fromtimestamp(p.stat().st_mtime).date().isoformat()  # O(1)
    rows = []  # O(1)
    for r in tasks:  # O(n)
        if not isinstance(r, dict) or not r.get("task"):  # O(1)
            continue  # O(1)
        tk = r.get("tokens") or {}  # O(1)
        slack = r.get("slack_s")  # O(1)
        rows.append({"v": 1, "date": day, "run": run, "source": "import", "language": "python", "task": r["task"],
                     "kind": r.get("kind"), "loc": r.get("loc"), "components": r.get("components"), "tests": r.get("tests"),
                     "focus": [], "band": band_of(r.get("kind"), r.get("loc"), r.get("tests")),
                     "critical_path": r.get("on_critical_path"), "slack_min": round(slack / 60, 3) if isinstance(slack, (int, float)) else None,
                     "tier": r.get("tier_planned") or ("haiku" if r.get("model") == "haiku" else "sonnet"), "agent_type": None,
                     "model": r.get("model"), "prime_bytes": None, "duration_s": r.get("duration_s"),
                     "tokens": {"in": tk.get("in"), "out": tk.get("out"), "cache_read": tk.get("cache_read"), "cache_write": tk.get("cache_write")},
                     "cost_usd": r.get("cost_usd"), "tool_calls": r.get("tool_calls"), "first_time_pass": r.get("first_time_pass"),
                     "rework_s": r.get("rework_s"), "rework_cost": r.get("rework_cost"), "pred_min": None})  # O(1)
    return rows  # O(n)


def main(argv=None):  # O(R log R)
    ap = argparse.ArgumentParser(description=__doc__.split(chr(10))[0])  # O(1)
    sub = ap.add_subparsers(dest="cmd", required=True)  # O(1)
    w = sub.add_parser("write")  # O(1)
    w.add_argument("--plan", required=True)  # O(1)
    w.add_argument("--dir", default=".superlite")  # O(1)
    w.add_argument("--run")  # O(1)
    r_ = sub.add_parser("read")  # O(1)
    r_.add_argument("--n", type=int, default=20)  # O(1)
    r_.add_argument("--band")  # O(1)
    d = sub.add_parser("derive")  # O(1)
    d.add_argument("--project", default=None)  # O(1)
    i = sub.add_parser("import")  # O(1)
    i.add_argument("files", nargs="+")  # O(1)
    a = ap.parse_args(argv)  # O(1)
    sys.stdout.reconfigure(encoding="utf-8")  # O(1)
    if a.cmd == "write":  # O(1)
        rows = record_run(a.plan, a.dir, a.run)  # O(R)
        print(f"history: {len(rows)} rows appended to {home() / 'history.jsonl'}")  # O(1)
    elif a.cmd == "read":  # O(1)
        rows = [r for r in read_rows() if not a.band or r.get("band") == a.band][-a.n:]  # O(R)
        for r in rows:  # O(n)
            print(json.dumps(r, sort_keys=True))  # O(w)
    elif a.cmd == "derive":  # O(1)
        st = write_stats(read_rows(), home() / "stats.json")  # O(R log R)
        if a.project:  # O(1)
            write_stats(read_rows(Path(a.project) / "history.jsonl"), Path(a.project) / "stats.json")  # O(R log R)
        print(f"stats: rows={st['rows']} bands={len(st['bands'])}")  # O(1)
    else:  # import
        have = {(r.get("run"), r.get("task")) for r in read_rows() if r.get("source") == "import"}  # O(R)
        new = [r for f in a.files for r in import_file(f) if (r["run"], r["task"]) not in have]  # O(files*n)
        if new:  # O(1)
            append_rows(new)  # O(n*w)
        write_stats(read_rows(), home() / "stats.json")  # O(R log R)
        print(f"history: imported {len(new)} rows")  # O(1)
    return 0  # O(1)


if __name__ == "__main__":  # O(1)
    sys.exit(main())  # O(1)
