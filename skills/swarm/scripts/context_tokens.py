"""Live context size and compact boundaries from a Claude Code transcript (JSONL).

    context_tokens.current_context(path) -> int
    context_tokens.last_compact_boundary(path) -> dict | None

Context size = last non-sidechain assistant message after the latest compact marker (boundary or
summary line): input + cache_read + cache_creation tokens. 0 when there is no such message (also right
after a compact, before the next assistant reply). Unreadable files and corrupt lines never raise. Stdlib only.
"""
from __future__ import annotations

import json

__all__ = ["snapshot", "current_context", "last_compact_boundary"]


def _parse(raw: str) -> dict | None:
    """Parse one JSONL line; None for blank, corrupt, or non-object lines."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def _num(v) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def _is_marker(e: dict) -> bool:
    return bool(e.get("isCompactSummary")) or (
        e.get("type") == "system" and e.get("subtype") == "compact_boundary")


def snapshot(path: str) -> dict:
    """Read usage and the latest compact boundary in one streaming pass."""
    total = 0
    boundary = None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for n, raw in enumerate(fh, 1):
                e = _parse(raw)
                if e is None:
                    continue
                is_marker = _is_marker(e)
                if is_marker:
                    total = 0
                if e.get("type") == "system" and e.get("subtype") == "compact_boundary":
                    meta = e.get("compactMetadata")
                    meta = meta if isinstance(meta, dict) else {}
                    trig = meta.get("trigger")
                    boundary = {"line": n, "pre_tokens": _num(meta.get("preTokens")),
                                "trigger": trig if isinstance(trig, str) else ""}
                elif e.get("isCompactSummary"):
                    if boundary and boundary["trigger"] and n - boundary["line"] <= 1:
                        continue
                    boundary = {"line": n, "pre_tokens": 0, "trigger": ""}
                if is_marker:
                    continue
                if e.get("type") != "assistant" or e.get("isSidechain"):
                    continue
                msg = e.get("message")
                usage = msg.get("usage") if isinstance(msg, dict) else None
                if isinstance(usage, dict):
                    total = (_num(usage.get("input_tokens")) + _num(usage.get("cache_read_input_tokens"))
                             + _num(usage.get("cache_creation_input_tokens")))
    except OSError:
        return {"tokens": 0, "boundary": None}
    return {"tokens": total, "boundary": boundary}


def current_context(path: str) -> int:
    return snapshot(path)["tokens"]


def last_compact_boundary(path: str) -> dict | None:
    return snapshot(path)["boundary"]
