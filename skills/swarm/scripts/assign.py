#!/usr/bin/env python3
"""Pick the idle agent for a task from the task graph and each agent's history.

    assign.py --task Tn --pool .superlite/pool.json [--plan P]

stdout is one word: `agent=<id>` (clear winner), `poll` (tie or no signal, ask
the eligible idle agents), or `fresh` (no eligible idle agent). Ineligible
agents are listed on stderr as `evict=<id> <reason>`.

pool.json: {"plan": "path (optional)", "agents": [{"id": "a1",
  "tasks": ["T1"], "files": ["src/x.py"], "context_k": 30, "idle_min": 5,
  "busy": false}]}

Scoring (D11): +5 if the agent built a task this one depends on, +3 per owned
or read file this task touches, +1 for the same package (directory). A winner
needs a margin of at least 3. Eligible: context_k + Est. context <= 80, fewer
than 3 tasks done, idle <= 50 min. Busy agents are never candidates.
"""

# Complexity notation:
# n = tasks in the plan, e = dependency edges, L = plan text size
# a = agents in the pool (<= ~10), t = tasks done per agent (< 3), F = files known per agent (own files + owned by its tasks)
# o = Owns paths of the task being assigned, d = its dependencies, len = path length
# "process spawn" = fork/exec of sl/bash; dominates wall time over the O(...) CPU term
import argparse  # O(1)
import json  # O(1)
import posixpath  # O(1)
import sys  # O(1)
from pathlib import Path  # O(1)

sys.path.insert(0, str(Path(__file__).resolve().parent))  # O(1)
import next_tasks as nt  # noqa: E402 | O(1)

MAX_CONTEXT_K = 80.0  # O(1)
MAX_TASKS = 3  # O(1)
MAX_IDLE_MIN = 50.0  # O(1)
MARGIN = 3  # O(1)
DEFAULT_CONTEXT_K = 20.0  # O(1)


def package(path):  # O(len path)
    return posixpath.dirname(path.replace("\\", "/"))  # O(len path)


def eligibility(agent, est_context, limits=None):  # O(1)
    """None when eligible, else a short reason. `limits` (config/routing.json, per tier) overrides the defaults."""
    lim = limits or {}  # O(1)
    max_ctx = float(lim.get("max_context_k", MAX_CONTEXT_K))  # O(1)
    max_tasks = int(lim.get("max_tasks", MAX_TASKS))  # O(1)
    max_idle = float(lim.get("max_idle_min", MAX_IDLE_MIN))  # O(1)
    if float(agent.get("context_k", 0)) + est_context > max_ctx:  # O(1)
        return f"context over {max_ctx:g}k"  # O(1)
    if len(agent.get("tasks", [])) >= max_tasks:  # O(1)
        return f"{max_tasks} tasks done"  # O(1)
    if float(agent.get("idle_min", 0)) > max_idle:  # O(1)
        return f"idle over {max_idle:g} min"  # O(1)
    return None  # O(1)


def score(agent, task_id, deps, owns, info):  # O(t + d + o*F*len)
    s = 0  # O(1)
    built = set(agent.get("tasks", []))  # O(t)
    if built & set(deps):  # O(t + d)
        s += 5  # O(1)
    files = {f.replace("\\", "/") for f in agent.get("files", [])}  # O(F*len)
    for t in built:  # O(t) iterations
        files.update(info.get(t, {}).get("owns", []))  # O(owns of t)
    s += 3 * sum(1 for o in owns if any(nt.owns_match(f, [o]) for f in files))  # O(o*F*len) nested any() over owns x files
    pkgs = {package(f) for f in files if package(f)}  # O(F*len)
    if any(package(o) in pkgs for o in owns if package(o)):  # O(o*len)
        s += 1  # O(1)
    return s  # O(1)


def decide(scores):  # O(a log a)
    """scores: {id: score} of eligible agents -> 'agent=<id>' | 'poll' | 'fresh'."""
    if not scores:  # O(1)
        return "fresh"  # O(1)
    ranked = sorted(scores.values(), reverse=True)  # O(a log a)
    top = ranked[0]  # O(1)
    second = ranked[1] if len(ranked) > 1 else 0  # O(1)
    if top > 0 and top - second >= MARGIN:  # O(1)
        winner = [k for k, v in scores.items() if v == top][0]  # O(a)
        return f"agent={winner}"  # O(1)
    return "poll"  # O(1)


def main(argv=None):  # O(L + a*o*F*len) + process spawn
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])  # O(1)
    ap.add_argument("--task", required=True)  # O(1)
    ap.add_argument("--pool", default=".superlite/pool.json")  # O(1)
    ap.add_argument("--plan")  # O(1)
    a = ap.parse_args(argv)  # O(1)
    pool_path = Path(a.pool)  # O(1)
    pool = json.loads(pool_path.read_text(encoding="utf-8")) if pool_path.exists() \
        else {"agents": []}  # O(1)
    plan = a.plan or pool.get("plan") or nt.DEFAULT_PLAN  # O(1)
    graph = nt.taskgraph(plan)  # O(n + e) + process spawn
    info = nt.parse_plan(plan)  # O(L)
    if a.task not in graph and a.task not in info:  # O(1)
        nt.die(f"unknown task {a.task}")  # O(1)
    task = info.get(a.task, {})  # O(1)
    deps = graph.get(a.task, [])  # O(1)
    owns = task.get("owns", [])  # O(1)
    est_context = task.get("est_context") or DEFAULT_CONTEXT_K  # O(1)
    scores = {}  # O(1)
    for agent in pool.get("agents", []):  # O(a) iterations
        if agent.get("busy") or agent.get("status") == "busy":  # O(1)
            continue  # O(1)
        why = eligibility(agent, est_context)  # O(1)
        if why:  # O(1)
            print(f"evict={agent['id']} {why}", file=sys.stderr)  # O(1)
            continue  # O(1)
        scores[agent["id"]] = score(agent, a.task, deps, owns, info)  # O(t + d + o*F*len)
    print(decide(scores))  # O(1)
    return 0  # O(1)


if __name__ == "__main__":  # O(1)
    sys.exit(main())  # O(1)
