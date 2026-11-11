"""Schedule = setup + scaled build + wrap-up."""
from __future__ import annotations

import pytest

from test_plan_tools import make_plan, sched


def test_schedule_total_is_setup_plus_scaled_build_plus_wrapup(tmp_path):
    p = make_plan(tmp_path, [{"id": "T1", "time": 10}])
    rep = sched(p)
    assert rep["total_min"] == pytest.approx(
        rep["pre_build_min"] + rep["build_scale"] * rep["build_min"] + rep["post_build_min"]
    )
