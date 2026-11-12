#!/usr/bin/env python3
"""Parsers for the learn-sync inputs: jsonl tails, review lines, fix notes, reports, anonymised outbox rows.

Library module, no CLI of its own (`main` prints this help). Stdlib only."""
import hashlib
import json
import re
import sys
from pathlib import Path

CATEGORIES = ("deep", "numeric", "encoding", "protocol", "file", "state")
TAGS = ("setup", "test", "pairing", "convention")
OUTBOX_FIELDS = ("tool", "count", "requested", "ran", "run")  # allow-list: never command, target, file, path, text
FAMILIES = ("haiku", "sonnet", "opus")

_FINDING = re.compile(
    r"^(?:[-*]\s+)?(?P<file>[^\s:]+):(?P<line>\d+) - (?P<problem>.+?) - (?P<fix>.+) - tier (?P<tier>\d+)\s*$")
_FIX_HEAD = re.compile(r"^# (?P<id>F-(?P<task>[A-Za-z0-9]+)-\d+) · tier (?P<tier>\d+) · round \d+ · "
                       r"(?P<status>NOT_FIXED|FIXED)\s*$")
_LEARNED = re.compile(r"^Learned:\s*(\w+):\s*(.+?)\s*$", re.M)


def read_new_lines(path, offset: int) -> tuple:  # O(n) in bytes read
    """Parse complete JSON-object lines past offset; leave a partial last line; skip bad lines."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return [], offset
    if offset < 0 or offset > len(data):
        offset = 0
    chunk = data[offset:]
    end = chunk.rfind(b"\n") + 1
    rows = []
    for raw in chunk[:end].split(b"\n"):
        try:
            row = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows, offset + end


def is_hook_row(row: dict) -> bool:  # O(1)
    return isinstance(row, dict) and row.get("kind") in ("rule-block", "rule-rewrite")


def parse_findings(text: str, run: str, date: str) -> list:  # O(lines)
    """Rows for every `file:line - problem - fix - tier N` line; other lines are ignored."""
    out = []
    for ln in text.splitlines():
        m = _FINDING.match(ln.strip())
        if not m:
            continue
        problem, fix = m["problem"].strip(), m["fix"].strip()
        hay = (problem + " " + fix).lower()
        cat = next((c for c in CATEGORIES if c in hay), "other")
        out.append({"v": 1, "date": date, "run": run, "file": m["file"], "line": int(m["line"]),
                    "problem": problem, "fix": fix, "tier": int(m["tier"]),
                    "category": cat, "fixed": None})
    return out


def _field(text: str, name: str) -> str:  # O(n)
    m = re.search(rf"^{name}:[ \t]*(.*?)[ \t]*$", text, re.M)
    return m.group(1) if m else ""


def parse_fix_note(text: str) -> dict | None:  # O(n)
    lines = text.splitlines()
    m = _FIX_HEAD.match(lines[0].strip()) if lines else None
    if not m:
        return None
    return {"id": m["id"], "task": m["task"], "tier": int(m["tier"]), "status": m["status"],
            "file": _field(text, "File"), "cause": _field(text, "Cause"), "fix": _field(text, "Fix")}


def parse_learned(report: str) -> dict | None:  # O(n)
    m = _LEARNED.search(report)
    if not m or m[1] not in TAGS:
        return None
    return {"tag": m[1], "text": m[2]}


def _family(model: str) -> str:  # O(1)
    low = str(model).lower()
    return next((f for f in FAMILIES if f in low), low)


def model_mismatches(launches: list, dispatch: dict) -> list:  # O(n)
    out = []
    for row in launches:
        if not isinstance(row, dict) or row.get("ev") != "launch":
            continue
        want = dispatch.get(row.get("agent_id"))
        if isinstance(want, dict):
            want = want.get("model")
        if not want or not row.get("model"):
            continue
        ran = _family(row["model"])
        if ran != str(want).lower():
            out.append({"requested": want, "ran": ran})
    return out


def outbox_row(signal: str, repo_id: str, date: str, version: str, **fields) -> dict:  # O(1)
    """An anonymised outbox row: hashed repo id plus allow-listed fields only."""
    row = {"v": 1, "date": date, "repo": hashlib.sha256(repo_id.encode("utf-8")).hexdigest()[:16],
           "superlite": version, "signal": signal}
    for k in OUTBOX_FIELDS:
        if k in fields:
            row[k] = fields[k]
    return row


def main(argv=None) -> int:  # O(1)
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
