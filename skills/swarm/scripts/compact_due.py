"""Session compact due check and host-neutral nudge text.

    compact_due.is_due(sl_dir, now, window_s=1800) -> bool
    compact_due.nudge_line() -> str

Due when now - stamp >= window_s, where stamp is last-compact mtime if present,
else the earliest launch t from metrics.jsonl (else 0). Stdlib only.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import project_config  # noqa: E402
import state  # noqa: E402

__all__ = ["is_due", "nudge_line", "limit", "token_nudge"]

_DEFAULT_WINDOW_S = 1800.0  # 30 min
_DEFAULT_TOKENS = 150000


def _positive(value, source: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"compact_tokens from {source} must be a positive integer")
    try:
        n = int(value) if isinstance(value, (int, str)) else None
    except ValueError:
        n = None
    if n is None or n <= 0:
        raise ValueError(f"compact_tokens from {source} must be a positive integer: {value!r}")
    return n


def limit(root: str) -> int:
    """Token limit: env SUPERLITE_COMPACT_TOKENS, else superlite.json compact_tokens, else 150000."""
    env = os.environ.get("SUPERLITE_COMPACT_TOKENS")
    if env is not None and env.strip():
        return _positive(env.strip(), "SUPERLITE_COMPACT_TOKENS")
    data = project_config.load(root)
    if "compact_tokens" in data:
        return _positive(data["compact_tokens"], "superlite.json")
    return _DEFAULT_TOKENS


def _k(n: int) -> str:
    return f"{round(n / 1000)}k"


def token_nudge(tokens: int, limit: int, command: str, request: int = 1) -> str:
    """Nudge text for a token-count trigger; request > 1 means a compact is still pending."""
    if request > 1:
        return (
            f"Compact still pending (request {request}, {_k(tokens)} tokens, limit {_k(limit)}): "
            f"run {command} now before anything else."
        )
    return (
        f"Context is {_k(tokens)} tokens (limit {_k(limit)}): run {command} now, "
        f"then re-read {state.DIR_NAME}/progress.md and continue."
    )


def _launch_t(sl: Path) -> float:  # O(L)
    """Earliest launch timestamp in metrics.jsonl, or 0."""
    path = sl / "metrics.jsonl"
    earliest: float | None = None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0.0
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        if isinstance(e, dict) and e.get("ev") == "launch" and isinstance(e.get("t"), (int, float)):
            t = float(e["t"])
            earliest = t if earliest is None else min(earliest, t)
    return 0.0 if earliest is None else earliest


def _compact_mtime(sl: Path) -> float | None:
    path = sl / "last-compact"
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def is_due(sl_dir: str, now: float, window_s: float = _DEFAULT_WINDOW_S) -> bool:  # O(L)
    """True when the compact window has elapsed since last compact or session launch."""
    sl = Path(sl_dir)
    stamp = _compact_mtime(sl)
    if stamp is None:
        stamp = _launch_t(sl)
    return (now - stamp) >= window_s


def nudge_line() -> str:
    """Host-neutral line asking the agent to compact the session."""
    return (
        "Session context is stale: please compact now (host compact / summarize), "
        f"then continue. Stamp {state.DIR_NAME}/last-compact when done."
    )


if __name__ == "__main__":
    import os
    import time

    sl = sys.argv[1] if len(sys.argv) > 1 else ""
    if not sl:
        proj = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
        sl = str(Path(proj) / state.DIR_NAME)
    if is_due(sl, time.time()):
        print(nudge_line())
        sys.exit(0)
    sys.exit(1)
