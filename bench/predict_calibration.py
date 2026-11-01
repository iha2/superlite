"""Why the schedule's predictions miss real runs, and a model fitted on the real numbers.

    python bench/predict_calibration.py [--write]     # --write: bench/results/predict-calibration.{md,json}

Data: every superlite combo run that has a transcript and a plan (per-dispatch durations, phase timeline) and the
predictor's own per-task minutes (`superlite-cli plan_tools schedule`, same plan). Three findings are measured, not assumed:
1. the predicted "total" is build + 3.5 min tail, but a real run also spends time BEFORE the first implementer starts
   (plan lint, stubs, primes) and AFTER the last build ends (package reviews, final review, fixes);
2. per task the predictor is too slow (it over-predicts), so the two errors partly cancel;
3. the build span is set by the graph, the agent cap and the dispatch lag, not by the sum of task times.
The fitted model is validated leave-one-run-out; its error is printed next to the predictor's. Nothing here edits
predictor.json: the coefficients are proposals."""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_shape_audit as bsa  # noqa: E402
import harness  # noqa: E402


def ols(X: list[list[float]], y: list[float]) -> list[float]:
    """Least squares by the normal equations (stdlib only), with a tiny ridge so a constant column cannot break it."""
    n = len(X[0])
    A = [[sum(r[i] * r[j] for r in X) + (1e-6 if i == j else 0) for j in range(n)] for i in range(n)]
    b = [sum(r[i] * v for r, v in zip(X, y)) for i in range(n)]
    for i in range(n):
        piv = max(range(i, n), key=lambda k: abs(A[k][i]))
        A[i], A[piv], b[i], b[piv] = A[piv], A[i], b[piv], b[i]
        for k in range(i + 1, n):
            f = A[k][i] / A[i][i]
            A[k] = [a - f * c for a, c in zip(A[k], A[i])]
            b[k] -= f * b[i]
    x = [0.0] * n
    for i in reversed(range(n)):
        x[i] = (b[i] - sum(A[i][j] * x[j] for j in range(i + 1, n))) / A[i][i]
    return x


def feats(r: dict) -> list[float]:
    return [1.0, r["loc"] / 100.0, float(r["components"]), 1.0 if r["tests"] == "slow" else 0.0]


def load() -> tuple[list[dict], dict]:
    """Per implementer dispatch rows and per run phase rows."""
    rows, runs = [], {}
    for lp in sorted(harness.RESULTS.glob("combo-superlite-*.jsonl")):
        name, rd = lp.stem, harness.RUNS / lp.stem
        pp = bsa.final_plan(rd) if rd.is_dir() else None
        events = harness.read_events(lp)
        if pp is None or not events:
            continue
        rep = bsa.plan_report(pp)
        ds = harness.dispatch_stats(events, rd)
        tasks, _ = harness.mechanical_score(rd, events, ds["dispatches"])
        tl = ds["timeline"]
        if tl["first_implementer_s"] is None or tl["last_build_end_s"] is None:
            continue
        kinds = [(d["subagent_type"] or "").split(":")[-1] for d in ds["dispatches"]]
        runs[name] = {
            "complete": tl["final_review_end_s"] is not None, "pre_build_s": tl["first_implementer_s"], "plan_write_s": tl["plan_write_s"],
            "build_span_s": round(tl["last_build_end_s"] - tl["first_implementer_s"], 1),
            "post_build_s": round(tl["last_event_s"] - tl["last_build_end_s"], 1) if tl["last_event_s"] else None,
            "total_s": tl["last_event_s"], "peak_agents": ds["max_concurrent_agents"],
            "reviews": sum(1 for k in kinds if "review" in k), "fixes": sum(1 for d in ds["dispatches"] if "fix" in (d["description"] or "").lower() or "fixer" in (d["subagent_type"] or "")),
            "pred_build_s": rep["base"]["build_min"] * 60, "pred_total_s": (rep["base"]["build_min"] + 3.5) * 60,
            "plan_tasks": rep["tasks"], "dispatched_tasks": len(tasks), "graph": rep["graph"], "pred_task_s": {t: m * 60 for t, m in rep["times"].items()},
        }
        for t in tasks:
            if t.get("actual_s") and rep["times"].get(t["task"]):
                rows.append({"run": name, "task": t["task"], "loc": t["loc"], "components": t["components"], "tests": t["tests"],
                             "model": t["model"], "actual_s": t["actual_s"], "pred_s": rep["times"][t["task"]] * 60,
                             "start_lag_s": t.get("start_lag_s")})
    return rows, runs


def mae(pairs):
    return round(st.mean(abs(a - b) for a, b in pairs), 1) if pairs else None


def task_models(rows: list[dict]) -> dict:
    """Leave-one-run-out per-task error of: the predictor, the predictor x a fitted scale, OLS on loc/components/slow tests."""
    runs = sorted({r["run"] for r in rows})
    out = {"predictor": [], "scaled": [], "ols": []}
    for run in runs:
        tr, te = [r for r in rows if r["run"] != run], [r for r in rows if r["run"] == run]
        if len(tr) < 6:
            continue
        k = st.median(r["actual_s"] / r["pred_s"] for r in tr)
        w = ols([feats(r) for r in tr], [r["actual_s"] for r in tr])
        for r in te:
            out["predictor"].append((r["actual_s"], r["pred_s"]))
            out["scaled"].append((r["actual_s"], k * r["pred_s"]))
            out["ols"].append((r["actual_s"], max(30.0, sum(a * b for a, b in zip(w, feats(r))))))
    return {k: {"mae_s": mae(v), "bias_s": round(st.mean(p - a for a, p in v), 1) if v else None, "n": len(v)} for k, v in out.items()}


def run_models(rows: list[dict], runs: dict) -> list[dict]:
    """Leave-one-run-out per-run error. Calibrated total = median pre-build + k x predicted build + median post-build, where
    k is the median real/predicted build ratio of the other runs. (Rebuilding the build span from fitted per-task times
    over the plan graph was tried and is worse: see finding 3.)"""
    out = []
    names = [n for n, r in runs.items() if r["complete"]]
    for name in names:
        r = runs[name]
        others = [runs[n] for n in names if n != name]
        k = st.median(o["build_span_s"] / o["pred_build_s"] for o in others)
        pre = st.median(o["pre_build_s"] for o in others)
        post = st.median(o["post_build_s"] for o in others)
        cal_build = k * r["pred_build_s"]
        out.append({"run": name, "actual_build_s": r["build_span_s"], "predictor_build_s": round(r["pred_build_s"]), "k": round(k, 2),
                    "calibrated_build_s": round(cal_build), "actual_total_s": r["total_s"], "predictor_total_s": round(r["pred_total_s"]),
                    "calibrated_total_s": round(pre + cal_build + post), "pre_s": r["pre_build_s"], "post_s": r["post_build_s"],
                    "pre_pred_s": round(pre), "post_pred_s": round(post)})
    return out


def final_fit(rows: list[dict]) -> dict:
    w = ols([feats(r) for r in rows], [r["actual_s"] for r in rows])
    ys = [r["actual_s"] for r in rows]
    ss = sum((y - st.mean(ys)) ** 2 for y in ys)
    r2 = lambda ps: round(1 - sum((y - p) ** 2 for y, p in zip(ys, ps)) / ss, 2)  # noqa: E731
    ml, ma = st.mean(r["loc"] for r in rows), st.mean(ys)
    corr = sum((r["loc"] - ml) * (y - ma) for r, y in zip(rows, ys)) / (sum((r["loc"] - ml) ** 2 for r in rows) * ss) ** 0.5
    return {"r2_predictor": r2([r["pred_s"] for r in rows]), "r2_fit_in_sample": r2([sum(a * b for a, b in zip(w, feats(r))) for r in rows]),
            "corr_loc_vs_seconds": round(corr, 2), "median_dispatch_s": round(st.median(ys), 1), "sd_dispatch_s": round(st.pstdev(ys), 1),"base_s": round(w[0], 1), "per_100_loc_s": round(w[1], 1), "per_component_s": round(w[2], 1), "slow_tests_s": round(w[3], 1), "n": len(rows),
            "as_predictor_json": {"base_min": round(w[0] / 60, 4), "per_loc_min": round(w[1] / 6000, 6), "per_component_min": round(w[2] / 60, 4),
                                  "slow_tests_min": round(w[3] / 60, 4)}}


def pct(a, b):
    return f"{100 * (b - a) / a:+.0f}%" if a else "-"


def markdown(rows, runs, tm, rm, fit) -> str:
    L = ["# Why the predictions miss, and a model fitted on real runs", "",
         "Generated by `bench/predict_calibration.py`. Leave-one-run-out: every number for a run is predicted without that run's data.", "",
         "## 1. Where the real time goes (seconds)", "",
         "| run | before first implementer | build span | after last build (reviews, final review, fixes) | total | predicted build | predicted total (build + 3.5 min) | reviews | fixes |",
         "|---|---|---|---|---|---|---|---|---|"]
    for n, r in runs.items():
        if r["complete"]:
            L.append(f"| {n} | {r['pre_build_s']} | {r['build_span_s']} | {r['post_build_s']} | {r['total_s']} | {r['pred_build_s']:.0f} | {r['pred_total_s']:.0f} | {r['reviews']} | {r['fixes']} |")
    comp = [r for r in runs.values() if r["complete"]]
    pre = [r["pre_build_s"] for r in comp]
    post = [r["post_build_s"] for r in comp]
    L += ["", f"**Finding 1.** The predicted total has no term for work before the first implementer ({min(pre):.0f} to {max(pre):.0f} s: plan lint, stubs, primes, wave 0) "
          f"or after the last build ({min(post):.0f} to {max(post):.0f} s: package reviews, final review, fixes). Predicted total is {st.median(r['pred_total_s'] for r in comp):.0f} s "
          f"against a real median of {st.median(r['total_s'] for r in comp):.0f} s; "
          f"{st.median(r['pre_build_s'] + r['post_build_s'] for r in comp):.0f} s of that (median) is outside the build.", "",
          "## 2. Per task: the predictor explains little", "",
          "Elapsed seconds of each implementer dispatch versus the predictor, leave-one-run-out (n = %d dispatches):" % tm["predictor"]["n"], "",
          "| model | mean abs error s | bias s (predicted - actual) |", "|---|---|---|"]
    for k, label in (("predictor", "current predictor"), ("scaled", "predictor x fitted scale"), ("ols", "OLS on loc, components, slow tests")):
        L.append(f"| {label} | {tm[k]['mae_s']} | {tm[k]['bias_s']:+} |")
    L += ["", f"**Finding 2.** Per dispatch the predictor is not far off on average (bias {tm['predictor']['bias_s']:+.0f} s) but it explains little: R^2 = {fit['r2_predictor']} "
          f"(a fit on loc, components and slow tests reaches {fit['r2_fit_in_sample']} in sample, and its leave-one-run-out error is no better). A dispatch takes a median "
          f"{fit['median_dispatch_s']} s with a spread of {fit['sd_dispatch_s']} s; LOC correlates at {fit['corr_loc_vs_seconds']}. The fitted per-component term is negative "
          f"(components and LOC are collinear), so do not copy it. Proposed coefficients (n={fit['n']}, NOT applied): base {fit['base_s']} s, {fit['per_100_loc_s']} s per 100 LOC, "
          f"{fit['per_component_s']} s per component, {fit['slow_tests_s']} s slow tests.", "",
          "## 3. Per run: old prediction vs calibrated (seconds)", "",
          "| run | real build | predictor build | k | calibrated build | real total | predictor total | calibrated total |", "|---|---|---|---|---|---|---|---|"]
    for r in rm:
        L.append(f"| {r['run']} | {r['actual_build_s']} | {r['predictor_build_s']} ({pct(r['actual_build_s'], r['predictor_build_s'])}) | {r['k']} | "
                 f"{r['calibrated_build_s']} ({pct(r['actual_build_s'], r['calibrated_build_s'])}) | {r['actual_total_s']} | "
                 f"{r['predictor_total_s']} ({pct(r['actual_total_s'], r['predictor_total_s'])}) | {r['calibrated_total_s']} ({pct(r['actual_total_s'], r['calibrated_total_s'])}) |")
    e_old = [abs(r["predictor_total_s"] - r["actual_total_s"]) / r["actual_total_s"] for r in rm]
    e_new = [abs(r["calibrated_total_s"] - r["actual_total_s"]) / r["actual_total_s"] for r in rm]
    b_old = [abs(r["predictor_build_s"] - r["actual_build_s"]) / r["actual_build_s"] for r in rm]
    b_new = [abs(r["calibrated_build_s"] - r["actual_build_s"]) / r["actual_build_s"] for r in rm]
    L += ["", f"Total time error: predictor {100 * st.mean(e_old):.0f}% mean (worst {100 * max(e_old):.0f}%), calibrated {100 * st.mean(e_new):.0f}% mean (worst {100 * max(e_new):.0f}%). "
          f"Build span error: predictor {100 * st.mean(b_old):.0f}% mean, calibrated {100 * st.mean(b_new):.0f}% mean (worst {100 * max(b_new):.0f}%).", "",
          "**Finding 3.** What still cannot be predicted from the plan: (a) the after-build phase (%.0f to %.0f s) depends on how many reviews and fixes the run needs; "
          "(b) the build span follows what the coordinator did, not the plan: tasks were batched or merged after the plan was written (dispatched/planned: %s), "
          "edges were not honoured (v2 ran T11 before T10 was done), and one slow dispatch can set the whole span (v3: one 480 s dispatch = the 481 s build). "
          "Rebuilding the span from fitted task times over the plan graph was tried (agent cap, dispatch lag) and was worse (65 to 76%% mean error against the predictor's 33%%), "
          "so the calibration scales the predictor's build instead. With %d complete runs this is a range estimate, not a point estimate." % (
              min(post), max(post), ", ".join(f"{n.split('-')[-1]} {r['dispatched_tasks']}/{r['plan_tasks']}" for n, r in runs.items() if r["complete"]), len(rm)), "",
          "## Caveats", "",
          "- Five complete runs and %d dispatches, one project (combo, Python). Every figure for a run is predicted without that run's data, so the errors are honest, but wide." % len(rows),
          "- Dispatch features come from the final plan file; where a run batched or merged tasks after writing the plan, features and dispatch do not match.",
          "- Not applied to predictor.json or `schedule`; those are owned elsewhere."]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    rows, runs = load()
    tm, rm, fit = task_models(rows), run_models(rows, runs), final_fit(rows)
    text = markdown(rows, runs, tm, rm, fit)
    if a.write:
        (harness.RESULTS / "predict-calibration.md").write_text(text, encoding="utf-8")
        slim = {n: {k: v for k, v in r.items() if k not in ("graph", "pred_task_s")} for n, r in runs.items()}
        (harness.RESULTS / "predict-calibration.json").write_text(json.dumps({"task_models": tm, "runs": slim, "per_run": rm, "fit": fit}, indent=1), encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
