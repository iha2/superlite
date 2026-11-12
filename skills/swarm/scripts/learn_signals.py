#!/usr/bin/env python3
"""Parsers for the learn-sync inputs: jsonl tails, review lines, fix notes, reports, anonymised outbox rows.

Library module, no CLI of its own (`main` prints this help). Stdlib only."""
import hashlib
import json
import locale
import re
import sys
from pathlib import Path

CATEGORIES = ("deep", "numeric", "encoding", "protocol", "file", "state")
TAGS = ("setup", "test", "pairing", "convention")
OUTBOX_FIELDS = ("id", "tool", "count", "requested", "ran", "run")  # allow-list: never command, target, file, path, text
FAMILIES = ("haiku", "sonnet", "opus")

_FINDING = re.compile(
    r"^(?:[-*]\s+)?(?P<file>[^\s:]+):(?P<line>\d+) - (?P<problem>.+?) - (?P<fix>.+) - tier (?P<tier>\d+)\s*$")
# Separator-tolerant: a note saved in a legacy code page (the `·` read back as another char) still parses.
_FIX_HEAD = re.compile(r"^# (?P<id>F-(?P<task>[A-Za-z0-9]+)-\d+)\s+\S{1,3}\s+tier (?P<tier>\d+)\s+\S{1,3}\s+"
                       r"round \d+\s+\S{1,3}\s+(?P<status>NOT_FIXED|FIXED)\s*$")
_SPAN_FILE = re.compile(r"(?P<file>[^\s,;:()`]+):\d")
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


def decode_note(data: bytes) -> str:  # O(n)
    """Fix-note bytes as text: UTF-8 (BOM stripped), else the locale code page (a Windows editor's cp1252), else latin-1."""
    for enc in ("utf-8-sig", locale.getpreferredencoding(False) or "latin-1", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("latin-1")


def parse_fix_note(text: str) -> dict | None:  # O(n)
    """The frozen fix note (skills/swarm/fixing.md): heading, then Goal, Where, Changed, Tried and failed, Evidence, Next.
    `file` is the first `path:line` span of Where (else of Changed); `what`/`why` come from Changed's `file:lines - what - why`."""
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.splitlines()
    m = _FIX_HEAD.match(lines[0].strip()) if lines else None
    if not m:
        return None
    where, changed = _field(text, "Where"), _field(text, "Changed")
    span = _SPAN_FILE.search(where) or _SPAN_FILE.search(changed)
    parts = [p.strip() for p in changed.split(" - ")]
    head_is_span = len(parts) > 1 and _SPAN_FILE.match(parts[0]) is not None
    return {"id": m["id"], "task": m["task"], "tier": int(m["tier"]), "status": m["status"],
            "goal": _field(text, "Goal"), "where": where, "changed": changed, "evidence": _field(text, "Evidence"),
            "file": span["file"].replace("\\", "/") if span else "",
            "what": parts[1] if head_is_span else "", "why": " - ".join(parts[2:]) if head_is_span else ""}


def parse_learned(report: str) -> dict | None:  # O(n)
    m = _LEARNED.search(report)
    if not m or m[1] not in TAGS:
        return None
    return {"tag": m[1], "text": m[2]}


def _family(model: str) -> str:  # O(1)
    low = str(model).lower()
    return next((f for f in FAMILIES if f in low), low)


def agent_tiers(agents_dir) -> dict:  # O(files)
    """Agent type name -> requested model family, from each agent file's `model:` frontmatter line."""
    out: dict = {}
    try:
        files = sorted(Path(agents_dir).glob("*.md"))
    except OSError:
        return out
    for f in files:
        try:
            m = re.search(r"^model:\s*(\S+)\s*$", f.read_text(encoding="utf-8", errors="replace"), re.M)
        except OSError:
            continue
        if m:
            out[f.stem] = _family(m.group(1))
    return out


def requested_family(agent_type: str, tiers: dict) -> str:  # O(families)
    """The model an agent type asks for: its agent file's model (`superlite:fixer-opus` -> opus), else a family named
    in the type (`implementer-haiku` -> haiku), else "" (unknown, so never a mismatch)."""
    name = str(agent_type or "").rsplit(":", 1)[-1]
    if name in tiers:
        return tiers[name]
    return next((f for f in FAMILIES if f in name.lower()), "")


def model_mismatches(launches: list, tiers: dict) -> list:  # O(n)
    """{requested, ran} for each launch whose resolved model family differs from what its agent type asks for.
    dispatch.json holds only batches, so the requested tier comes from the launch's own agent type."""
    out = []
    for row in launches:
        if not isinstance(row, dict) or row.get("ev") != "launch":
            continue
        want = requested_family(row.get("type", ""), tiers)
        if not want or not row.get("model"):
            continue
        ran = _family(row["model"])
        if ran != want:
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
