#!/usr/bin/env python3
"""Print one task's block from the CURRENT plan plus the Produces lines of every task it Consumes.

    task.py Tn [--plan P] [--pool .superlite/pool.json]      (as `superlite-cli task Tn --plan P`)

The lookup agents use to read their own task and their neighbours instead of asking the coordinator. It reads
the plan file on every call, so it is never stale. Output: the task block verbatim, then `## Consumed
interfaces`: for each producer of an item Tn consumes, its title, Produces lines and `e.g.` examples.
Exit 2 for an unknown task or a missing plan. Language-neutral. Stdlib only.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import next_tasks as nt  # noqa: E402
import plan_tools as pt  # noqa: E402
import state  # noqa: E402


def producers_of(plan, task) -> list:  # O(n*q*s)
    """(ids of the tasks that produce what `task` consumes in task order, consumed items nothing produces)."""
    produced = {}  # signature key or item text -> [task id]
    for t in plan.tasks:
        for item in t.produces:
            sig = pt.parse_signature(item)
            produced.setdefault(pt.sig_key(sig) if sig else pt.strip_ticks(item), []).append(t.id)
    found, missing = [], []
    for item in task.consumes:
        sig = pt.parse_signature(item)
        ref = re.search(pt.REF_TAIL, pt.strip_ticks(item))
        refs = re.findall(pt.TASK_ID, ref.group(1)) if ref else []
        cands = produced.get(pt.sig_key(sig) if sig else pt.strip_ticks(item), [])
        if refs:
            cands = [c for c in cands if c in refs] or [r for r in refs if r in plan.by_id]
        if not cands:
            missing.append(item)
        for c in cands:
            if c not in found and c != task.id:
                found.append(c)
    return sorted(found, key=pt.nkey), missing


def render(plan, tid) -> str:  # O(n*q*s)
    task = plan.by_id.get(tid)
    if task is None:
        raise KeyError(tid)
    out = [task.text.rstrip("\n").rstrip()]
    prods, missing = producers_of(plan, task)
    if prods or missing:
        out += ["", "## Consumed interfaces"]
    for pid in prods:
        p = plan.by_id[pid]
        out += ["", f"### {p.id} {p.title}"]
        out += [f"Produces: {x}" for x in p.produces] + [f"e.g.: {x}" for x in p.examples]
    for item in missing:
        out += ["", f"Consumes: {item} (no task in the plan produces it)"]
    return "\n".join(out) + "\n"


def main(argv=None):  # O(L + n*q*s)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("task", help="task id, e.g. T5")
    ap.add_argument("--plan")
    ap.add_argument("--pool", default=f"{state.DIR_NAME}/pool.json")
    a = ap.parse_args(argv)
    plan_path = a.plan
    if not plan_path:
        try:
            plan_path = json.loads(Path(a.pool).read_text(encoding="utf-8")).get("plan")
        except (OSError, ValueError, AttributeError):
            plan_path = None
    plan_path = plan_path or nt.DEFAULT_PLAN
    try:
        plan = pt.Plan.load(plan_path)
    except pt.PlanError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    tid = a.task if a.task in plan.by_id else a.task.upper()
    try:
        text = render(plan, tid)
    except KeyError:
        print(f"error: no task {a.task} in {plan_path} (tasks: {', '.join(t.id for t in plan.tasks)})", file=sys.stderr)
        return 2
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
