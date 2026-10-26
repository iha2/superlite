#!/usr/bin/env python3
"""Live feed of a superlite run: agent starts and finishes (metrics.jsonl) and ledger lines (progress.md), as they land.

    sl tail [--dir .superlite] [--once]

Waits for the folder when it does not exist yet. By default prints what is already there, then follows the
files every 2 s until Ctrl-C; `--once` prints what is there and exits (tests, a quick look). Read-only. Stdlib only.
"""
# Complexity notation:
#   b = new bytes since the last poll, n = lines in them
import argparse  # O(1)
import datetime  # O(1)
import json  # O(1)
import sys  # O(1)
import time  # O(1)
from pathlib import Path  # O(1)

FILES = ("metrics.jsonl", "progress.md")  # O(1)


def clock(ms) -> str:  # O(1)
    return datetime.datetime.fromtimestamp(ms / 1000).strftime("%H:%M:%S")  # O(1)


def event_line(e: dict, t0: list) -> str | None:  # O(1)
    """One launch or stop record as a feed line; None for any other record. t0 holds the first record's time."""
    if e.get("ev") not in ("launch", "stop"):  # O(1)
        return None  # O(1)
    t = e.get("t") or 0  # O(1)
    if not t0:  # O(1)
        t0.append(t)  # O(1)
    who = str(e.get("type") or "").split(":")[-1]  # O(1)
    head = f"{clock(t)} +{(t - t0[0]) / 1000:5.0f}s"  # O(1)
    aid = str(e.get("agent_id") or "")[:8]  # O(1)
    if e["ev"] == "launch":  # O(1)
        return f"{head}  START  {who:20} {aid}  {e.get('desc') or ''}"  # O(1)
    return f"{head}  DONE   {who:20} {aid}"  # O(1)


def ledger_line(line: str) -> str | None:  # O(1)
    s = line.strip()  # O(len)
    if not s or s.startswith("#"):  # O(1)
        return None  # O(1)
    return f"{datetime.datetime.now():%H:%M:%S}          LEDGER {s[:150]}"  # O(1)


def poll(sl_dir: Path, offsets: dict, t0: list) -> list[str]:  # O(b + n)
    """The feed lines for what each file gained since `offsets` (updated in place)."""
    out: list[str] = []  # O(1)
    for name in FILES:  # O(1)
        p = sl_dir / name  # O(1)
        try:  # O(1)
            if p.stat().st_size < offsets.get(name, 0):  # rewritten from the start (a new run): read it again | O(1)
                offsets[name] = 0  # O(1)
            with open(p, encoding="utf-8", errors="replace") as f:  # O(1)
                f.seek(offsets.get(name, 0))  # O(1)
                chunk = f.read()  # O(b)
                offsets[name] = f.tell()  # O(1)
        except OSError:  # missing yet | O(1)
            continue  # O(1)
        for ln in chunk.splitlines():  # O(n)
            if name == "metrics.jsonl":  # O(1)
                try:  # O(1)
                    e = json.loads(ln)  # O(len)
                except ValueError:  # a torn line while the hooks append | O(1)
                    continue  # O(1)
                row = event_line(e, t0) if isinstance(e, dict) else None  # O(1)
            else:  # O(1)
                row = ledger_line(ln)  # O(1)
            if row:  # O(1)
                out.append(row)  # O(1)
    return out  # O(n)


def main(argv=None) -> int:  # O(polls * (b + n))
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])  # O(1)
    ap.add_argument("--dir", default=".superlite", help="the run's .superlite folder (default .superlite)")  # O(1)
    ap.add_argument("--once", action="store_true", help="print what is there and exit")  # O(1)
    a = ap.parse_args(argv)  # O(1)
    sys.stdout.reconfigure(encoding="utf-8")  # O(1)
    sl_dir = Path(a.dir)  # O(1)
    offsets: dict = {}  # O(1)
    t0: list = []  # O(1)
    if not sl_dir.is_dir():  # O(1)
        print(f"waiting for {sl_dir.resolve()} ...", flush=True)  # O(1)
    try:  # O(1)
        while True:  # O(polls)
            for row in poll(sl_dir, offsets, t0):  # O(b + n)
                print(row, flush=True)  # O(1)
            if a.once:  # O(1)
                return 0  # O(1)
            time.sleep(2)  # O(1)
    except KeyboardInterrupt:  # O(1)
        return 0  # O(1)


if __name__ == "__main__":  # O(1)
    sys.exit(main())  # O(1)
