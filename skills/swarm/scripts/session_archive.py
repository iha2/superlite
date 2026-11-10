"""Archive manifest for closed sessions: <hub>/.superlite/session-store/<slug>/session.json."""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import state  # noqa: E402


def path_for(root: Path, slug: str) -> Path:
    return root / state.DIR_NAME / "session-store" / slug / "session.json"


def write(root: Path, row: dict, wip_ref: str | None, now_ms: int) -> Path:
    data = {**row, "closed_at": now_ms, "wip_ref": wip_ref}
    target = path_for(root, str(row["slug"]))
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".session-", suffix=".tmp")
    done = False
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(data, indent=1) + "\n")
        os.replace(tmp, target)
        done = True
    finally:
        if not done:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
    return target


def read(root: Path, slug: str) -> dict | None:
    try:
        data = json.loads(path_for(root, slug).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def slugs(root: Path) -> list[str]:
    store = root / state.DIR_NAME / "session-store"
    if not store.is_dir():
        return []
    return sorted(p.name for p in store.iterdir() if p.is_dir() and (p / "session.json").is_file())
