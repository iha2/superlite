#!/usr/bin/env python3
"""Overlay host analytics total onto a cost_table.summary_json dict.

    host_cost.apply(summary, kind, path) -> dict

kind=claude: JSONL stream; last total_cost_usd wins.
other kinds: JSON object with usd.
Empty or missing path: return summary unchanged (est), never raise.
Missing host keys never fail a build. Stdlib only.
"""
# Complexity notation:
#   L = stream lines, B = file bytes
from __future__ import annotations

import json  # O(1)
from pathlib import Path  # O(1)

__all__ = ["apply"]


def _claude_total(path: Path) -> float | None:  # O(L)
    """Last cumulative total_cost_usd in a Claude stream-json log."""
    truth = None  # O(1)
    try:  # O(1)
        text = path.read_text(encoding="utf-8", errors="replace")  # O(B)
    except OSError:  # O(1)
        return None  # O(1)
    for ln in text.splitlines():  # O(L)
        ln = ln.strip()  # O(1)
        if not ln:  # O(1)
            continue  # O(1)
        try:  # O(1)
            e = json.loads(ln)  # O(w)
        except ValueError:  # O(1)
            continue  # O(1)
        if isinstance(e, dict) and isinstance(e.get("total_cost_usd"), (int, float)):  # O(1)
            truth = float(e["total_cost_usd"])  # O(1)
    return truth  # O(1)


def _json_usd(path: Path) -> float | None:  # O(B)
    try:  # O(1)
        data = json.loads(path.read_text(encoding="utf-8"))  # O(B)
    except (OSError, ValueError):  # O(1)
        return None  # O(1)
    if isinstance(data, dict) and isinstance(data.get("usd"), (int, float)):  # O(1)
        return float(data["usd"])  # O(1)
    return None  # O(1)


def apply(summary: dict, kind: str, path: str) -> dict:  # O(L) or O(B)
    """Return summary with source=host and usd from path, or the est summary if unavailable."""
    if not path:  # O(1)
        return summary  # O(1)
    p = Path(path)  # O(1)
    if not p.is_file():  # O(1)
        return summary  # O(1)
    usd = _claude_total(p) if kind == "claude" else _json_usd(p)  # O(L) or O(B)
    if usd is None:  # O(1)
        return summary  # O(1)
    out = dict(summary)  # O(keys)
    out["source"] = "host"  # O(1)
    out["usd"] = usd  # O(1)
    return out  # O(1)
