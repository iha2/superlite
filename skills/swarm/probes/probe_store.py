"""Incident log, file selection and the pass cache of the probe runner."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

from probe_common import CODE_EXT, HERE, SKIP_DIRS



INCIDENT_SL = HERE.parent / "scripts" / "superlite-cli"
_append_lock = threading.Lock()


def _append_incident(log: Path, file: str, line: int, context: str, kind: str,
                     resolution: str | None) -> None:
    entry = {"file": file, "line": line, "context": context, "agent": "probe_run", "kind": kind}
    if resolution:
        entry["resolution"] = resolution
    entry["time"] = time.strftime("%H:%M:%S")
    log.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
    with _append_lock:
        fd = os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)


def _log_incidents(log: Path, jobs: list[tuple]) -> None:  # O(j + bytes) total, j = jobs; one locked append, no spawns
    """Write incidents (file, line, context, kind, resolution) in one locked append through the
    incident module, imported in process (no superlite-cli or Python spawn per entry); fall back to direct
    appends when it cannot be imported or rejects an entry."""
    if not jobs:
        return
    try:
        spec = importlib.util.spec_from_file_location(
            "_superlite_incident", INCIDENT_SL.parent / "incident.py")  # no sys.path change, no clash
        incident = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(incident)
        incident.append(str(log), [incident.make_entry(f, line, ctx, agent="probe_run", kind=kind,
                                                       resolution=res)
                                   for f, line, ctx, kind, res in jobs])
        return
    except (OSError, ImportError, SyntaxError, AttributeError, TypeError, ValueError):
        pass  # missing or broken incident module, a rejected entry, I/O: fall back to direct appends
    for f, line, ctx, kind, res in jobs:
        ctx = ctx if len(ctx) <= 200 else ctx[:199] + "…"
        _append_incident(log, f, line, ctx, kind, res)


def _log_incident(log: Path, file: str, line: int, context: str, kind: str,
                  resolution: str | None = None) -> None:
    """Write one incident (see _log_incidents)."""
    _log_incidents(log, [(file, line, context, kind, resolution)])


def read_log(log: Path) -> list[dict]:  # O(log bytes) total
    out = []
    if log.is_file():
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


# ----------------------------------------------------------------------------- files, cache
def norm_file(root: Path, f: str) -> str:  # O(len f) + resolve syscall
    f = f.strip().replace("\\", "/")
    p = Path(f)
    if p.is_absolute():
        try:
            return p.resolve().relative_to(root).as_posix()
        except ValueError:
            return p.as_posix()
    return re.sub(r"^(\./)+", "", f)


def discover_files(root: Path, adapter: Path, probes_dir: Path | None) -> list[str]:  # O(T log T) total, T = tree entries
    base = root / "src" if (root / "src").is_dir() else root
    found = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            if p.suffix in CODE_EXT and p.resolve() != adapter and not (
                    probes_dir and p.resolve().is_relative_to(probes_dir)):
                found.append(p.relative_to(root).as_posix())
    return found


def commit_files(root: Path, sha: str, work: Path | None = None) -> list[str]:  # O(n) + git spawn
    r = subprocess.run(["git", "-C", str(root), "diff-tree", "--no-commit-id", "--name-only", "-r",
                        "--root", "-z", "--relative", sha], capture_output=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"git diff-tree failed: {r.stderr.decode(errors='replace').strip()}")
    names = [n for n in r.stdout.decode("utf-8", "replace").split("\0") if n]
    keep = []
    for n in names:
        p = Path(n)
        if p.suffix in CODE_EXT and not (set(p.parts[:-1]) & SKIP_DIRS) and ((work or root) / n).exists():
            keep.append(p.as_posix())
    return keep


def sha_bytes(*parts: bytes) -> str:  # O(total bytes)
    h = hashlib.sha256()
    for p in parts:
        h.update(hashlib.sha256(p).digest())
    return h.hexdigest()


def load_cache(path: Path) -> dict:  # O(cache bytes)
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d.get("entries"), dict) else {"version": 1, "entries": {}}
    except (OSError, ValueError, AttributeError):
        return {"version": 1, "entries": {}}


def save_cache(path: Path, new_entries: dict) -> None:  # O(cache bytes) read + merge + write
    cache = load_cache(path)
    cache["entries"].update(new_entries)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(cache, indent=1), encoding="utf-8")
    os.replace(tmp, path)
