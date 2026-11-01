"""cost.py: est summary JSON and markdown agent cost table."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from test_plan_tools import SCRIPTS

sys.path.insert(0, str(SCRIPTS))
import cost  # noqa: E402


def fixture_sl(tmp_path, rows=None):
    """Write a minimal .superlite with cost-rows the e.g. shape expects."""
    sl = tmp_path / ".superlite"
    sl.mkdir(parents=True, exist_ok=True)
    rows = rows or [{"agent": "a1", "model": "sonnet", "wall_min": 1.0, "tokens": 100, "usd": 0.5}]
    (sl / "cost-rows.json").write_text(json.dumps(rows), encoding="utf-8")
    return sl


def test_summary_json_est(tmp_path):
    sl = fixture_sl(tmp_path)
    s = cost.est_summary(sl)
    assert s["source"] == "est"
    assert s["usd"] == 0.5
    assert s["tokens"] == 100
    assert s["wall_min"] == 1.0
    assert s["rows"] == [{"agent": "a1", "model": "sonnet", "wall_min": 1.0, "tokens": 100, "usd": 0.5}]
    saved = json.loads((sl / "cost-summary.json").read_text(encoding="utf-8"))
    assert saved["source"] == "est" and saved["usd"] == 0.5


def test_table_columns_and_totals(tmp_path):
    sl = fixture_sl(tmp_path)
    md = cost.table(sl)
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
    s = cost.est_summary(sl)
    assert s["rows"][0]["usd"] == 0.1235
    assert s["rows"][1]["usd"] == 0.1
    assert s["usd"] == 0.2235


def test_cost_line_matches_old_output(tmp_path):
    """`cost.py line` prints what cost_live printed: the host block, silent before the host reports."""
    import subprocess
    sl = tmp_path / ".superlite"
    sl.mkdir()
    script = Path(SCRIPTS) / "cost.py"

    def run(*args):
        return subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True, encoding="utf-8")
    assert run("line", str(sl)).stdout == ""
    row = {"ev": "host_usage", "host": "cursor", "model": "sonnet", "generation_id": "g1",
           "input": 1000, "output": 200, "cache_read": 500, "cache_write": 50, "usd": 1.25}
    (sl / "metrics.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    want = ("superlite cost (host, cursor): $1.2500\n  tokens 1750 | 1 turn\n  in 1000 out 200 cache_r 500\n")
    assert run("line", str(sl)).stdout == want
    assert cost.line(sl) == want.rstrip("\n")
    assert run("table", str(sl)).stdout.startswith("| agent | model | wall_min | tokens | usd |\n")
    assert run("bogus").returncode == 2
