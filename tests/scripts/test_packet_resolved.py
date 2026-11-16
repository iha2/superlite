"""review_packet.incident_items: a file:line group whose entries are all resolved (and not low confidence) stays out of the open list."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import review_packet  # noqa: E402


def _entry(resolution: str, confidence: str = "high", file: str = "a.py", line: int = 3) -> dict:
    return {"kind": "unsure", "file": file, "line": line, "resolution": resolution,
            "confidence": confidence, "context": "ctx", "agent": "T1"}


def test_all_resolved_repeated_not_open():
    items = review_packet.incident_items([_entry("fixed"), _entry("fixed")])
    assert [it for it in items if (it.file, it.line) == ("a.py", 3)] == []


def test_one_unresolved_repeated_open():
    items = review_packet.incident_items([_entry("fixed"), _entry("open")])
    mine = [it for it in items if (it.file, it.line) == ("a.py", 3)]
    assert len(mine) == 1
    assert "repeated" in mine[0].title and "unresolved" in mine[0].title


def test_resolved_low_still_listed():
    items = review_packet.incident_items([_entry("fixed", confidence="low")])
    mine = [it for it in items if (it.file, it.line) == ("a.py", 3)]
    assert len(mine) == 1
    assert "low confidence" in mine[0].title
