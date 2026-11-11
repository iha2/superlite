#!/usr/bin/env python3
"""Print the ready tasks, longest predicted Est. time first, within the cap.

    next_tasks.py --plan P --checkpointed T1,T3 --running T2 [--cap 10]

Also hosts the small plan helpers the other run scripts import (task info, Owns
matching, the task graph), all read through planfile. Stdlib only.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import planfile  # noqa: E402
import base  # noqa: E402
import state  # noqa: E402

DEFAULT_PLAN = f"{state.DIR_NAME}/plan.md"
DEFAULT_TIME = 1.0


def die(msg, code=2):  # O(len msg)
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def sl_command(tool, *args):  # O(args)
    """Command line that runs another superlite tool through the `superlite-cli` launcher (D18).

    SUPERLITE_CLI replaces the launcher (tests); base.bash picks the bash (SUPERLITE_BASH wins)."""
    launcher = os.environ.get("SUPERLITE_CLI") or str(SCRIPTS / "superlite-cli")
    return [base.bash(), launcher, tool,
            *map(str, args)]


def run_tool(tool, *args):  # O(output) + process spawn
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}  # cp1252 pipes on Windows
    return subprocess.run(sl_command(tool, *args), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)


def in_process():
    """Sibling tools run inside this process (no spawn) unless SUPERLITE_CLI swaps the
    launcher, which tests use to put fakes in their place."""
    return not os.environ.get("SUPERLITE_CLI")


def sibling(name):
    """Import a sibling tool module (incident, project_config) from this directory."""
    return __import__(name)


_PLANS = {}  # resolved plan path -> Plan; one parse per process
_GRAPHS = {}  # resolved plan path -> {"graph", "seams", "status"}


def read_plan(plan):  # O(L) once per plan per process; O(1) after
    """The parsed planfile.Plan; exits with a message when the plan is missing."""
    key = str(Path(plan).resolve())
    if key not in _PLANS:
        try:
            _PLANS[key] = planfile.read(str(plan))
        except planfile.PlanError as e:
            die(str(e))
    return _PLANS[key]


def taskgraph_full(plan):  # O(n + e) once per plan per process; O(1) after
    """{"graph": task -> tasks it waits on, "seams": ["Ta↔Tb", ...], "status": task -> Status}.

    `graph` holds the waited edges only (Plan.waited_edges); `seams` holds every edge."""
    key = str(Path(plan).resolve())
    if key in _GRAPHS:
        return _GRAPHS[key]
    p = read_plan(plan)
    unknown = next((t for e in p.edges for t in e if t not in p.tasks), None)
    if unknown:
        die(f"taskgraph failed: unknown task id {unknown!r} in taskgraph (no such task in plan)")
    order = {t: i for i, t in enumerate(p.tasks)}
    graph = {t: sorted((a for a, b in p.waited_edges() if b == t), key=order.get) for t in p.tasks}
    status = {t: (task.fields.get("status") or ["approved"])[0].strip().lower() for t, task in p.tasks.items()}
    _GRAPHS[key] = {"graph": graph, "seams": [f"{a}↔{b}" for a, b in p.edges], "status": status}
    return _GRAPHS[key]


def taskgraph(plan):  # O(n + e) (memoized parse, see taskgraph_full)
    """Task -> dependencies."""
    return {k: list(v) for k, v in taskgraph_full(plan)["graph"].items()}


def add_incidents(log, items):  # O(total context) + one append; or one spawn per item with SUPERLITE_CLI
    """Log incidents; items are (file, line, context, kind, agent, resolution). In process
    they go through incident.append in one locked write; failures only warn."""
    if not items:
        return
    if in_process():
        inc = sibling("incident")
        entries = []
        for f, ln, ctx, k, ag, res in items:
            try:
                entries.append(inc.make_entry(str(f), int(ln), ctx, agent=ag, kind=k,
                                              resolution=res or None))
            except (ValueError, TypeError) as e:
                print(f"warning: incident not logged ({f}:{ln}): {e}", file=sys.stderr)
        try:
            inc.append(log, entries)
        except OSError as e:
            print(f"warning: incidents not logged: {e}", file=sys.stderr)
        return
    for file, line, context, kind, agent, resolution in items:
        cmd = ["add", "--file", file, "--line", line, "--context", context,
               "--agent", agent, "--kind", kind, "--log", log]
        if resolution:
            cmd += ["--resolution", resolution]
        r = run_tool("incident", *cmd)
        if r.returncode != 0:
            print(f"warning: incident not logged: {(r.stderr or r.stdout).strip()}",
                  file=sys.stderr)


def add_incident(log, file, line, context, kind, agent, resolution=None):  # O(context) + one append
    """Log one incident; failures only warn."""
    add_incidents(log, [(file, line, context, kind, agent, resolution)])


def _num(pattern, *texts):  # O(len text)
    for text in texts:
        m = re.search(pattern, text, re.I)
        if m:
            return float(m.group(1))
    return None


def parse_owns(text):  # O(len)
    """Owned paths from an `**Owns:**` line; parenthetical notes dropped, braces expanded. A trailing `/` means a directory."""
    clean = re.sub(r"\([^()]*\)", "", text)
    paths = planfile.Task("-", fields={"owns": [clean]}).owns
    return [p.replace("\\", "/").rstrip(".;").strip() for p in paths if p.strip(".; ")]


def parse_plan(plan):  # O(L)
    """{task id: info}, in plan order.

    info: id, title, est_time (min), est_context (k tokens), complexity (heading `Complexity N`), owns (list), body."""
    p = read_plan(plan)
    heads = {}
    for ln in p.lines:
        m = planfile.HEADING.match(ln)
        if m:
            heads.setdefault(m.group(1), m.group(2))
    out = {}
    for tid, t in p.tasks.items():
        head = heads.get(tid, "")
        body = "\n".join(f"{k}: {v}" for k, vs in t.fields.items() for v in vs)
        out[tid] = {
            "id": tid,
            "title": t.title or tid,
            "est_time": _num(r"Est\.?\s*time:?\*{0,2}\s*([\d.]+)\s*min", head, body),
            "est_context": _num(r"Est\.?\s*context:?\*{0,2}\s*([\d.]+)\s*k", head, body),
            "complexity": _num(r"Complexity\s+(\d+)", head),
            "owns": parse_owns(" , ".join(t.fields.get("owns", []))),
            "body": body,
        }
    return out


def _glob_rx(g):  # O(len)
    out, i = "", 0
    while i < len(g):
        if g.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif g.startswith("**", i):  # trailing/bare `**`: any depth
            out, i = out + ".*", i + 2
        elif g[i] == "*":
            out, i = out + "[^/]*", i + 1
        else:
            out, i = out + re.escape(g[i]), i + 1
    return out


def owns_match(path, owns):  # O(f * len)
    path = path.replace("\\", "/")
    for o in owns:
        if "*" in o:  # glob (Wave 0 owns): `**/` also matches the root
            if re.fullmatch(_glob_rx(o), path):
                return True
        elif o.endswith("/"):
            if path.startswith(o):
                return True
        elif path == o or path.startswith(o + "/"):
            return True
    return False


def stable_root(plan_path):  # O(1)
    """The folder whose `.superlite` holds this plan's state."""
    root = Path(plan_path).resolve().parent
    if root.name == state.DIR_NAME:
        return root.parent
    if root.name == "plans" and root.parent.name == "docs":
        return root.parent.parent
    return root


def stable_filter(plan):  # O(S) read, or None when the plan has no stable.json
    """Stable task ids from stable.json for this plan, or None if the file is absent
    or unreadable (then every non-draft task is offered)."""
    f = state.path(stable_root(plan), "stable.json")
    if not f.is_file():
        return None
    try:
        return set(json.loads(f.read_text(encoding="utf-8")).get("stable") or [])
    except (ValueError, AttributeError):
        return None


def csv(value):  # O(len)
    return [x.strip() for x in (value or "").split(",") if x.strip()]


def draft_blocked(graph, status):  # O(n + e)
    """Draft tasks plus every task that depends on one, directly or transitively."""
    blocked = {t for t in graph if (status or {}).get(t) == "draft"}
    changed = True
    while changed:
        changed = False
        for t, deps in graph.items():
            if t not in blocked and any(d in blocked for d in deps):
                blocked.add(t)
                changed = True
    return blocked


def ready_tasks(graph, times, checkpointed, running, cap, status=None, stable=None):  # O(n*(n + e) + R log R) worst case (draft closure); O(n + e + R log R) without drafts
    free = cap - len(running)
    if free <= 0:
        return []
    done, busy = set(checkpointed), set(running)
    blocked = draft_blocked(graph, status)
    ready = [t for t, deps in graph.items()
             if t not in done and t not in busy and t not in blocked and all(d in done for d in deps)
             and (stable is None or t in stable)]
    order = {t: i for i, t in enumerate(graph)}
    ready.sort(key=lambda t: (-times.get(t, DEFAULT_TIME), order[t]))
    return ready[:free]


def main(argv=None):  # O(L + n + e + R log R) + process spawn
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--plan", default=DEFAULT_PLAN)
    ap.add_argument("--checkpointed", default="")
    ap.add_argument("--running", default="")
    ap.add_argument("--cap", type=int, default=None, help="default: superlite.json cap, else 10")
    a = ap.parse_args(argv)
    import project_config
    try:
        cap = project_config.effective_cap(a.cap, Path(".").resolve())
    except project_config.ProjectConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    graph = taskgraph(a.plan)
    status = taskgraph_full(a.plan).get("status", {})
    info = parse_plan(a.plan)
    times = {t: (info[t]["est_time"] if t in info and info[t]["est_time"] is not None
                 else DEFAULT_TIME) for t in graph}
    for t in ready_tasks(graph, times, csv(a.checkpointed), csv(a.running), cap, status, stable_filter(a.plan)):
        print(t)
    return 0


if __name__ == "__main__":
    sys.exit(main())
