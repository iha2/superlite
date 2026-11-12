#!/usr/bin/env python3
"""Persistent run history: one aggregate row per task per run, and stats derived by feature band.

    history.py write  --plan P [--dir .superlite] [--run ID]   rows of this run -> history, then derive
    history.py read   [--n 20] [--band B]                      last rows (JSON lines)
    history.py derive [--project .superlite]                   rebuild stats.json (global and project)
    history.py import FILE...                                  bench/results/*.json score files -> rows

Files: `$SUPERLITE_HOME/history.jsonl` (default `$XDG_DATA_HOME/superlite`, else `~/.local/share/superlite`), append-only, plus `stats.json` there and a
per-project copy of both under `.superlite/`. Aggregates only: no code, prompts or file contents.

ROW SCHEMA (v1), one JSON object per line:
  v 1, date (YYYY-MM-DD), run (id), source ("run" | "import"), language (python, typescript, javascript,
  go, rust, csharp, java, ruby, unknown: from the repo's marker files), task (Tn),
  kind (mechanical|design), loc, components, tests (fast|slow), focus (sorted Review Focus categories
  of the task's package), band (see band_of), critical_path (bool; predicted for source=run, actual for
  imports), slack_min (predicted slack in minutes, or actual slack_s / 60 for imports),
  tier (haiku|sonnet), agent_type, model, prime_bytes, duration_s, tokens {in,out,cache_read,cache_write},
  cost_usd, tool_calls, first_time_pass (true|false|null), rework_s, rework_cost, pred_min,
  phase ("proto" | "build"; absent on older rows; `run` is the run archive's id), run_id (feature run id or null), sandbox
  (repo-relative open sandbox or null), repository (hub checkout path).
Recording the same (repository, run, task) twice is a no-op unless its outcome (first_time_pass, rework_s, rework_cost, status, findings) changed, then the new row is appended and the last row wins; stats and fits read through unique_rows (content duplicates count once per repository).
STATS (stats.json): {v, updated, rows, bands: {band: S}, tiers: {tier: S}, bands_by_tier: {tier|band: S}} with
  S = {n, ftp_n, ftp_rate, mean_s, p90_s, mean_cost}. Same rows, same stats (sorted keys, no clock in S).
Stdlib only.
"""
import argparse
import json
import math
import os
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402
import sandbox  # noqa: E402
import state  # noqa: E402

MARKERS = [("tsconfig.json", "typescript"), ("package.json", "javascript"), ("go.mod", "go"), ("Cargo.toml", "rust"),
           ("pyproject.toml", "python"), ("setup.py", "python"), ("requirements.txt", "python"), ("pom.xml", "java"),
           ("build.gradle", "java"), ("Gemfile", "ruby")]
SUFFIX_MARKERS = [(".csproj", "csharp"), (".sln", "csharp")]


def home() -> Path:
    """$SUPERLITE_HOME, else $XDG_DATA_HOME/superlite (default ~/.local/share/superlite).
    hooks/session-start repeats this rule in bash: change both together."""
    env = os.environ.get("SUPERLITE_HOME", "").strip()
    if env:
        return Path(env)
    user = Path(os.environ.get("HOME") or Path.home())
    xdg = os.environ.get("XDG_DATA_HOME", "")
    return (Path(xdg) if os.path.isabs(xdg) else user / ".local" / "share") / "superlite"  # the spec ignores relative values


def detect_language(root=".") -> str:  # O(entries of root)
    root = Path(root)
    for name, lang in MARKERS:
        if (root / name).exists():
            return lang
    try:
        names = [p.name for p in root.iterdir()]
    except OSError:
        return "unknown"
    for suf, lang in SUFFIX_MARKERS:
        if any(n.endswith(suf) for n in names):
            return lang
    return "unknown"


def band_of(kind, loc, tests) -> str:
    """kind/loc band/tests: `mechanical/le150/fast`. loc bands: le60, le150, le300, gt300, unk."""
    lb = "unk" if loc is None else ("le60" if loc <= 60 else "le150" if loc <= 150 else "le300" if loc <= 300 else "gt300")
    return f"{kind or 'design'}/{lb}/{tests or 'fast'}"


def read_rows(path=None) -> list:  # O(R*w)
    p = Path(path) if path else home() / "history.jsonl"
    out = []
    if p.is_file():
        for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                e = json.loads(ln)
            except ValueError:
                continue
            if isinstance(e, dict):
                out.append(e)
    return out


def append_rows(rows: list, path=None) -> Path:  # O(n*w)
    p = Path(path) if path else home() / "history.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True) + chr(10))
    return p


def _stat(rows: list) -> dict:  # O(n log n)
    durs = sorted(r["duration_s"] for r in rows if isinstance(r.get("duration_s"), (int, float)))
    costs = [r["cost_usd"] for r in rows if isinstance(r.get("cost_usd"), (int, float))]
    ftp = [r["first_time_pass"] for r in rows if r.get("first_time_pass") is not None]
    return {"n": len(rows), "ftp_n": len(ftp), "ftp_rate": round(sum(1 for x in ftp if x) / len(ftp), 4) if ftp else None,
            "mean_s": round(sum(durs) / len(durs), 1) if durs else None,
            "p90_s": durs[min(len(durs) - 1, math.ceil(0.9 * len(durs)) - 1)] if durs else None,
            "mean_cost": round(sum(costs) / len(costs), 6) if costs else None}


def derive(rows: list) -> dict:  # O(R log R)
    bands: dict[str, list] = {}
    tiers: dict[str, list] = {}
    both: dict[str, list] = {}
    for r in rows:
        b = r.get("band") or band_of(r.get("kind"), r.get("loc"), r.get("tests"))
        t = r.get("tier") or "unknown"
        bands.setdefault(b, []).append(r)
        tiers.setdefault(t, []).append(r)
        both.setdefault(f"{t}|{b}", []).append(r)
    return {"v": 1, "rows": len(rows), "bands": {k: _stat(v) for k, v in sorted(bands.items())},
            "tiers": {k: _stat(v) for k, v in sorted(tiers.items())},
            "bands_by_tier": {k: _stat(v) for k, v in sorted(both.items())}}


def _content_key(r: dict) -> tuple:
    tk = r.get("tokens") if isinstance(r.get("tokens"), dict) else {}
    return (r.get("repository"), r.get("date"), r.get("task"), r.get("duration_s"),
            tk.get("in"), tk.get("out"), r.get("model"))


def _id_key(r: dict) -> tuple:
    return (r.get("repository"), r.get("run"), r.get("task"))


def _keyed(r: dict) -> bool:  # O(1) rows of old files without a repository or run keep content-only dedupe
    return bool(r.get("repository") and r.get("run") and r.get("task"))


def unique_rows(rows: list[dict]) -> list[dict]:  # O(R)
    """The last row per (repository, run, task) wins; then content duplicates per repository, first kept:
    date, task, duration, tokens in/out and model, any run id."""
    last = {_id_key(r): i for i, r in enumerate(rows) if _keyed(r)}
    seen: set = set()
    out = []
    for i, r in enumerate(rows):
        if _keyed(r) and last[_id_key(r)] != i:
            continue
        k = _content_key(r)
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def feature_state(sl_dir) -> dict:  # O(file)
    d = base.read_json(Path(sl_dir) / "feature.json", None)
    return d if isinstance(d, dict) else {}


def phase_of(sl_dir) -> str:  # O(file)
    """`proto` while a prototype sandbox is open here, or (legacy) this checkout is the state's worktree, else `build`."""
    try:
        if sandbox.open_sandbox(Path(sl_dir)):
            return "proto"
        st = feature_state(sl_dir)
        wt = st.get("worktree")
        if wt and st.get("status") == "building" and Path(wt).resolve() == Path(sl_dir).resolve().parent:
            return "proto"
    except (OSError, ValueError):  # fail open: unreadable state is a build row
        pass
    return "build"


def write_stats(rows: list, path) -> dict:  # O(R log R)
    st = derive(unique_rows([r for r in rows if r.get("phase") != "proto"]))
    full = dict(st, updated=datetime.now().isoformat(timespec="seconds"))
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(full, indent=1, sort_keys=True), encoding="utf-8")
    return st


def load_stats(path=None) -> dict:  # O(file)
    data = base.read_json(Path(path) if path else home() / "stats.json", {})
    return data if isinstance(data, dict) else {}


def project_stats(sl_dir) -> dict:  # O(file)
    """The run's own stats first, else the global ones: what lint and dispatch cite."""
    return load_stats(Path(sl_dir) / "stats.json") or load_stats()


def predicted_flags(plan, args=None) -> dict:  # O(n*(n + e))
    """{task: {critical_path, slack_min, pred_min}} from the predicted schedule (unlimited lanes, CPM)."""
    import plan_tools as pt
    args = args or pt.build_parser().parse_args(["schedule", "--plan", "x"])
    rep = pt.schedule_report(plan, args)
    graph, times = rep["graph"], rep["times"]
    es: dict[str, float] = {}
    order = list(graph)
    for _ in order:
        for t in order:
            es[t] = max([es.get(d, 0.0) + times.get(d, 0.0) for d in graph[t]] or [0.0])
    total = max((es[t] + times.get(t, 0.0) for t in order), default=0.0)
    succ: dict[str, list] = {t: [] for t in order}
    for t in order:
        for d in graph[t]:
            succ.setdefault(d, []).append(t)
    lf: dict[str, float] = {}
    for _ in order:
        for t in order:
            lf[t] = min([lf.get(s, total) - times.get(s, 0.0) for s in succ.get(t, [])] or [total])
    out = {}
    for t in order:
        slack = round(lf[t] - (es[t] + times.get(t, 0.0)), 3)
        out[t] = {"critical_path": t in set(rep["critical_path"]), "slack_min": max(0.0, slack), "pred_min": round(times.get(t, 0.0), 3)}
    return out


def run_rows(plan_path, sl_dir=state.DIR_NAME, run_id=None) -> list:  # O(M + B + n*r)
    """Rows for this run from metrics.jsonl, the plan, routing and the prime files."""
    import metrics
    import plan_tools as pt
    import routing
    sl = Path(sl_dir)
    plan = pt.Plan.load(str(plan_path))
    recs = metrics.task_records(sl, plan_path)
    flags = predicted_flags(plan)
    tiers = routing.read_tiers(str(plan_path))
    focus = pt.review_focus_cases(plan)
    lang = detect_language(Path(sl).resolve().parent)
    prime = {t: (sl / name).stat().st_size if (sl / name).is_file() else None for t, name in (("sonnet", "prime.md"), ("haiku", "prime-haiku.md"))}
    rid = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    phase = phase_of(sl)
    st = feature_state(sl)
    sb = sandbox.open_sandbox(sl)
    rows = []
    for r in recs:
        task = plan.by_id.get(r["task"])
        parts = {s.lower() for o in (task.owns if task else []) for s in __import__("re").split("[/.]", o)}
        cats = sorted({c for pkg, cs in focus.items() if pkg in parts for c in cs})
        f = flags.get(r["task"], {})
        tier = tiers.get(r["task"]) or ("haiku" if r["model"] == "haiku" else "sonnet")
        tk = r["tokens"]
        rows.append({"v": 1, "date": date.today().isoformat(), "run": rid, "phase": phase, "run_id": st.get("run_id"), "sandbox": sb, "source": "run", "language": lang, "task": r["task"],
                     "kind": r["kind"], "loc": r["loc"], "components": r["components"], "tests": r["tests"], "focus": cats,
                     "band": band_of(r["kind"], r["loc"], r["tests"]), "critical_path": f.get("critical_path"),
                     "slack_min": f.get("slack_min"), "tier": tier, "agent_type": r["agent_type"], "model": r["model"],
                     "prime_bytes": prime.get(tier), "duration_s": r["duration_s"],
                     "tokens": {"in": tk["in"], "out": tk["out"], "cache_read": tk["cache_read"], "cache_write": tk["cache_write"]},
                     "cost_usd": r["cost_usd"], "tool_calls": r["tool_calls"], "first_time_pass": r["first_time_pass"],
                     "rework_s": r["rework_s"], "rework_cost": r["rework_cost"], "pred_min": f.get("pred_min")})
    return rows


OUTCOME_KEYS = ("first_time_pass", "rework_s", "rework_cost", "status", "findings")


def _fresh(rows: list, have: list) -> list:  # O(R + n)
    """Rows not yet recorded: new (repository, run, task), or the newest recorded row's outcome differs."""
    latest = {_id_key(r): r for r in have}
    return [r for r in rows if _id_key(r) not in latest
            or any(r.get(k) != latest[_id_key(r)].get(k) for k in OUTCOME_KEYS)]


def repository_id(sl_dir) -> str:
    """Canonical hub checkout path, shared by linked worktrees and distinct across local repositories."""
    checkout = Path(sl_dir).resolve().parent
    try:
        return state.hub_root(checkout).resolve().as_posix()
    except (base.GitError, OSError, IndexError, ValueError):
        return checkout.as_posix()


def record_run(plan_path, sl_dir=state.DIR_NAME, run_id=None) -> list:  # O(R + n*w)
    """Append this run's rows (global and project) and refresh both stats files."""
    repo = repository_id(sl_dir)
    return record_rows([dict(r, repository=repo) for r in run_rows(plan_path, sl_dir, run_id)], sl_dir)


def record_rows(rows: list, sl_dir) -> list:  # O(R + n*w)
    """Append rows to global and project history unless the same (repository, run, task) outcome is already
    the newest recorded; refresh both stats files. Returns the rows appended to the global history."""
    proj = Path(sl_dir) / "history.jsonl"
    new = _fresh(rows, read_rows())
    if new:
        append_rows(new)
    new_proj = _fresh(rows, read_rows(proj))
    if new_proj:
        append_rows(new_proj, proj)
    write_stats(read_rows(), home() / "stats.json")
    write_stats(read_rows(proj), Path(sl_dir) / "stats.json")
    return new


def import_file(path) -> list:  # O(n)
    """Rows from a bench score file (`tasks` records as bench/mechanical.py writes them)."""
    p = Path(path)
    d = base.read_json(p, None)
    tasks = d.get("tasks") if isinstance(d, dict) else d
    if not isinstance(tasks, list):
        return []
    run = p.stem
    day = datetime.fromtimestamp(p.stat().st_mtime).date().isoformat()
    rows = []
    for r in tasks:
        if not isinstance(r, dict) or not r.get("task"):
            continue
        tk = r.get("tokens") or {}
        slack = r.get("slack_s")
        rows.append({"v": 1, "date": day, "run": run, "source": "import", "language": "python", "task": r["task"],
                     "kind": r.get("kind"), "loc": r.get("loc"), "components": r.get("components"), "tests": r.get("tests"),
                     "focus": [], "band": band_of(r.get("kind"), r.get("loc"), r.get("tests")),
                     "critical_path": r.get("on_critical_path"), "slack_min": round(slack / 60, 3) if isinstance(slack, (int, float)) else None,
                     "tier": r.get("tier_planned") or ("haiku" if r.get("model") == "haiku" else "sonnet"), "agent_type": None,
                     "model": r.get("model"), "prime_bytes": None, "duration_s": r.get("duration_s"),
                     "tokens": {"in": tk.get("in"), "out": tk.get("out"), "cache_read": tk.get("cache_read"), "cache_write": tk.get("cache_write")},
                     "cost_usd": r.get("cost_usd"), "tool_calls": r.get("tool_calls"), "first_time_pass": r.get("first_time_pass"),
                     "rework_s": r.get("rework_s"), "rework_cost": r.get("rework_cost"), "pred_min": None})
    return rows


def main(argv=None):  # O(R log R)
    ap = argparse.ArgumentParser(description=__doc__.split(chr(10))[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write")
    w.add_argument("--plan", required=True)
    w.add_argument("--dir", default=state.DIR_NAME)
    w.add_argument("--run")
    r_ = sub.add_parser("read")
    r_.add_argument("--n", type=int, default=20)
    r_.add_argument("--band")
    d = sub.add_parser("derive")
    d.add_argument("--project", default=None)
    i = sub.add_parser("import")
    i.add_argument("files", nargs="+")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    if a.cmd == "write":
        rows = record_run(a.plan, a.dir, a.run)
        print(f"history: {len(rows)} rows appended to {home() / 'history.jsonl'}")
    elif a.cmd == "read":
        rows = [r for r in read_rows() if not a.band or r.get("band") == a.band][-a.n:]
        for r in rows:
            print(json.dumps(r, sort_keys=True))
    elif a.cmd == "derive":
        st = write_stats(read_rows(), home() / "stats.json")
        if a.project:
            write_stats(read_rows(Path(a.project) / "history.jsonl"), Path(a.project) / "stats.json")
        print(f"stats: rows={st['rows']} bands={len(st['bands'])}")
    else:  # import
        have = {(r.get("run"), r.get("task")) for r in read_rows() if r.get("source") == "import"}
        new = [r for f in a.files for r in import_file(f) if (r["run"], r["task"]) not in have]
        if new:
            append_rows(new)
        write_stats(read_rows(), home() / "stats.json")
        print(f"history: imported {len(new)} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
