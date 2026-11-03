"""Nudge ledger for token-based compaction.

    compact_log.record_nudge(sl_dir, tokens, limit, now, boundary_line=0) -> int
    compact_log.reconcile(sl_dir, transcript, now) -> "none" | "pending" | "done"

State lives in <sl_dir>/compact_state.json, events in <sl_dir>/compact.jsonl
(compact_nudge, compact_missed on the 3rd unanswered nudge, compact_done).
Never raises on filesystem errors. Stdlib only.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

__all__ = ["record_nudge", "reconcile"]

context_tokens = None  # lazily imported; tests may replace it
_MISSED_AT = 3


def _ct():
    global context_tokens
    if context_tokens is None:
        import context_tokens as mod
        context_tokens = mod
    return context_tokens


def _state_path(sl_dir: str) -> Path:
    return Path(sl_dir) / "compact_state.json"


def _load(sl_dir: str) -> dict | None:
    try:
        data = json.loads(_state_path(sl_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _save(sl_dir: str, st: dict) -> None:
    try:
        os.makedirs(sl_dir, exist_ok=True)
        _state_path(sl_dir).write_text(json.dumps(st), encoding="utf-8", newline="\n")
    except OSError:
        pass


def _clear(sl_dir: str) -> None:
    try:
        _state_path(sl_dir).unlink()
    except OSError:
        pass


def _log(sl_dir: str, ev: dict) -> None:
    try:
        os.makedirs(sl_dir, exist_ok=True)
        with open(Path(sl_dir) / "compact.jsonl", "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    except OSError:
        pass


def record_nudge(sl_dir: str, tokens: int, limit: int, now: float, boundary_line: int = 0) -> int:
    st = _load(sl_dir) or {}
    try:
        count = int(st.get("count", 0)) + 1
    except (TypeError, ValueError):
        count = 1
        st = {}
    missed = bool(st.get("missed"))
    first_boundary = st.get("boundary_line", boundary_line) if st else boundary_line
    _log(sl_dir, {"ev": "compact_nudge", "t": now, "tokens": tokens, "limit": limit, "n": count})
    if count >= _MISSED_AT and not missed:
        _log(sl_dir, {"ev": "compact_missed", "t": now, "tokens": tokens, "limit": limit, "n": count})
        missed = True
    _save(sl_dir, {"count": count, "tokens": tokens, "limit": limit,
                   "boundary_line": first_boundary, "missed": missed})
    return count


def reconcile(sl_dir: str, transcript: str, now: float) -> str:
    st = _load(sl_dir)
    if not st or not st.get("count"):
        return "none"
    try:
        ct = _ct()
        b = ct.last_compact_boundary(transcript)
        if not b or int(b.get("line", 0)) <= int(st.get("boundary_line", 0)):
            return "pending"
        after = ct.current_context(transcript)
    except (OSError, ValueError, TypeError, ImportError):
        return "pending"
    _log(sl_dir, {"ev": "compact_done", "t": now, "before": st.get("tokens", 0), "after": after})
    _clear(sl_dir)
    return "done"
