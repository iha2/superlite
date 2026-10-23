#!/usr/bin/env python3
"""predict.py: the stored predictor for task time, context and cost.

  predict.py --plan PLAN.md [--models config/models.json] [--coef config/predictor.json]
  predict.py --refit RESULTS.json [RESULTS.json ...] [--coef config/predictor.json] [--out FILE] [--dry-run]

Reads each task's measurable features (Components, LOC owned, Tests: fast|slow, Model),
computes Est. time, Est. context and a per-task cost, and prints run totals as JSON.
Prices and speeds live in config/models.json; coefficients in config/predictor.json.
--refit fits the time coefficients to predicted-against-actual data (ridge toward the
current coefficients) and writes them back. Stdlib only.
"""

# Complexity notation:
# n = tasks in the plan, e = dependency edges, L = plan text size (chars/lines)
# M = models in models.json (<= ~5), S = bytes of a JSON file read
# N = refit observations, R = records per results file, F = files/globs matched, P = result patterns
# k = 4 regression coefficients (constant), per-task feature/cost math is O(1)
from __future__ import annotations  # O(1)

import argparse  # O(1)
import glob  # O(1)
import json  # O(1)
import math
import os  # O(1)
import sys  # O(1)
from pathlib import Path  # O(1)

SCRIPT_DIR = Path(__file__).resolve().parent  # O(1)
CONFIG_DIR = SCRIPT_DIR.parent / "config"  # O(1)
if str(SCRIPT_DIR) not in sys.path:  # O(1)
    sys.path.insert(0, str(SCRIPT_DIR))  # O(1)

import plan_tools  # noqa: E402  (plan_tools imports this module lazily, so no cycle) | O(1)


class PredictError(Exception):  # O(1)
    pass  # O(1)


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

def _load_json(path: Path, what: str) -> dict:  # O(S) read+parse
    try:  # O(1)
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))  # O(S) read + JSON parse
    except FileNotFoundError as exc:  # O(1)
        raise PredictError(f"{what} not found: {path}") from exc  # O(1)
    except json.JSONDecodeError as exc:  # O(1)
        raise PredictError(f"{what} is not valid JSON ({path}): {exc}") from exc  # O(1)


def load_models(path=None) -> dict:  # O(S + M)
    data = _load_json(Path(path) if path else CONFIG_DIR / "models.json", "models file")  # O(S)
    if path is None:  # an explicit file wins over superlite.json | O(1)
        import project_config  # O(1)
        data = project_config.apply_project_prices(data, Path(".").resolve())  # O(M)
    mult = data.get("cache_write_multipliers", {"5m": 1.25, "1h": 2.0})  # O(1)
    for m in data.get("models", {}).values():  # O(M) iterations
        m.setdefault("cache_write_5m", m["input"] * mult.get("5m", 1.25))  # O(1)
        m.setdefault("cache_write_1h", m["input"] * mult.get("1h", 2.0))  # O(1)
        m.setdefault("output_tps", 60)  # O(1)
    return data  # O(1)


HISTORY_MIN_ROWS = 30  # rows with loc and duration needed before history replaces the shipped coefficients | O(1)
_HIST_CACHE: dict = {}  # O(1)


def history_observations(rows: list) -> list:  # O(R)
    """Refit observations from history rows (aggregates only): loc, components, tests and duration."""
    obs = []  # O(1)
    for r in rows:  # O(R)
        loc, secs = r.get("loc"), r.get("duration_s")  # O(1)
        if isinstance(loc, (int, float)) and isinstance(secs, (int, float)) and secs > 0:  # O(1)
            obs.append({"components": float(r.get("components") or 1.0), "loc": float(loc), "slow": r.get("tests") == "slow",  # O(1)
                        "model": r.get("model"), "actual": secs / 60.0, "overhead": None})
    return obs  # O(R)


def load_coef(path=None) -> dict:  # O(S) shipped; O(R) with enough history
    """Shipped coefficients; with no explicit path and at least HISTORY_MIN_ROWS usable history rows
    (`$SUPERLITE_HOME/history.jsonl`) the refit from history instead (deterministic: same rows, same fit)."""
    coef = _load_json(Path(path) if path else CONFIG_DIR / "predictor.json", "predictor file")  # O(S)
    if path:  # O(1)
        return coef  # O(1)
    try:  # history never breaks a prediction | O(1)
        import history  # O(1)
        hp = history.home() / "history.jsonl"  # O(1)
        key = (str(hp), hp.stat().st_mtime_ns, hp.stat().st_size) if hp.is_file() else None  # O(1)
        if key is None:  # O(1)
            return coef  # O(1)
        if key not in _HIST_CACHE:  # O(R)
            obs = history_observations(history.read_rows(hp))  # O(R)
            fitted = None  # O(1)
            if len(obs) >= int(os.environ.get("SUPERLITE_HISTORY_MIN", HISTORY_MIN_ROWS)):  # O(1)
                fitted, _ = refit(obs, load_models(), coef)  # O(R*M)
                fitted["fit"]["source"] = "history"  # O(1)
            _HIST_CACHE[key] = fitted  # O(1)
        return _HIST_CACHE[key] or coef  # O(1)
    except Exception:  # noqa: BLE001 | O(1)
        return coef  # O(1)


def resolve_model(name: str | None, models: dict, coef: dict) -> str:  # O(M)
    table = models["models"]  # O(1)
    wanted = (name or coef.get("default_model", "sonnet")).lower()  # O(1)
    if wanted in table:  # O(1)
        return wanted  # O(1)
    for key in table:  # O(M) iterations
        if key in wanted:  # O(1) substring test on short names
            return key  # O(1)
    raise PredictError(f"unknown model {name!r}; known: {', '.join(table)}")  # O(M) join


# --------------------------------------------------------------------------
# Prediction
# --------------------------------------------------------------------------

def speed_scale(model: str, models: dict, coef: dict) -> float:  # O(1)
    ref = models["models"][coef.get("reference_model", "sonnet")]["output_tps"]  # O(1)
    return ref / models["models"][model]["output_tps"]  # O(1)


def time_from(feats: dict, model: str, models: dict, coef: dict) -> float:  # O(1)
    t = coef["time"]  # O(1)
    return (t["base_min"] + t["per_component_min"] * feats["components"]  # O(1)
            + (t["slow_tests_min"] if feats["tests"] == "slow" else 0.0)  # O(1)
            + t["per_loc_min"] * feats["loc"] * speed_scale(model, models, coef))  # O(1)


def context_from(task, feats: dict, coef: dict) -> int:  # O(1)
    c = coef["context"]  # O(1)
    docs = feats.get("docs_chars")  # O(1)
    if docs is None:  # O(1)
        docs = len(task.text)  # O(1)
    reads = feats.get("reads_loc")  # O(1)
    if reads is None:  # O(1)
        reads = len(task.consumes) * c["read_loc_per_consume"]  # O(1)
    var = c["base"] + c["per_loc"] * feats["loc"] + c["per_doc_char"] * docs + c["per_read_loc"] * reads  # O(1)
    return int(round(c["prefix"] + var * c["margin"]))  # O(1)


def cost_from(feats: dict, peak: int, model: str, models: dict, coef: dict) -> dict:  # O(1)
    c, price = coef["cost"], models["models"][model]  # O(1)
    prefix = coef["context"]["prefix"]  # O(1)
    calls = c["calls_base"] + c["calls_per_100_loc"] * feats["loc"] / 100  # O(1)
    variable = max(0, peak - prefix)  # O(1)
    avg_ctx = prefix + variable / 2  # O(1)
    tokens = {  # O(1)
        "input": c["fresh_input_tokens"],  # O(1)
        "output": feats["loc"] * c["output_per_loc"] + calls * c["output_per_call"],  # O(1)
        "cache_write": variable,  # O(1)
        "cache_read": calls * avg_ctx,  # O(1)
    }  # O(1)
    write_price = price["cache_write_1h" if c.get("cache_ttl") == "1h" else "cache_write_5m"]  # O(1)
    per_m = {"input": price["input"], "output": price["output"], "cache_write": write_price,  # O(1)
             "cache_read": price["cache_read"]}  # O(1)
    cost = {k: tokens[k] * per_m[k] / 1e6 for k in tokens}  # O(1)
    cost["total"] = sum(cost.values())  # O(1)
    return {"usd": {k: round(v, 6) for k, v in cost.items()}, "tokens": {k: int(round(v)) for k, v in tokens.items()}}  # O(1)


def task_features(task, coef: dict) -> tuple[dict, str]:  # O(1)
    raw = task.features  # O(1)
    d = coef["defaults"]  # O(1)
    feats = {  # O(1)
        "components": raw.get("components", d["components"]),  # O(1)
        "loc": raw.get("loc", d["loc"]),  # O(1)
        "tests": raw.get("tests", d["tests"]),  # O(1)
        "model": raw.get("model"),  # O(1)
        "kind": raw.get("kind"),  # O(1) mechanical|design, informational
        "docs_chars": raw.get("docs_chars"),  # O(1)
        "reads_loc": raw.get("reads_loc"),  # O(1)
    }  # O(1)
    source = "features" if task.has_features else ("parenthetical" if "loc" in raw else "defaults")  # O(1)
    return feats, source  # O(1)


def predict_task(task, models: dict, coef: dict) -> dict:  # O(M) via resolve_model, else O(1)
    feats, source = task_features(task, coef)  # O(1)
    model = resolve_model(feats["model"], models, coef)  # O(M)
    minutes = time_from(feats, model, models, coef)  # O(1)
    peak = context_from(task, feats, coef)  # O(1)
    cost = cost_from(feats, peak, model, models, coef)  # O(1)
    return {  # O(1)
        "task": task.id,  # O(1)
        "model": model,  # O(1)
        "features": {k: v for k, v in feats.items() if v is not None},  # O(1)
        "source": source,  # O(1)
        "est_time_min": round(minutes, 3),  # O(1)
        "est_context": peak,  # O(1)
        "est_context_k": round(peak / 1000),  # O(1)
        "stated_time_min": task.stated_time,  # O(1)
        "stated_context": task.stated_context,  # O(1)
        "cost": cost["usd"],  # O(1)
        "tokens": cost["tokens"],  # O(1)
    }  # O(1)


def predict_plan(plan, models: dict, coef: dict) -> dict:  # O(n)
    tasks = {}  # O(1)
    totals = {"input": 0.0, "output": 0.0, "cache_write": 0.0, "cache_read": 0.0, "total": 0.0}  # O(1)
    minutes = 0.0  # O(1)
    for t in plan.tasks:  # O(n) iterations
        p = predict_task(t, models, coef)  # O(M) per task
        tasks[t.id] = p  # O(1)
        minutes += p["est_time_min"]  # O(1)
        for k in totals:  # O(1) iterations (5 cost keys)
            totals[k] += p["cost"][k]  # O(1)
    return {  # O(1)
        "tasks": tasks,  # O(1)
        "totals": {  # O(1)
            "tasks": len(tasks),  # O(1)
            "sum_task_min": round(minutes, 3),  # O(1)
            "cost_usd": {k: round(v, 6) for k, v in totals.items()},  # O(1)
        },  # O(1)
    }  # O(1)


# --------------------------------------------------------------------------
# Refit
# --------------------------------------------------------------------------

def _record_list(data) -> list:  # O(R)
    if isinstance(data, list):  # O(1)
        return data  # O(1)
    if isinstance(data, dict):  # O(1)
        for key in ("tasks", "task_times", "records", "est_time"):  # O(1) iterations (4 keys)
            v = data.get(key)  # O(1)
            if isinstance(v, list):  # O(1)
                return v  # O(1)
            if isinstance(v, dict):  # O(1)
                return [dict(r, task=k) if isinstance(r, dict) else r for k, r in v.items()]  # O(R) copy per dict
    return []  # O(1)


def _num(rec: dict, *names):  # O(1)
    for n in names:  # O(1) iterations (<= 4 names)
        if n in rec and isinstance(rec[n], (int, float)):  # O(1)
            return float(rec[n])  # O(1)
    return None  # O(1)


def load_observations(paths: list[str]) -> list[dict]:  # O(S) total input + glob
    obs = []  # O(1)
    for pattern in paths:  # O(P) iterations
        files = sorted(glob.glob(pattern)) or [pattern]  # O(F log F) glob + sort
        for f in files:  # O(F) iterations per pattern
            data = _load_json(Path(f), "results file")  # O(S_f) read + parse
            for r in _record_list(data):  # O(R) iterations per file
                if not isinstance(r, dict):  # O(1)
                    continue  # O(1)
                loc = _num(r, "loc", "loc_owned", "LOC")  # O(1)
                actual = _num(r, "actual_min", "actual_time_min", "actual_minutes")  # O(1)
                if actual is None:  # O(1)
                    secs = _num(r, "actual_s", "actual_seconds", "seconds", "duration_s")  # O(1)
                    actual = secs / 60 if secs is not None else None  # O(1)
                if loc is None or actual is None:  # O(1)
                    continue  # O(1)
                slow = r.get("tests") == "slow" or r.get("slow") is True  # O(1)
                ov = _num(r, "start_overhead_min")  # O(1)
                if ov is None:  # O(1)
                    s_ov = _num(r, "start_overhead_s", "agent_start_overhead_s", "overhead_s")  # O(1)
                    ov = s_ov / 60 if s_ov is not None else None  # O(1)
                obs.append({"components": _num(r, "components") or 1.0, "loc": loc, "slow": bool(slow),  # O(1)
                            "model": r.get("model"), "actual": actual, "overhead": ov})  # O(1)
    return obs  # O(1)


def _solve(a: list[list[float]], b: list[float]) -> list[float]:  # O(k^3) = O(1) for k=4
    n = len(b)  # O(1)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]  # O(k^2)
    for col in range(n):  # O(k) iterations
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))  # O(k) per iteration
        if abs(m[piv][col]) < 1e-12:  # O(1)
            raise PredictError("singular system while refitting")  # O(1)
        m[col], m[piv] = m[piv], m[col]  # O(1)
        for r in range(n):  # O(k) iterations
            if r != col:  # O(1)
                f = m[r][col] / m[col][col]  # O(1)
                for c in range(col, n + 1):  # O(k) iterations
                    m[r][c] -= f * m[col][c]  # O(1)
    return [m[i][n] / m[i][i] for i in range(n)]  # O(k)


def mean_abs_error(obs: list[dict], models: dict, coef: dict) -> float:  # O(N*M)
    errs = []  # O(1)
    for o in obs:  # O(N) iterations
        feats = {"components": o["components"], "loc": o["loc"], "tests": "slow" if o["slow"] else "fast"}  # O(1)
        model = resolve_model(o["model"], models, coef)  # O(M)
        errs.append(abs(time_from(feats, model, models, coef) - o["actual"]))  # O(1)
    return sum(errs) / len(errs)  # O(N)


def _percentile(xs: list[float], q: float) -> float:  # O(N log N)
    xs = sorted(xs)  # O(N log N)
    pos = (len(xs) - 1) * q  # O(1)
    lo = int(math.floor(pos))  # O(1)
    hi = min(lo + 1, len(xs) - 1)  # O(1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)  # O(1)


def refit_band(obs: list[dict], coef: dict) -> dict:  # O(N log N)
    """high_min = p90 of observed actual task minutes, clamped to [4, 8], rounded to 0.5;
    low_min = 2.5 x the median agent start overhead when the results carry one, else unchanged."""
    band = dict(coef.get("size_band") or {"low_min": 1.5, "high_min": 6.0})  # O(1)
    p90 = _percentile([o["actual"] for o in obs], 0.9)  # O(N log N)
    band["high_min"] = round(min(8.0, max(4.0, p90)) * 2) / 2  # O(1)
    over = sorted(o["overhead"] for o in obs if o.get("overhead") is not None)  # O(N log N)
    if over:  # O(1)
        med = _percentile(over, 0.5)  # O(N)
        band["low_min"] = round(2.5 * med, 2)  # O(1)
    return band  # O(1)


def refit(obs: list[dict], models: dict, coef: dict, ridge: float = 0.25) -> tuple[dict, dict]:  # O(N*M + N*k^2)
    if len(obs) < 4:  # O(1)
        raise PredictError(f"need at least 4 usable observations to refit, got {len(obs)}")  # O(1)
    t = coef["time"]  # O(1)
    prior = [t["base_min"], t["per_component_min"], t["per_loc_min"] * 100, t["slow_tests_min"]]  # O(1)
    rows, ys = [], []  # O(1)
    for o in obs:  # O(N) iterations
        model = resolve_model(o["model"], models, coef)  # O(M)
        rows.append([1.0, o["components"], o["loc"] * speed_scale(model, models, coef) / 100, 1.0 if o["slow"] else 0.0])  # O(1)
        ys.append(o["actual"])  # O(1)
    k = 4  # O(1)
    a = [[sum(r[i] * r[j] for r in rows) + (ridge if i == j else 0.0) for j in range(k)] for i in range(k)]  # O(N*k^2) = O(N)
    b = [sum(r[i] * y for r, y in zip(rows, ys)) + ridge * prior[i] for i in range(k)]  # O(N*k) = O(N)
    sol = _solve(a, b)  # O(k^3) = O(1)
    sol = [max(0.0, v) for v in sol]  # O(1)
    new = json.loads(json.dumps(coef))  # O(size of coef) JSON round trip
    new["time"] = {"base_min": round(sol[0], 4), "per_component_min": round(sol[1], 4),  # O(1)
                   "per_loc_min": round(sol[2] / 100, 6), "slow_tests_min": round(sol[3], 4)}  # O(1)
    new["size_band"] = refit_band(obs, coef)  # O(N log N)
    before = mean_abs_error(obs, models, coef)  # O(N*M)
    after = mean_abs_error(obs, models, new)  # O(N*M)
    new["fit"] = {"n": len(obs), "mae_before_min": round(before, 4), "mae_after_min": round(after, 4)}  # O(1)
    return new, new["fit"]  # O(1)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None) -> int:  # O(S + n*M + N*M) plus file I/O
    for s in (sys.stdout, sys.stderr):  # O(1) iterations (2 streams)
        if hasattr(s, "reconfigure"):  # O(1)
            s.reconfigure(encoding="utf-8")  # O(1)
    ap = argparse.ArgumentParser(prog="predict.py", description="Stored predictor for task time, context and cost.")  # O(1)
    ap.add_argument("--plan", help="plan to predict")  # O(1)
    ap.add_argument("--models", help="models.json (default: config/models.json)")  # O(1)
    ap.add_argument("--coef", help="predictor.json (default: config/predictor.json)")  # O(1)
    ap.add_argument("--refit", nargs="+", metavar="RESULTS", help="refit coefficients from results JSON files")  # O(1)
    ap.add_argument("--out", help="write refitted coefficients here (default: the --coef file)")  # O(1)
    ap.add_argument("--dry-run", action="store_true", help="with --refit: print, do not write")  # O(1)
    args = ap.parse_args(argv)  # O(1)
    try:  # O(1)
        models = load_models(args.models)  # O(1)
        coef = load_coef(args.coef)  # O(1)
        if args.refit:  # O(1)
            obs = load_observations(args.refit)  # O(S_f) file reads
            new, fit = refit(obs, models, coef)  # O(N*M)
            dest = Path(args.out or args.coef or CONFIG_DIR / "predictor.json")  # O(1)
            if not args.dry_run:  # O(1)
                dest.write_text(json.dumps(new, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")  # O(1) file write
            print(json.dumps({"fit": fit, "time": new["time"], "size_band": new["size_band"], "written": None if args.dry_run else str(dest)}, indent=2))  # O(1)
            return 0  # O(1)
        if not args.plan:  # O(1)
            ap.error("--plan is required (or use --refit)")  # O(1)
        plan = plan_tools.Plan.load(args.plan)  # O(L) parse plan
        print(json.dumps(predict_plan(plan, models, coef), indent=2, ensure_ascii=False))  # O(n*M)
        return 0  # O(1)
    except (PredictError, plan_tools.PlanError) as exc:  # O(1)
        print(f"error: {exc}", file=sys.stderr)  # O(1)
        return getattr(exc, "code", 2)  # O(1)


if __name__ == "__main__":  # O(1)
    sys.exit(main())  # O(1)
