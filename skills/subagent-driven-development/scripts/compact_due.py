"""Session compact due check and host-neutral nudge text.

    compact_due.is_due(sl_dir, now, window_s=1800) -> bool
    compact_due.nudge_line() -> str

Due when now - stamp >= window_s, where stamp is last-compact mtime if present,
else the earliest launch t from metrics.jsonl (else 0). Stdlib only.
"""
# Complexity notation:
#   L = metrics.jsonl lines, B = file bytes
from __future__ import annotations

import json  # O(1)
from pathlib import Path  # O(1)

__all__ = ["is_due", "nudge_line"]

_DEFAULT_WINDOW_S = 1800.0  # 30 min


def _launch_t(sl: Path) -> float:  # O(L)
    """Earliest launch timestamp in metrics.jsonl, or 0."""
    path = sl / "metrics.jsonl"  # O(1)
    earliest: float | None = None  # O(1)
    try:  # O(1)
        text = path.read_text(encoding="utf-8", errors="replace")  # O(B)
    except OSError:  # O(1)
        return 0.0  # O(1)
    for ln in text.splitlines():  # O(L)
        ln = ln.strip()  # O(1)
        if not ln:  # O(1)
            continue  # O(1)
        try:  # O(1)
            e = json.loads(ln)  # O(w)
        except ValueError:  # O(1)
            continue  # O(1)
        if isinstance(e, dict) and e.get("ev") == "launch" and isinstance(e.get("t"), (int, float)):  # O(1)
            t = float(e["t"])  # O(1)
            earliest = t if earliest is None else min(earliest, t)  # O(1)
    return 0.0 if earliest is None else earliest  # O(1)


def _compact_mtime(sl: Path) -> float | None:  # O(1)
    path = sl / "last-compact"  # O(1)
    try:  # O(1)
        return path.stat().st_mtime  # O(1)
    except OSError:  # O(1)
        return None  # O(1)


def is_due(sl_dir: str, now: float, window_s: float = _DEFAULT_WINDOW_S) -> bool:  # O(L)
    """True when the compact window has elapsed since last compact or session launch."""
    sl = Path(sl_dir)  # O(1)
    stamp = _compact_mtime(sl)  # O(1)
    if stamp is None:  # O(1)
        stamp = _launch_t(sl)  # O(L)
    return (now - stamp) >= window_s  # O(1)


def nudge_line() -> str:  # O(1)
    """Host-neutral line asking the agent to compact the session."""
    return (  # O(1)
        "Session context is stale: please compact now (host compact / summarize), "
        "then continue. Stamp .superlite/last-compact when done."
    )


if __name__ == "__main__":  # O(L) CLI for hooks
    import os  # O(1)
    import sys  # O(1)
    import time  # O(1)

    sl = sys.argv[1] if len(sys.argv) > 1 else ""  # O(1)
    if not sl:  # O(1)
        proj = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()  # O(1)
        sl = str(Path(proj) / ".superlite")  # O(1)
    if is_due(sl, time.time()):  # O(L)
        print(nudge_line())  # O(1)
        sys.exit(0)  # O(1)
    sys.exit(1)  # O(1) not due
