"""host_cost.py: overlay host truth on cost_table.summary_json (fake by default)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from test_plan_tools import SCRIPTS

sys.path.insert(0, str(SCRIPTS))
import host_cost  # noqa: E402

# T3 e.g. shape; swap to real cost_table when SUPERLITE_REAL=1
FAKE_SUMMARY = {
    "source": "est",
    "usd": 0.5,
    "tokens": 100,
    "wall_min": 1.0,
    "rows": [{"agent": "a1", "model": "sonnet", "wall_min": 1.0, "tokens": 100, "usd": 0.5}],
}


def summary_json(sl_dir: str) -> dict:
    """Swappable seam: fake T3 e.g. unless SUPERLITE_REAL=1."""
    if os.environ.get("SUPERLITE_REAL") == "1":
        import cost_table  # noqa: E402
        return cost_table.summary_json(sl_dir)
    return dict(FAKE_SUMMARY)


def write_claude_stream(path: Path, totals):
    """JSONL stream; last total_cost_usd wins (protocol Review Focus)."""
    lines = []
    for i, usd in enumerate(totals):
        lines.append(json.dumps({"type": "result", "total_cost_usd": usd, "n": i}))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_claude_stream_labels_host(tmp_path):
    sl = tmp_path / ".superlite"
    sl.mkdir()
    (sl / "cost-rows.json").write_text(json.dumps(FAKE_SUMMARY["rows"]), encoding="utf-8")
    stream = tmp_path / "stream.jsonl"
    write_claude_stream(stream, [0.1, 1.25])
    base = summary_json(str(sl))
    out = host_cost.apply(base, "claude", str(stream))
    assert out["source"] == "host"
    assert out["usd"] == 1.25


def test_other_host_fixture(tmp_path):
    base = summary_json(str(tmp_path))
    path = tmp_path / "host.json"
    path.write_text(json.dumps({"usd": 1.25}), encoding="utf-8")
    out = host_cost.apply(base, "other", str(path))
    assert out["source"] == "host"
    assert out["usd"] == 1.25


def test_missing_host_stays_est(tmp_path):
    base = summary_json(str(tmp_path))
    out = host_cost.apply(base, "claude", "")
    assert out["source"] == "est"
    assert out["usd"] == 0.5
    assert out is base or out == base


def test_json_shape_protocol(tmp_path):
    """Review Focus: protocol — host overlay keeps summary keys, only source/usd flip."""
    base = summary_json(str(tmp_path))
    path = tmp_path / "h.json"
    path.write_text(json.dumps({"usd": 2.0, "extra": "ignored"}), encoding="utf-8")
    out = host_cost.apply(dict(base), "cursor", str(path))
    assert set(out) >= {"source", "usd", "tokens", "wall_min", "rows"}
    assert out["source"] == "host" and out["usd"] == 2.0
    assert out["tokens"] == base["tokens"] and out["rows"] == base["rows"]
