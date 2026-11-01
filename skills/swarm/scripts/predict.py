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

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_DIR = SCRIPT_DIR.parent / "config"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import plan_tools  # noqa: E402  (plan_tools imports this module lazily, so no cycle)


class PredictError(Exception):
    pass


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

def _load_json(path: Path, what: str) -> dict:  # O(S) read+parse
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise PredictError(f"{what} not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise PredictError(f"{what} is not valid JSON ({path}): {exc}") from exc


def load_models(path=None) -> dict:  # O(S + M)
    data = _load_json(Path(path) if path else CONFIG_DIR / "models.json", "models file")
    if path is None:  # an explicit file wins over superlite.json
        import project_config
        data = project_config.apply_project_prices(data, Path(".").resolve())
    mult = data.get("cache_write_multipliers", {"5m": 1.25, "1h": 2.0})
    for m in data.get("models", {}).values():
        m.setdefault("cache_write_5m", m["input"] * mult.get("5m", 1.25))
        m.setdefault("cache_write_1h", m["input"] * mult.get("1h", 2.0))
        m.setdefault("output_tps", 60)
    return data


HISTORY_MIN_ROWS = 30  # rows with loc and duration needed before history replaces the shipped coefficients
_HIST_CACHE: dict = {}


def history_observations(rows: list) -> list:  # O(R)
    """Refit observations from history rows (aggregates only): loc, components, tests and duration."""
    obs = []
    for r in rows:
        loc, secs = r.get("loc"), r.get("duration_s")
        if isinstance(loc, (int, float)) and isinstance(secs, (int, float)) and secs > 0:
            obs.append({"components": float(r.get("components") or 1.0), "loc": float(loc), "slow": r.get("tests") == "slow",
                        "model": r.get("model"), "actual": secs / 60.0, "overhead": None})
    return obs


def load_coef(path=None) -> dict:  # O(S) shipped; O(R) with enough history
    """Shipped coefficients; with no explicit path and at least HISTORY_MIN_ROWS usable history rows
    (`$SUPERLITE_HOME/history.jsonl`) the refit from history instead (deterministic: same rows, same fit)."""
    coef = _load_json(Path(path) if path else CONFIG_DIR / "predictor.json", "predictor file")
    if path:
        return coef
    try:  # history never breaks a prediction
        import history
        hp = history.home() / "history.jsonl"
        key = (str(hp), hp.stat().st_mtime_ns, hp.stat().st_size) if hp.is_file() else None
        if key is None:
            return coef
        if key not in _HIST_CACHE:
            obs = history_observations(history.read_rows(hp))
            fitted = None
            if len(obs) >= int(os.environ.get("SUPERLITE_HISTORY_MIN", HISTORY_MIN_ROWS)):
                fitted, _ = refit(obs, load_models(), coef)
                fitted["fit"]["source"] = "history"
            _HIST_CACHE[key] = fitted
        return _HIST_CACHE[key] or coef
    except (OSError, ValueError, KeyError, TypeError, ArithmeticError, PredictError):
        return coef


def resolve_model(name: str | None, models: dict, coef: dict) -> str:  # O(M)
    table = models["models"]
    wanted = (name or coef.get("default_model", "sonnet")).lower()
    if wanted in table:
        return wanted
    for key in table:
        if key in wanted:
            return key
    raise PredictError(f"unknown model {name!r}; known: {', '.join(table)}")


# --------------------------------------------------------------------------
# Prediction
# --------------------------------------------------------------------------

def speed_scale(model: str, models: dict, coef: dict) -> float:
    ref = models["models"][coef.get("reference_model", "sonnet")]["output_tps"]
    return ref / models["models"][model]["output_tps"]


def time_from(feats: dict, model: str, models: dict, coef: dict) -> float:
    t = coef["time"]
    raw = (t["base_min"] + t["per_component_min"] * feats["components"]
           + (t["slow_tests_min"] if feats["tests"] == "slow" else 0.0)
           + t["per_loc_min"] * feats["loc"] * speed_scale(model, models, coef))
    return raw * float(t.get("scale", 1.0))


def context_from(task, feats: dict, coef: dict) -> int:
    c = coef["context"]
    docs = feats.get("docs_chars")
    if docs is None:
        docs = len(task.text)
    reads = feats.get("reads_loc")
    if reads is None:
        reads = len(task.consumes) * c["read_loc_per_consume"]
    var = c["base"] + c["per_loc"] * feats["loc"] + c["per_doc_char"] * docs + c["per_read_loc"] * reads
    return int(round(c["prefix"] + var * c["margin"]))


def cost_from(feats: dict, peak: int, model: str, models: dict, coef: dict) -> dict:
    c, price = coef["cost"], models["models"][model]
    prefix = coef["context"]["prefix"]
    calls = c["calls_base"] + c["calls_per_100_loc"] * feats["loc"] / 100
    variable = max(0, peak - prefix)
    avg_ctx = prefix + variable / 2
    tokens = {
        "input": c["fresh_input_tokens"],
        "output": feats["loc"] * c["output_per_loc"] + calls * c["output_per_call"],
        "cache_write": variable,
        "cache_read": calls * avg_ctx,
    }
    write_price = price["cache_write_1h" if c.get("cache_ttl") == "1h" else "cache_write_5m"]
    per_m = {"input": price["input"], "output": price["output"], "cache_write": write_price,
             "cache_read": price["cache_read"]}
    cost = {k: tokens[k] * per_m[k] / 1e6 for k in tokens}
    cost["total"] = sum(cost.values())
    return {"usd": {k: round(v, 6) for k, v in cost.items()}, "tokens": {k: int(round(v)) for k, v in tokens.items()}}


def task_features(task, coef: dict) -> tuple[dict, str]:
    raw = task.features
    d = coef["defaults"]
    feats = {
        "components": raw.get("components", d["components"]),
        "loc": raw.get("loc", d["loc"]),
        "tests": raw.get("tests", d["tests"]),
        "model": raw.get("model"),
        "kind": raw.get("kind"),
        "docs_chars": raw.get("docs_chars"),
        "reads_loc": raw.get("reads_loc"),
    }
    source = "features" if task.has_features else ("parenthetical" if "loc" in raw else "defaults")
    return feats, source


def predict_task(task, models: dict, coef: dict) -> dict:  # O(M) via resolve_model, else O(1)
    feats, source = task_features(task, coef)
    model = resolve_model(feats["model"], models, coef)
    minutes = time_from(feats, model, models, coef)
    peak = context_from(task, feats, coef)
    cost = cost_from(feats, peak, model, models, coef)
    return {
        "task": task.id,
        "model": model,
        "features": {k: v for k, v in feats.items() if v is not None},
        "source": source,
        "est_time_min": round(minutes, 3),
        "est_context": peak,
        "est_context_k": round(peak / 1000),
        "stated_time_min": task.stated_time,
        "stated_context": task.stated_context,
        "cost": cost["usd"],
        "tokens": cost["tokens"],
    }


def predict_plan(plan, models: dict, coef: dict) -> dict:  # O(n)
    tasks = {}
    totals = {"input": 0.0, "output": 0.0, "cache_write": 0.0, "cache_read": 0.0, "total": 0.0}
    minutes = 0.0
    for t in plan.tasks:
        p = predict_task(t, models, coef)
        tasks[t.id] = p
        minutes += p["est_time_min"]
        for k in totals:
            totals[k] += p["cost"][k]
    return {
        "tasks": tasks,
        "totals": {
            "tasks": len(tasks),
            "sum_task_min": round(minutes, 3),
            "cost_usd": {k: round(v, 6) for k, v in totals.items()},
        },
    }


# --------------------------------------------------------------------------
# Refit
# --------------------------------------------------------------------------

def _record_list(data) -> list:  # O(R)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("tasks", "task_times", "records", "est_time"):
            v = data.get(key)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):
                return [dict(r, task=k) if isinstance(r, dict) else r for k, r in v.items()]
    return []


def _num(rec: dict, *names):
    for n in names:
        if n in rec and isinstance(rec[n], (int, float)):
            return float(rec[n])
    return None


def _observation(r: dict) -> dict | None:  # O(1)
    """One results record as an observation; None when it has no loc or no actual time."""
    loc = _num(r, "loc", "loc_owned", "LOC")
    actual = _num(r, "actual_min", "actual_time_min", "actual_minutes")
    if actual is None:
        secs = _num(r, "actual_s", "actual_seconds", "seconds", "duration_s")
        actual = secs / 60 if secs is not None else None
    if loc is None or actual is None:
        return None
    slow = r.get("tests") == "slow" or r.get("slow") is True
    ov = _num(r, "start_overhead_min")
    if ov is None:
        s_ov = _num(r, "start_overhead_s", "agent_start_overhead_s", "overhead_s")
        ov = s_ov / 60 if s_ov is not None else None
    return {"components": _num(r, "components") or 1.0, "loc": loc, "slow": bool(slow),
            "model": r.get("model"), "actual": actual, "overhead": ov}


def load_observations(paths: list[str]) -> list[dict]:  # O(S) total input + glob
    obs = []
    for pattern in paths:
        for f in sorted(glob.glob(pattern)) or [pattern]:
            records = _record_list(_load_json(Path(f), "results file"))
            obs += [o for o in (_observation(r) for r in records if isinstance(r, dict)) if o is not None]
    return obs


def _eliminate(m: list[list[float]], col: int, n: int) -> None:  # O(k^2)
    """Zero column `col` in every other row of the augmented matrix."""
    for r in range(n):
        if r != col:
            f = m[r][col] / m[col][col]
            for c in range(col, n + 1):
                m[r][c] -= f * m[col][c]


def _solve(a: list[list[float]], b: list[float]) -> list[float]:  # O(k^3) = O(1) for k=4
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            raise PredictError("singular system while refitting")
        m[col], m[piv] = m[piv], m[col]
        _eliminate(m, col, n)
    return [m[i][n] / m[i][i] for i in range(n)]


def mean_abs_error(obs: list[dict], models: dict, coef: dict) -> float:  # O(N*M)
    errs = []
    for o in obs:
        feats = {"components": o["components"], "loc": o["loc"], "tests": "slow" if o["slow"] else "fast"}
        model = resolve_model(o["model"], models, coef)
        errs.append(abs(time_from(feats, model, models, coef) - o["actual"]))
    return sum(errs) / len(errs)


def _percentile(xs: list[float], q: float) -> float:  # O(N log N)
    xs = sorted(xs)
    pos = (len(xs) - 1) * q
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def refit_band(obs: list[dict], coef: dict) -> dict:  # O(N log N)
    """high_min = p90 of observed actual task minutes, clamped to [4, 8], rounded to 0.5;
    low_min = 2.5 x the median agent start overhead when the results carry one, else unchanged."""
    band = dict(coef.get("size_band") or {"low_min": 1.5, "high_min": 6.0})
    p90 = _percentile([o["actual"] for o in obs], 0.9)
    band["high_min"] = round(min(8.0, max(4.0, p90)) * 2) / 2
    over = sorted(o["overhead"] for o in obs if o.get("overhead") is not None)
    if over:
        med = _percentile(over, 0.5)
        band["low_min"] = round(2.5 * med, 2)
    return band


def refit(obs: list[dict], models: dict, coef: dict, ridge: float = 0.25) -> tuple[dict, dict]:  # O(N*M + N*k^2)
    if len(obs) < 4:
        raise PredictError(f"need at least 4 usable observations to refit, got {len(obs)}")
    t = coef["time"]
    prior = [t["base_min"], t["per_component_min"], t["per_loc_min"] * 100, t["slow_tests_min"]]
    rows, ys = [], []
    for o in obs:
        model = resolve_model(o["model"], models, coef)
        rows.append([1.0, o["components"], o["loc"] * speed_scale(model, models, coef) / 100, 1.0 if o["slow"] else 0.0])
        ys.append(o["actual"])
    k = 4
    a = [[sum(r[i] * r[j] for r in rows) + (ridge if i == j else 0.0) for j in range(k)] for i in range(k)]
    b = [sum(r[i] * y for r, y in zip(rows, ys)) + ridge * prior[i] for i in range(k)]
    sol = _solve(a, b)
    sol = [max(0.0, v) for v in sol]
    new = json.loads(json.dumps(coef))
    new["time"] = {"base_min": round(sol[0], 4), "per_component_min": round(sol[1], 4),
                   "per_loc_min": round(sol[2] / 100, 6), "slow_tests_min": round(sol[3], 4),
                   "scale": 1.0}  # refit absorbs scale into the coefficients
    new["size_band"] = refit_band(obs, coef)
    before = mean_abs_error(obs, models, coef)
    after = mean_abs_error(obs, models, new)
    new["fit"] = {"n": len(obs), "mae_before_min": round(before, 4), "mae_after_min": round(after, 4)}
    return new, new["fit"]


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None) -> int:  # O(S + n*M + N*M) plus file I/O
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="predict.py", description="Stored predictor for task time, context and cost.")
    ap.add_argument("--plan", help="plan to predict")
    ap.add_argument("--models", help="models.json (default: config/models.json)")
    ap.add_argument("--coef", help="predictor.json (default: config/predictor.json)")
    ap.add_argument("--refit", nargs="+", metavar="RESULTS", help="refit coefficients from results JSON files")
    ap.add_argument("--out", help="write refitted coefficients here (default: the --coef file)")
    ap.add_argument("--dry-run", action="store_true", help="with --refit: print, do not write")
    args = ap.parse_args(argv)
    try:
        models = load_models(args.models)
        coef = load_coef(args.coef)
        if args.refit:
            obs = load_observations(args.refit)
            new, fit = refit(obs, models, coef)
            dest = Path(args.out or args.coef or CONFIG_DIR / "predictor.json")
            if not args.dry_run:
                dest.write_text(json.dumps(new, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(json.dumps({"fit": fit, "time": new["time"], "size_band": new["size_band"], "written": None if args.dry_run else str(dest)}, indent=2))
            return 0
        if not args.plan:
            ap.error("--plan is required (or use --refit)")
        plan = plan_tools.Plan.load(args.plan)
        print(json.dumps(predict_plan(plan, models, coef), indent=2, ensure_ascii=False))
        return 0
    except (PredictError, plan_tools.PlanError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return getattr(exc, "code", 2)


if __name__ == "__main__":
    sys.exit(main())
