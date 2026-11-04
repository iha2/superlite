"""Nudge ledger for token-based compaction.

    compact_log.record_nudge(sl_dir, tokens, limit, now, boundary_line=0, transcript="") -> int
    compact_log.reconcile(sl_dir, transcript, now) -> "none" | "pending" | "done"

State lives in <sl_dir>/compact_state.json as {"sessions": {<transcript path>: entry}}, so sessions
sharing one .superlite keep separate request counts, missed status and boundary lines. Events go to
<sl_dir>/compact.jsonl, each tagged with its transcript (compact_nudge, compact_missed on the 3rd
unanswered nudge, compact_done; compact_done waits for a post-compact measurement).
Never raises on filesystem errors. Stdlib only.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
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


def _load_all(sl_dir: str) -> dict:
    try:
        data = json.loads(_state_path(sl_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    sessions = data.get("sessions") if isinstance(data, dict) else None
    return sessions if isinstance(sessions, dict) else {}


def _load(sl_dir: str, transcript: str) -> dict | None:
    entry = _load_all(sl_dir).get(transcript)
    return entry if isinstance(entry, dict) else None


@contextmanager
def _state_lock(sl_dir: str):
    os.makedirs(sl_dir, exist_ok=True)
    with open(Path(sl_dir) / "compact_state.lock", "a+b") as lock:
        if os.name == "nt":
            import msvcrt
            lock.seek(0, os.SEEK_END)
            if lock.tell() == 0:
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            unlock = lambda: msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            unlock = lambda: fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        try:
            yield
        finally:
            unlock()


def _write(sl_dir: str, sessions: dict) -> None:
    tmp_path = None
    try:
        if not sessions:
            _state_path(sl_dir).unlink(missing_ok=True)
            return
        os.makedirs(sl_dir, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".compact_state.", suffix=".tmp", dir=sl_dir)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"sessions": sessions}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, _state_path(sl_dir))
    except OSError:
        pass
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _save(sl_dir: str, transcript: str, st: dict) -> None:
    sessions = _load_all(sl_dir)
    sessions[transcript] = st
    _write(sl_dir, sessions)


def _clear(sl_dir: str, transcript: str) -> None:
    sessions = _load_all(sl_dir)
    sessions.pop(transcript, None)
    _write(sl_dir, sessions)


def _log(sl_dir: str, ev: dict) -> None:
    try:
        os.makedirs(sl_dir, exist_ok=True)
        with open(Path(sl_dir) / "compact.jsonl", "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    except OSError:
        pass


def record_nudge(sl_dir: str, tokens: int, limit: int, now: float, boundary_line: int = 0,
                 transcript: str = "") -> int:
    count = 1
    try:
        with _state_lock(sl_dir):
            st = _load(sl_dir, transcript) or {}
            try:
                count = int(st.get("count", 0)) + 1
            except (TypeError, ValueError):
                st = {}
            missed = bool(st.get("missed"))
            first_boundary = st.get("boundary_line", boundary_line) if st else boundary_line
            base = {"t": now, "tokens": tokens, "limit": limit, "n": count, "transcript": transcript}
            _log(sl_dir, {"ev": "compact_nudge", **base})
            if count >= _MISSED_AT and not missed:
                _log(sl_dir, {"ev": "compact_missed", **base})
                missed = True
            _save(sl_dir, transcript, {"count": count, "tokens": tokens, "limit": limit,
                                       "boundary_line": first_boundary, "missed": missed})
    except OSError:
        pass
    return count


def reconcile(sl_dir: str, transcript: str, now: float, snapshot: dict | None = None) -> str:
    try:
        with _state_lock(sl_dir):
            st = _load(sl_dir, transcript)
            if not st or not st.get("count"):
                return "none"
            if snapshot is None:
                snapshot = _ct().snapshot(transcript)
            b = snapshot.get("boundary")
            if not b or int(b.get("line", 0)) <= int(st.get("boundary_line", 0)):
                return "pending"
            after = int(snapshot.get("tokens", 0))
            if after <= 0:
                return "pending"  # compacted, but no post-compact measurement yet
            _log(sl_dir, {"ev": "compact_done", "t": now, "before": st.get("tokens", 0), "after": after,
                          "transcript": transcript})
            _clear(sl_dir, transcript)
            return "done"
    except (OSError, ValueError, TypeError, ImportError):
        return "pending"
