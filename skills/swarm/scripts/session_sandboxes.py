"""Session row list of prototype sandboxes: `row["sandboxes"]` holds one entry per topic.

    record(checkout, topic, **fields)  upsert the entry for topic on this checkout's session row (False when none)
    open_entries(row)                  the entries whose status is not discarded or closed

Entry: {topic, path (repo-relative), run_id, status, started, ended}. A status of discarded or closed sets `ended`.
Writes go through session_registry.update (file lock, atomic replace). Never raises: a missing registry, row or
a corrupt file returns False and `session reconcile` reports the drift.
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402
import session_registry as sr  # noqa: E402

DONE = {"discarded", "closed"}


def _upsert(row: dict, topic: str, fields: dict) -> None:
    entries = row.setdefault("sandboxes", [])
    entry = next((e for e in entries if e.get("topic") == topic), None)
    if entry is None:
        entry = {"topic": topic, "path": None, "run_id": None, "status": None,
                 "started": datetime.now().isoformat(timespec="seconds"), "ended": None}
        entries.append(entry)
    entry.update(fields)
    if fields.get("status") in DONE and not fields.get("ended"):
        entry["ended"] = datetime.now().isoformat(timespec="seconds")


def record(checkout: Path, topic: str, **fields) -> bool:  # O(N)
    try:
        here = sr.toplevel(Path(checkout))
        root = sr.hub_root(here)
        reg = sr.read(root)
        row = sr.row_at(reg, here, sr.resolve_host(root, reg))
        if row is None:
            return False
        slug = row["slug"]
        found = []

        def change(r):
            target = sr.find_row(r, slug)
            if target is not None:
                _upsert(target, topic, fields)
                found.append(True)
        sr.update(root, change)
        return bool(found)
    except (base.GitError, OSError, ValueError, KeyError, TypeError, IndexError) as e:
        print(f"session: sandbox record skipped: {e!r}", file=sys.stderr)
        return False


def open_entries(row: dict) -> list[dict]:  # O(n)
    entries = (row or {}).get("sandboxes")
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, dict) and e.get("status") not in DONE]
