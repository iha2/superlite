#!/usr/bin/env python3
"""Live feed of a superlite run: agent starts and finishes (metrics.jsonl) and ledger lines (progress.md), as they land.

    superlite-cli tail [--dir .superlite] [--once]

Waits for the folder when it does not exist yet. By default prints what is already there, then follows the
files every 2 s until Ctrl-C; `--once` prints what is there and exits (tests, a quick look). Read-only. Stdlib only.
"""
import argparse
import datetime
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import state  # noqa: E402

FILES = ("metrics.jsonl", "progress.md")


def clock(ms) -> str:
    return datetime.datetime.fromtimestamp(ms / 1000).strftime("%H:%M:%S")


def event_line(e: dict, t0: list) -> str | None:
    """One launch or stop record as a feed line; None for any other record. t0 holds the first record's time."""
    if e.get("ev") not in ("launch", "stop"):
        return None
    t = e.get("t") or 0
    if not t0:
        t0.append(t)
    who = str(e.get("type") or "").split(":")[-1]
    head = f"{clock(t)} +{(t - t0[0]) / 1000:5.0f}s"
    aid = str(e.get("agent_id") or "")[:8]
    if e["ev"] == "launch":
        return f"{head}  START  {who:20} {aid}  {e.get('desc') or ''}"
    return f"{head}  DONE   {who:20} {aid}"


def ledger_line(line: str) -> str | None:
    s = line.strip()
    if not s or s.startswith("#"):
        return None
    return f"{datetime.datetime.now():%H:%M:%S}          LEDGER {s[:150]}"


def _read_new(p: Path, name: str, offsets: dict) -> str:  # O(b)
    """What the file gained since `offsets[name]` (updated in place); "" while the file is missing."""
    try:
        if p.stat().st_size < offsets.get(name, 0):  # rewritten from the start (a new run): read it again
            offsets[name] = 0
        with open(p, encoding="utf-8", errors="replace") as f:
            f.seek(offsets.get(name, 0))
            chunk = f.read()
            offsets[name] = f.tell()
    except OSError:  # missing yet
        return ""
    return chunk


def _feed_line(name: str, ln: str, t0: list) -> str | None:  # O(1)
    if name != "metrics.jsonl":
        return ledger_line(ln)
    try:
        e = json.loads(ln)
    except ValueError:  # a torn line while the hooks append
        return None
    return event_line(e, t0) if isinstance(e, dict) else None


def poll(sl_dir: Path, offsets: dict, t0: list) -> list[str]:  # O(b + n)
    """The feed lines for what each file gained since `offsets` (updated in place)."""
    out: list[str] = []
    for name in FILES:
        for ln in _read_new(sl_dir / name, name, offsets).splitlines():
            row = _feed_line(name, ln, t0)
            if row:
                out.append(row)
    return out


def main(argv=None) -> int:  # O(polls * (b + n))
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default=state.DIR_NAME, help=f"the run's {state.DIR_NAME} folder (default {state.DIR_NAME})")
    ap.add_argument("--once", action="store_true", help="print what is there and exit")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    sl_dir = Path(a.dir)
    offsets: dict = {}
    t0: list = []
    if not sl_dir.is_dir():
        print(f"waiting for {sl_dir.resolve()} ...", flush=True)
    try:
        while True:
            for row in poll(sl_dir, offsets, t0):
                print(row, flush=True)
            if a.once:
                return 0
            time.sleep(2)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
