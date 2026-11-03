"""Live context size and compact boundaries from a Claude Code transcript (JSONL).

    context_tokens.current_context(path) -> int
    context_tokens.last_compact_boundary(path) -> dict | None

Context size = last non-sidechain assistant message after the latest compact marker (boundary or
summary line): input + cache_read + cache_creation tokens. 0 when there is no such message (also right
after a compact, before the next assistant reply). Unreadable files and corrupt lines never raise. Stdlib only.
"""
from __future__ import annotations

import json

__all__ = ["current_context", "last_compact_boundary"]


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


def _read(path: str) -> list[str]:
    """Raw lines of the file; empty when unreadable."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.readlines()
    except OSError:
        return []


def _lines(path: str):
    """Yield (1-based line number, parsed dict); skip unreadable files and bad lines."""
    for n, raw in enumerate(_read(path), 1):
        obj = _parse(raw)
        if obj is not None:
            yield n, obj


def _num(v) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def _is_marker(e: dict) -> bool:
    return bool(e.get("isCompactSummary")) or (
        e.get("type") == "system" and e.get("subtype") == "compact_boundary")


def current_context(path: str) -> int:
    total = 0
    for _, e in _lines(path):
        if _is_marker(e):
            total = 0  # usage before a compact no longer describes the context
            continue
        if e.get("type") != "assistant" or e.get("isSidechain"):
            continue
        msg = e.get("message")
        usage = msg.get("usage") if isinstance(msg, dict) else None
        if not isinstance(usage, dict):
            continue
        total = (_num(usage.get("input_tokens")) + _num(usage.get("cache_read_input_tokens"))
                 + _num(usage.get("cache_creation_input_tokens")))
    return total


def last_compact_boundary(path: str) -> dict | None:
    found = None
    for n, e in _lines(path):
        if e.get("type") == "system" and e.get("subtype") == "compact_boundary":
            meta = e.get("compactMetadata")
            meta = meta if isinstance(meta, dict) else {}
            trig = meta.get("trigger")
            found = {"line": n, "pre_tokens": _num(meta.get("preTokens")),
                     "trigger": trig if isinstance(trig, str) else ""}
        elif e.get("isCompactSummary"):
            if found and found["trigger"] and n - found["line"] <= 1:
                continue  # summary that follows its own boundary
            found = {"line": n, "pre_tokens": 0, "trigger": ""}
    return found
