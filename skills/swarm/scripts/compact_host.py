"""Per-host compact command lookup.

    compact_host.command(host: str) -> str
    compact_host.confidence(host: str) -> str

Rows live in skills/swarm/config/compact.json, keyed by host name, each with
"command" and "confidence". Unknown hosts, and rows with an empty command, get
the fallback text from command(). Stdlib only.
"""
from __future__ import annotations

import json
from pathlib import Path

__all__ = ["command", "confidence"]

_CONFIG = Path(__file__).resolve().parent.parent / "config" / "compact.json"
_FALLBACK = (
    "your host's compact command (none known: write a handoff note and start a fresh session)"
)
_NONE = "none"


def _rows() -> dict:
    try:
        data = json.loads(_CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _row(host: str) -> dict:
    row = _rows().get(host) if isinstance(host, str) else None
    return row if isinstance(row, dict) else {}


def command(host: str) -> str:
    """Compact command for host, or the fallback text when none is known."""
    cmd = _row(host).get("command")
    if isinstance(cmd, str) and cmd:
        return cmd
    return _FALLBACK


def confidence(host: str) -> str:
    """Confidence of the host's compact command, 'none' for unknown hosts."""
    conf = _row(host).get("confidence")
    return conf if isinstance(conf, str) and conf else _NONE
