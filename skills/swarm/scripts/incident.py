#!/usr/bin/env python3
"""Append-only incident log: add, hotspots, summary. Stdlib only."""
import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
import state  # noqa: E402

DEFAULT_LOG = f"{state.DIR_NAME}/incidents.jsonl"
KINDS = ("test-failure", "exception", "workaround", "spec-ambiguity",
         "assumption", "flaky", "perf", "unsure", "verified")
CONFIDENCE = ("low", "medium", "high")
MAX_CONTEXT = 200

if os.name == "nt":
    import msvcrt

    def _lock(f, shared=False):  # msvcrt has no shared mode: readers lock exclusively too
        f.seek(0)
        while True:
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                return
            except OSError:
                time.sleep(0.01)

    def _unlock(f):
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock(f, shared=False):
        fcntl.flock(f.fileno(), fcntl.LOCK_SH if shared else fcntl.LOCK_EX)

    def _unlock(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _die(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(2)


def make_entry(file, line, context, agent=None, kind=None, resolution=None,  # O(c)
               confidence=None, date=None, run=None, module=None, category=None):
    """One validated log entry; ValueError on bad input. date defaults to today, module to the file's parent dir."""
    if line < 0:
        raise ValueError("line must be >= 0")
    if kind is not None and kind not in KINDS:
        raise ValueError(f"kind must be one of: {', '.join(KINDS)}")
    if confidence is not None and confidence not in CONFIDENCE:
        raise ValueError(f"confidence must be one of: {', '.join(CONFIDENCE)}")
    if not file or not context:
        raise ValueError("file and context are required")
    ctx = context
    if len(ctx) > MAX_CONTEXT:
        ctx = ctx[:MAX_CONTEXT - 1] + "…"
    entry = {"file": file, "line": int(line), "context": ctx}
    for key, val in (("agent", agent), ("kind", kind), ("resolution", resolution),
                     ("confidence", confidence)):
        if val is not None:
            entry[key] = val
    entry["time"] = time.strftime("%H:%M:%S")
    entry["date"] = date or time.strftime("%Y-%m-%d")
    entry["module"] = module if module is not None else PurePosixPath(_norm(file)).parent.as_posix()
    for key, val in (("run", run), ("category", category)):
        if val is not None:
            entry[key] = val
    return entry


def append(log, entries):  # O(total c) + one open, lock and fsync for the whole batch
    """Append entries (dicts from make_entry) under one lock: one open and one fsync."""
    if not entries:
        return
    data = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries).encode("utf-8")
    path = Path(log)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab+") as f:
        _lock(f)
        try:
            f.seek(0, os.SEEK_END)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        finally:
            _unlock(f)


def cmd_add(a):  # O(c) + file append O(c) + fsync
    try:
        entry = make_entry(a.file, a.line, a.context, a.agent, a.kind, a.resolution,
                           a.confidence, run=a.run, category=a.category)
    except ValueError as e:
        _die(str(e))
    append(a.log, [entry])
    return 0


def _resolve_rows(text, file, line, evidence):
    """(rewritten lines, count) with the open rows at file:line marked fixed; other rows keep their bytes."""
    out = []
    matched = 0
    for raw in text.splitlines(keepends=True):
        body = raw.rstrip("\r\n")
        ending = raw[len(body):]
        try:
            e = json.loads(body) if body.strip() else None
        except json.JSONDecodeError:
            e = None
        if (isinstance(e, dict) and "file" in e and "line" in e
                and e.get("kind") != "verified"
                and e.get("resolution") in (None, "open")
                and e["line"] == line
                and PurePosixPath(_norm(e["file"])).as_posix() == file):
            e["resolution"] = "fixed"
            e["evidence"] = evidence
            matched += 1
            out.append(json.dumps(e, ensure_ascii=False) + ending)
        else:
            out.append(raw)
    return out, matched


def cmd_resolve(a):  # O(B) + one temp write, replace and fsync under the lock
    """Mark open rows at file:line as fixed with evidence; other rows keep their bytes."""
    file = PurePosixPath(_norm(a.file)).as_posix()
    path = Path(a.log)
    if not path.exists():
        print(f"incident: nothing open at {file}:{a.line}")
        return 1
    matched = 0
    with open(path, "r+b") as f:
        _lock(f)
        try:
            out, matched = _resolve_rows(f.read().decode("utf-8"), file, a.line, a.evidence)
            if matched:  # in place under the lock: add() on this inode never lands in an orphan
                f.seek(0)
                f.write("".join(out).encode("utf-8"))
                f.truncate()
                f.flush()
                os.fsync(f.fileno())
        finally:
            _unlock(f)
    if not matched:
        print(f"incident: nothing open at {file}:{a.line}")
        return 1
    print(f"incident: resolved {matched} at {file}:{a.line}")
    return 0


def read_text(log):  # O(B), one shared lock: never sees a half-done append or in-place rewrite
    """The log's text ("" when missing), read under the same interprocess lock every writer takes."""
    try:
        f = open(log, "rb")
    except FileNotFoundError:
        return ""
    with f:
        _lock(f, shared=True)
        try:
            data = f.read()
        finally:
            _unlock(f)
    return data.decode("utf-8", errors="replace")


def read_all(log):  # O(B)
    """Every JSON object row of the log, hook rows (no file/line) included."""
    out = []
    for ln in read_text(log).splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            e = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if isinstance(e, dict):
            out.append(e)
    return out


def _read(log):  # O(B)
    return [e for e in read_all(log) if "file" in e and "line" in e]


def _norm(p):  # O(len(path))
    return str(p).replace("\\", "/")


def cmd_hotspots(a):  # O(B + i log i)
    entries = _read(a.log)
    if a.files:
        wanted = {_norm(x.strip()) for x in a.files.split(",") if x.strip()}
        entries = [e for e in entries if _norm(e["file"]) in wanted]
    counts = Counter((_norm(e["file"]), e["line"]) for e in entries
                     if e.get("kind") != "verified")

    def key(e):
        loc = (_norm(e["file"]), e["line"])
        verified = e.get("kind") == "verified"
        repeated = not verified and counts[loc] > 1
        unresolved = not verified and e.get("resolution") in (None, "open")
        low = e.get("confidence") == "low"
        return (0 if repeated else 1, 0 if unresolved else 1,
                0 if low else 1, loc[0], loc[1])

    entries.sort(key=key)
    for e in entries[:a.max]:
        print(json.dumps(e, ensure_ascii=False))
    return 0


def cmd_summary(a):  # O(B + i + d log d + f)
    entries = read_all(a.log)
    print(f"total: {len(entries)}")
    for name in ("kind", "resolution"):
        c = Counter(e.get(name, "-") for e in entries)
        print(f"{name}: " + ", ".join(f"{k}={v}" for k, v in sorted(c.items())))
    c = Counter(_norm(e["file"]) for e in entries if "file" in e)
    print("files: " + ", ".join(f"{k}={v}" for k, v in c.most_common(10)))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="incident.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("add")
    s.add_argument("--file", required=True)
    s.add_argument("--line", type=int, required=True)
    s.add_argument("--context", required=True)
    s.add_argument("--agent")
    s.add_argument("--kind")
    s.add_argument("--resolution")
    s.add_argument("--confidence")
    s.add_argument("--run")
    s.add_argument("--category")
    s.add_argument("--log", default=DEFAULT_LOG)
    s.set_defaults(fn=cmd_add)
    s = sub.add_parser("resolve")
    s.add_argument("--file", required=True)
    s.add_argument("--line", type=int, required=True)
    s.add_argument("--evidence", required=True)
    s.add_argument("--log", default=DEFAULT_LOG)
    s.set_defaults(fn=cmd_resolve)
    s = sub.add_parser("hotspots")
    s.add_argument("--files")
    s.add_argument("--log", default=DEFAULT_LOG)
    s.add_argument("--max", type=int, default=30)
    s.set_defaults(fn=cmd_hotspots)
    s = sub.add_parser("summary")
    s.add_argument("--log", default=DEFAULT_LOG)
    s.set_defaults(fn=cmd_summary)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
