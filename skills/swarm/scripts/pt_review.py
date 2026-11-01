#!/usr/bin/env python3
"""Review-side plan tools: adapter, packet, contract.

    pt_review.py adapter  --plan PLAN.md [--root .] [--out .superlite/probes/adapter.py]
    pt_review.py packet   --plan PLAN.md [--root .] [--log F] [--probes DIR] [--out F]
                          [--max-tokens 20000] [--tests F] [--base REF]
    pt_review.py contract --plan PLAN.md [--root .] [--seam Ta,Tb] [--log F] [--timeout 300]

A `contract --seam` run logs to the run folder's incidents.jsonl by default: each mismatch is an
`exception` incident, a pass is a `verified` entry.

This is the entry the plan tool dispatches to (`main(argv)`); the bodies live in review_adapter,
review_packet and review_contract, and this module re-exports what other scripts import.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _pyrun as pyrun  # noqa: E402
import planfile  # noqa: E402
import state  # noqa: E402
from review_adapter import build_adapter, cmd_adapter, check_data_contract  # noqa: E402,F401
from review_adapter import check_shape, parse_shape  # noqa: E402,F401
from review_contract import (TEST_PATH_RE, Mismatch, ToolError, check_interfaces, consumes_ids,  # noqa: E402,F401
                             owned_files, project_env)
from review_packet import cmd_packet, focus_packages, read_test_summary, source_excerpts  # noqa: E402,F401

parse_plan = planfile.read  # for importers that read a plan through this module


def rerun_consumer_tests(root: Path, consumer: planfile.Task, timeout: float  # O(X) + 1 pytest subprocess total
                         ) -> tuple[list[Mismatch], list[str]]:
    files = [o for o in owned_files(consumer) if o.endswith(".py") and TEST_PATH_RE.search(o) and (root / o).is_file()]
    if not files:
        return [], [f"part3 skipped: {consumer.id} owns no existing Python test files"]
    try:
        cmd = [*pyrun.pytest_cmd(), "-q", "-x", "--no-header", "-p", "no:cacheprovider", *files]
    except pyrun.PytestUnavailable as e:
        return [], [f"part3 skipped: {e}"]
    try:
        proc = pyrun.run_tree(cmd, cwd=root, env=project_env(root, SUPERLITE_REAL="1"), capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return [Mismatch(3, files[0], 1, f"{consumer.id} tests hung (> {timeout:.0f}s) with SUPERLITE_REAL=1",
                         consumer.id)], []
    if proc.returncode == 0:
        return [], []
    if proc.returncode == 5:
        return [], [f"part3 skipped: pytest collected no tests in {', '.join(files)}"]
    out = proc.stdout + proc.stderr
    loc = re.search(r"^([^\s:]+\.py):(\d+): ", out, re.M)
    last = next((ln for ln in reversed(out.splitlines()) if ln.strip()), "pytest failed")
    file, line = (loc.group(1).replace("\\", "/"), int(loc.group(2))) if loc else (files[0], 1)
    return [Mismatch(3, file, line, f"{consumer.id} tests fail with the real producer "
                                    f"(SUPERLITE_REAL=1): {last.strip()}", consumer.id)], []


def resolve_seam(plan: planfile.Plan, seam: str) -> tuple[planfile.Task, planfile.Task]:  # O(E + m) total
    parts = [s.strip() for s in seam.split(",")]
    if len(parts) != 2 or any(p not in plan.tasks for p in parts):
        raise ToolError(f"--seam needs two task ids that exist in the plan, got {seam!r}")
    a, b = parts
    ta, tb = plan.tasks[a], plan.tasks[b]
    if (a, b) in plan.edges or a in consumes_ids(tb):
        return ta, tb
    if (b, a) in plan.edges or b in consumes_ids(ta):
        return tb, ta
    raise ToolError(f"no seam between {a} and {b} (no edge in %% taskgraph, no Consumes tag)")


def append_log(path: Path, entry: dict) -> None:  # O(len(entry)) + file open total
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _check_seam(plan: planfile.Plan, root: Path, seam: str, timeout: float):  # O(parts 1-3) total
    """Run the three parts for one seam; return (mismatches, notes)."""
    producer, consumer = resolve_seam(plan, seam)
    mismatches, n = check_interfaces(plan, root, [producer.id])
    notes = [f"part1: {producer.id} has no parseable Produces signatures"] if n == 0 else []
    m2, n2 = check_data_contract(plan, root, producer, consumer)
    m3, n3 = rerun_consumer_tests(root, consumer, timeout)
    return mismatches + m2 + m3, notes + n2 + n3


def _log_result(log: Path, label: str, mismatches: list[Mismatch], seam: bool) -> None:
    stamp = time.strftime("%H:%M:%S")
    for m in mismatches:
        append_log(log, {"file": m.file, "line": m.line, "context": f"{label}: {m.msg}"[:200],
                         "agent": "pt_review", "kind": "exception", "confidence": "high",
                         "resolution": "open", "time": stamp})
    if not mismatches and seam:
        append_log(log, {"file": "", "line": 0, "context": f"verified: {label}", "agent": "pt_review",
                         "kind": "verified", "time": stamp})


def cmd_contract(a: argparse.Namespace) -> int:  # O(parts above + Mm file opens) total
    plan = planfile.read(a.plan)
    root = Path(a.root).resolve()
    if a.seam:
        label = "seam " + "↔".join(s.strip() for s in a.seam.split(","))
        mismatches, notes = _check_seam(plan, root, a.seam, a.timeout)
    else:
        label = "contract"
        mismatches, n = check_interfaces(plan, root, None)
        notes = [f"part1: {n} Produces signature(s) checked"]
    for note in notes:
        print(f"note: {note}")
    for m in mismatches:
        print(m)
    # A seam run always logs: mismatches become incidents, a pass becomes `verified`.
    log = Path(a.log) if a.log else (state.path(root, "incidents.jsonl") if a.seam else None)
    if log:
        _log_result(log, label, mismatches, bool(a.seam))
    if mismatches:
        print(f"FAIL: {label}: {len(mismatches)} mismatch(es)")
        return 1
    print(f"PASS: verified: {label}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="pt_review.py", description="Review-side plan tools.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("adapter", "packet", "contract"):
        sp = sub.add_parser(name)
        sp.add_argument("--plan", required=True)
        sp.add_argument("--root", default=".")
        if name == "adapter":
            sp.add_argument("--out")
        elif name == "packet":
            sp.add_argument("--log")
            sp.add_argument("--probes")
            sp.add_argument("--out")
            sp.add_argument("--max-tokens", type=int, default=20000)
            sp.add_argument("--tests", help="test summary JSON (default: the run folder's test-summary.json)")
            sp.add_argument("--base", help="git ref for the diff stat and source excerpts (default: the root commit)")
        else:
            sp.add_argument("--seam", help="Ta,Tb: run parts 1-3 for that seam")
            sp.add_argument("--log", help="append a verified/incident entry to this JSONL log "
                                          "(--seam default: the run folder's incidents.jsonl)")
            sp.add_argument("--timeout", type=float, default=300.0, help="consumer test timeout, seconds")
    return ap


def main(argv: list[str]) -> int:
    try:
        a = build_parser().parse_args(argv)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 2
    try:
        return {"adapter": cmd_adapter, "packet": cmd_packet, "contract": cmd_contract}[a.cmd](a)
    except (ToolError, planfile.PlanError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    sys.exit(main(sys.argv[1:]))
