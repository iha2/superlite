#!/usr/bin/env python3
"""Per-agent cost table (USD, tokens, wall) with est source until host reconcile.

    cost_table.summary_json(sl_dir) -> dict with source/est totals and rows
    cost_table.render(sl_dir) -> markdown table with totals row

Rows come from `.superlite/cost-rows.json` when present (prototype fixtures);
otherwise from metrics.agent_records + models.json. Always writes cost-summary.json.
Stdlib only.
"""
# Complexity notation:
#   R = cost rows, A = agents, B = transcript bytes
from __future__ import annotations

import json  # O(1)
import sys  # O(1)
from pathlib import Path  # O(1)

HERE = Path(__file__).resolve().parent  # O(1)
if str(HERE) not in sys.path:  # O(1)
    sys.path.insert(0, str(HERE))  # O(1)

__all__ = ["summary_json", "render"]

USD_PLACES = 4  # Review Focus: numeric rounding | O(1)


def _round_usd(v) -> float:  # O(1)
    return round(float(v), USD_PLACES)  # O(1)


def _norm_row(r: dict) -> dict:  # O(1)
    return {"agent": str(r["agent"]), "model": str(r.get("model") or ""),  # O(1)
            "wall_min": float(r.get("wall_min") or 0.0), "tokens": int(r.get("tokens") or 0),
            "usd": _round_usd(r.get("usd") or 0.0)}


def _rows_from_file(sl: Path) -> list | None:  # O(R)
    p = sl / "cost-rows.json"  # O(1)
    if not p.is_file():  # O(1)
        return None  # O(1)
    data = json.loads(p.read_text(encoding="utf-8"))  # O(R)
    return [_norm_row(r) for r in data]  # O(R)


def _rows_from_metrics(sl: Path) -> list:  # O(A + B)
    import metrics  # noqa: E402  | O(1)
    prices = metrics.load_prices()  # O(file)
    rows = []  # O(1)
    for a in metrics.agent_records(sl):  # O(A + B)
        tok = metrics._empty()  # O(1)
        for s in a.get("segments") or []:  # O(segments)
            for k in tok:  # O(1)
                tok[k] += (s.get("tok") or {}).get(k) or 0  # O(1)
        fam = metrics.family(a.get("model")) or metrics.family(a.get("type")) or "sonnet"  # O(1)
        price = prices.get(fam) or prices["sonnet"]  # O(1)
        start = (a.get("start_ms") or 0) / 1000.0  # O(1)
        stop = (a.get("stop_ms") or a.get("start_ms") or 0) / 1000.0  # O(1)
        wall_min = max(0.0, (stop - start) / 60.0)  # O(1)
        tokens = int(tok["input"] + tok["output"] + tok["cache_read"] + tok["cache_write_5m"] + tok["cache_write_1h"])  # O(1)
        rows.append(_norm_row({"agent": a.get("agent") or "", "model": fam, "wall_min": round(wall_min, 4),  # O(1)
                               "tokens": tokens, "usd": metrics.cost_of(tok, price)}))
    return rows  # O(1)


def summary_json(sl_dir: str) -> dict:  # O(R) or O(A + B)
    """Estimated run cost: source=est until host_cost.apply overlays host truth."""
    sl = Path(sl_dir)  # O(1)
    rows = _rows_from_file(sl)  # O(R)
    if rows is None:  # O(1)
        rows = _rows_from_metrics(sl)  # O(A + B)
    out = {"source": "est", "usd": _round_usd(sum(r["usd"] for r in rows)),  # O(R)
           "tokens": sum(r["tokens"] for r in rows), "wall_min": round(sum(r["wall_min"] for r in rows), 4),
           "rows": rows}
    sl.mkdir(parents=True, exist_ok=True)  # O(1)
    (sl / "cost-summary.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")  # O(R)
    return out  # O(1)


def render(sl_dir: str) -> str:  # O(R)
    """Markdown table: agent | model | wall_min | tokens | usd, plus a totals row."""
    s = summary_json(sl_dir)  # O(R)
    lines = ["| agent | model | wall_min | tokens | usd |",  # O(1)
             "| --- | --- | --- | --- | --- |"]
    for r in s["rows"]:  # O(R)
        lines.append(f"| {r['agent']} | {r['model']} | {r['wall_min']} | {r['tokens']} | {r['usd']} |")  # O(1)
    lines.append(f"| totals |  | {s['wall_min']} | {s['tokens']} | {s['usd']} |")  # O(1)
    return "\n".join(lines) + "\n"  # O(R)
