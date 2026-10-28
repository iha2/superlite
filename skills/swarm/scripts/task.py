#!/usr/bin/env python3
"""Print one task's block from the CURRENT plan plus the Produces lines of every task it Consumes.

    task.py Tn [--plan P] [--pool .superlite/pool.json]      (as `sl task Tn --plan P`)

The lookup agents use to read their own task and their neighbours instead of asking the coordinator. It reads
the plan file on every call, so it is never stale. Output: the task block verbatim, then `## Consumed
interfaces`: for each producer of an item Tn consumes, its title, Produces lines and `e.g.` examples.
Exit 2 for an unknown task or a missing plan. Language-neutral. Stdlib only.
"""
# Complexity notation:
#   L = plan text size, n = tasks, q = Produces/Consumes items overall
import argparse  # O(1)
import json  # O(1)
import re  # O(1)
import sys  # O(1)
from pathlib import Path  # O(1)

sys.path.insert(0, str(Path(__file__).resolve().parent))  # O(1)
import next_tasks as nt  # noqa: E402 | O(1)
import plan_tools as pt  # noqa: E402 | O(1)


def producers_of(plan, task) -> list:  # O(n*q*s)
    """(ids of the tasks that produce what `task` consumes in task order, consumed items nothing produces)."""
    produced = {}  # signature key or item text -> [task id] | O(1)
    for t in plan.tasks:  # O(n) iterations
        for item in t.produces:  # O(q) iterations
            sig = pt.parse_signature(item)  # O(s)
            produced.setdefault(pt.sig_key(sig) if sig else pt.strip_ticks(item), []).append(t.id)  # O(1)
    found, missing = [], []  # O(1)
    for item in task.consumes:  # O(q) iterations
        sig = pt.parse_signature(item)  # O(s)
        ref = re.search(pt.REF_TAIL, pt.strip_ticks(item))  # O(s)
        refs = re.findall(pt.TASK_ID, ref.group(1)) if ref else []  # O(s)
        cands = produced.get(pt.sig_key(sig) if sig else pt.strip_ticks(item), [])  # O(1)
        if refs:  # O(1)
            cands = [c for c in cands if c in refs] or [r for r in refs if r in plan.by_id]  # O(cands*refs)
        if not cands:  # O(1)
            missing.append(item)  # O(1)
        for c in cands:  # O(cands) iterations
            if c not in found and c != task.id:  # O(found)
                found.append(c)  # O(1)
    return sorted(found, key=pt.nkey), missing  # O(k log k)


def render(plan, tid) -> str:  # O(n*q*s)
    task = plan.by_id.get(tid)  # O(1)
    if task is None:  # O(1)
        raise KeyError(tid)  # O(1)
    out = [task.text.rstrip("\n").rstrip()]  # O(block)
    prods, missing = producers_of(plan, task)  # O(n*q*s)
    if prods or missing:  # O(1)
        out += ["", "## Consumed interfaces"]  # O(1)
    for pid in prods:  # O(k) iterations
        p = plan.by_id[pid]  # O(1)
        out += ["", f"### {p.id} {p.title}"]  # O(1)
        out += [f"Produces: {x}" for x in p.produces] + [f"e.g.: {x}" for x in p.examples]  # O(q)
    for item in missing:  # O(q) iterations
        out += ["", f"Consumes: {item} (no task in the plan produces it)"]  # O(1)
    return "\n".join(out) + "\n"  # O(out)


def main(argv=None):  # O(L + n*q*s)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])  # O(1)
    ap.add_argument("task", help="task id, e.g. T5")  # O(1)
    ap.add_argument("--plan")  # O(1)
    ap.add_argument("--pool", default=".superlite/pool.json")  # O(1)
    a = ap.parse_args(argv)  # O(1)
    if a.pool == ".superlite/pool.json":  # O(1)
        import session as sl_session  # O(1)
        a.pool = sl_session.display_path(sl_session.state_dir(a.plan) / "pool.json")  # O(S)
    plan_path = a.plan  # O(1)
    if not plan_path:  # O(1)
        try:  # O(1)
            plan_path = json.loads(Path(a.pool).read_text(encoding="utf-8")).get("plan")  # O(pool bytes)
        except (OSError, ValueError, AttributeError):  # O(1)
            plan_path = None  # O(1)
    plan_path = plan_path or nt.DEFAULT_PLAN  # O(1)
    try:  # O(1)
        plan = pt.Plan.load(plan_path)  # O(L)
    except pt.PlanError as e:  # O(1)
        print(f"error: {e}", file=sys.stderr)  # O(1)
        return 2  # O(1)
    tid = a.task if a.task in plan.by_id else a.task.upper()  # O(1)
    try:  # O(1)
        text = render(plan, tid)  # O(n*q*s)
    except KeyError:  # O(1)
        print(f"error: no task {a.task} in {plan_path} (tasks: {', '.join(t.id for t in plan.tasks)})", file=sys.stderr)  # O(n)
        return 2  # O(1)
    sys.stdout.write(text)  # O(out)
    return 0  # O(1)


if __name__ == "__main__":  # O(1)
    sys.exit(main())  # O(1)
