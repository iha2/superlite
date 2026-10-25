"""Two clocks vs measured runs: task scale, and schedule = setup + scaled build + wrap-up."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from test_plan_tools import CONFIG, SCRIPTS, make_plan, sched

import predict  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "sitegen_p2_actuals.json"
CAL = Path(__file__).resolve().parents[2] / "bench" / "results" / "predict-calibration.json"


def test_time_scale_nudges_shipped_check_value():
    coef = predict.load_coef(str(CONFIG / "predictor.json"))
    models = predict.load_models()
    feats = {"components": 1, "loc": 120, "tests": "slow"}
    raw = 2.354
    assert abs(predict.time_from(feats, "sonnet", models, {**coef, "time": {**coef["time"], "scale": 1.0}}) - raw) < 1e-3
    assert abs(predict.time_from(feats, "sonnet", models, coef) - raw * coef["time"]["scale"]) < 1e-3


def test_schedule_total_is_setup_plus_scaled_build_plus_wrapup(tmp_path):
    p = make_plan(tmp_path, [{"id": "T1", "time": 10}])
    rep = sched(p)
    assert rep["total_min"] == pytest.approx(
        rep["pre_build_min"] + rep["build_scale"] * rep["build_min"] + rep["post_build_min"]
    )


def test_sitegen_task_mae_improves_with_scale():
    """Single-task implementer walls (not batch or Wave 0): scale should cut mean abs error."""
    data = json.loads(FIX.read_text(encoding="utf-8"))
    rows = [t for t in data["tasks"] if t["kind"] == "implementer"]
    scale = predict.load_coef(str(CONFIG / "predictor.json"))["time"]["scale"]
    old = sum(abs(t["pred_min"] - t["implementer_min"]) for t in rows) / len(rows)
    new = sum(abs(t["pred_min"] * scale - t["implementer_min"]) for t in rows) / len(rows)
    assert new < old


def test_combo_calibration_new_total_beats_old():
    """Leave-one-out calibrated totals in predict-calibration.json beat build+3.5."""
    assert CAL.is_file(), "bench/results/predict-calibration.json missing"
    data = json.loads(CAL.read_text(encoding="utf-8"))
    coef = predict.load_coef(str(CONFIG / "predictor.json"))
    s = coef["schedule"]
    better = 0
    for r in data["per_run"]:
        pred_build_min = r["predictor_build_s"] / 60.0
        actual = r["actual_total_s"] / 60.0
        old = pred_build_min + 3.5
        new = s["pre_build_min"] + s["build_scale"] * pred_build_min + s["post_build_min"]
        if abs(new - actual) < abs(old - actual):
            better += 1
    assert better >= 4  # at least 4 of 5 combo runs closer
