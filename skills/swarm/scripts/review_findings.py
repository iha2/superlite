#!/usr/bin/env python3
"""review_findings record [--file REPLY.txt]: parse the final reviewer's `file:line - problem - fix - tier N` lines
(from the file, or stdin) into review-findings.jsonl in the run folder, then run `learn sync` as an advisory step.
Prints "review_findings: N recorded"; exits 0 even with no matching lines. Stdlib only."""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402
import incident  # noqa: E402
import learn_signals  # noqa: E402
import run_archive  # noqa: E402
import state  # noqa: E402

FINDINGS_FILE = "review-findings.jsonl"


def _read_text(src: str | None) -> str:  # O(n)
    if src:
        return Path(src).read_text(encoding="utf-8", errors="replace")
    return sys.stdin.buffer.read().decode("utf-8", errors="replace")


def _sync() -> None:  # O(sync)
    """Advisory: absent learn module or any advisory failure skips the sync."""
    try:
        import learn  # noqa: PLC0415  (lazy: written by a later task)
    except ImportError:
        return
    try:
        learn.main(["sync"])
    except (*base.advisory_errors(), NotImplementedError):  # NotImplementedError: learn.py stub until T7 lands
        pass


def cmd_record(a) -> int:  # O(n)
    cwd = Path.cwd()
    sl = state.run_dir(cwd)
    try:
        text = _read_text(a.file)
    except OSError as e:
        print(f"review_findings: cannot read input: {e}", file=sys.stderr)
        return 0
    cur = run_archive.read_current_run(sl)
    rows = learn_signals.parse_findings(text, cur["id"] if cur else "", time.strftime("%Y-%m-%d"))
    if rows:
        try:
            incident.append(str(sl / FINDINGS_FILE), rows)
        except OSError as e:
            print(f"review_findings: cannot write: {e}", file=sys.stderr)
            return 0
    print(f"review_findings: {len(rows)} recorded")
    _sync()
    return 0


def main(argv=None) -> int:  # O(n)
    p = argparse.ArgumentParser(prog="review_findings", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("record", help="record findings from a reviewer reply")
    s.add_argument("--file", help="reply text file (default: stdin)")
    s.set_defaults(fn=cmd_record)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
