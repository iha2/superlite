"""cost_table.py: est summary JSON and markdown agent cost table."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from test_plan_tools import SCRIPTS

sys.path.insert(0, str(SCRIPTS))
import cost_table  # noqa: E402


def fixture_sl(tmp_path, rows=None):
    """Write a minimal .superlite with cost-rows the e.g. shape expects."""
    sl = tmp_path / ".superlite"
    sl.mkdir(parents=True, exist_ok=True)
    rows = rows or [{"agent": "a1", "model": "sonnet", "wall_min": 1.0, "tokens": 100, "usd": 0.5}]
    (sl / "cost-rows.json").write_text(json.dumps(rows), encoding="utf-8")
    return sl


def test_summary_json_est(tmp_path):
    sl = fixture_sl(tmp_path)
    s = cost_table.summary_json(str(sl))
    assert s["source"] == "est"
    assert s["usd"] == 0.5
    assert s["tokens"] == 100
    assert s["wall_min"] == 1.0
    assert s["rows"] == [{"agent": "a1", "model": "sonnet", "wall_min": 1.0, "tokens": 100, "usd": 0.5}]
    saved = json.loads((sl / "cost-summary.json").read_text(encoding="utf-8"))
    assert saved["source"] == "est" and saved["usd"] == 0.5


def test_table_columns_and_totals(tmp_path):
    sl = fixture_sl(tmp_path)
    md = cost_table.render(str(sl))
    assert "| agent |" in md
    assert "| model |" in md
    assert "wall_min" in md and "tokens" in md and "usd" in md
    assert "totals" in md
    assert "a1" in md and "sonnet" in md


def test_usd_rounding(tmp_path):
    """Review Focus: numeric — USD values round consistently (4 dp)."""
    sl = fixture_sl(tmp_path, rows=[
        {"agent": "a1", "model": "sonnet", "wall_min": 0.5, "tokens": 10, "usd": 0.1234567},
        {"agent": "a2", "model": "haiku", "wall_min": 0.5, "tokens": 20, "usd": 0.1000001},
    ])
    s = cost_table.summary_json(str(sl))
    assert s["rows"][0]["usd"] == 0.1235
    assert s["rows"][1]["usd"] == 0.1
    assert s["usd"] == 0.2235
