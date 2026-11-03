"""Compact check for the prompt hook: token nudge from the live transcript, else the old timer.

CLI: compact_check.py <sl_dir> [--transcript P] [--host H]
Prints the nudge and exits 0 when there is one, else prints nothing and exits 1. Fails open.
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compact_due  # noqa: E402
import compact_host  # noqa: E402
import compact_log  # noqa: E402
import context_tokens  # noqa: E402


def decode_path(raw: str) -> str:
    """The hook passes the transcript_path value still JSON-escaped; decode it whole."""
    try:
        val = json.loads('"' + raw + '"')
    except ValueError:
        return raw
    return val if isinstance(val, str) else raw


def _readable(path: str) -> bool:
    if not path or not os.path.isfile(path):
        return False
    try:
        with open(path, "rb") as fh:
            fh.read(1)
    except OSError:
        return False
    return True


def run(sl_dir: str, transcript: str, host: str, now: float) -> str:
    if not _readable(transcript):
        return compact_due.nudge_line() if compact_due.is_due(sl_dir, now) else ""
    compact_log.reconcile(sl_dir, transcript, now)
    tokens = context_tokens.current_context(transcript)
    root = os.path.dirname(os.path.abspath(sl_dir))
    limit = compact_due.limit(root)
    if tokens < limit:
        return ""  # readable transcript: the live count (even none yet) is authoritative
    b = context_tokens.last_compact_boundary(transcript)
    req = compact_log.record_nudge(sl_dir, tokens, limit, now,
                                   boundary_line=int(b["line"]) if b else 0, transcript=transcript)
    return compact_due.token_nudge(tokens, limit, compact_host.command(host), req)


def main(argv: list[str]) -> int:
    try:
        sl = argv[1] if len(argv) > 1 else ""
        transcript, host = "", "claude-code"
        i = 2
        while i < len(argv) - 1:
            if argv[i] == "--transcript":
                transcript = decode_path(argv[i + 1])
            elif argv[i] == "--host":
                host = argv[i + 1]
            i += 2
        out = run(sl, transcript, host, time.time()) if sl else ""
    except Exception:
        return 1
    if not out:
        return 1
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
