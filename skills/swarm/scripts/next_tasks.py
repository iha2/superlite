#!/usr/bin/env python3
"""Print the ready tasks, longest predicted Est. time first, within the cap.

    next_tasks.py --plan P --committed T1,T3 --running T2 [--cap 10]

Also hosts the small plan-parsing helpers the other run scripts import
(plan sections, Owns expansion, task graph via plan_tools). Stdlib only.
"""

# Complexity notation:
# n = tasks in the plan, e = dependency edges, L = plan text size (chars)
# R = ready tasks (<= n), cap = max parallel tasks, c = committed/running ids passed in
# f = Owns paths per task, P = paths after brace expansion, len = length of the string at hand
# "process spawn" = fork/exec of sl/bash; dominates wall time over the O(...) CPU term
import argparse  # O(1)
import json  # O(1)
import os  # O(1)
import re  # O(1)
import subprocess  # O(1)
import sys  # O(1)
from pathlib import Path  # O(1)

SCRIPTS = Path(__file__).resolve().parent  # O(1)
DEFAULT_PLAN = ".superlite/plan.md"  # O(1)
DEFAULT_TIME = 1.0  # O(1)

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")  # O(1)
_TASK_HEADING = re.compile(r"^###\s+(T\d+[A-Za-z]?)\b(.*)$")  # O(1)


def die(msg, code=2):  # O(len msg)
    print(f"error: {msg}", file=sys.stderr)  # O(1)
    sys.exit(code)  # O(1)


def sl_command(tool, *args):  # O(args)
    """Command line that runs another superlite tool through the `sl` launcher (D18).

    SUPERLITE_SL replaces the launcher (tests); SUPERLITE_BASH picks the bash."""
    launcher = os.environ.get("SUPERLITE_SL") or str(SCRIPTS / "sl")  # O(1)
    return [os.environ.get("SUPERLITE_BASH") or "bash", launcher, tool,  # O(args)
            *map(str, args)]  # O(args)


def run_tool(tool, *args):  # O(output) + process spawn
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}  # cp1252 pipes on Windows | O(env)
    return subprocess.run(sl_command(tool, *args), capture_output=True, text=True,  # O(output) + process spawn
                          encoding="utf-8", errors="replace", env=env)  # O(1)


def in_process():  # O(1)
    """Sibling tools run inside this process (no spawn) unless SUPERLITE_SL swaps the
    launcher, which tests use to put fakes in their place."""
    return not os.environ.get("SUPERLITE_SL")  # O(1)


def sibling(name):  # O(1) after the first import
    """Import a sibling tool module (plan_tools, incident) from this directory."""
    if str(SCRIPTS) not in sys.path:  # O(len sys.path)
        sys.path.insert(0, str(SCRIPTS))  # O(len sys.path)
    return __import__(name)  # O(1) cached after first import


_GRAPHS = {}  # resolved plan path -> {"graph", "seams"}; one parse per process | O(1)


def taskgraph_full(plan):  # O(L + n + e) once per plan per process; O(1) after
    """{"graph": task -> dependencies, "seams": ["Ta↔Tb", ...]} for the plan.

    In process: plan_tools.parse_taskgraph, parsed once and memoized. With SUPERLITE_SL:
    `sl plan_tools taskgraph --format full`."""
    key = str(Path(plan).resolve())  # O(len)
    if key in _GRAPHS:  # O(1) hashmap
        return _GRAPHS[key]  # O(1)
    if in_process():  # O(1)
        pt = sibling("plan_tools")  # O(1)
        try:  # O(1)
            graph = pt.parse_taskgraph(pt.Plan.load(str(plan)))  # O(L + n + e)
        except pt.PlanError as e:  # O(1)
            die(f"taskgraph failed: error: {e}", e.code)  # O(1)
        plan_obj = pt.Plan.load(str(plan))  # O(L) second parse, for the task statuses
        data = {"graph": graph, "seams": pt.seams_of(graph),  # O(e)
                "status": {t: plan_obj.by_id[t].status for t in graph if t in plan_obj.by_id}}  # O(n*k*s)
    else:  # O(1)
        r = run_tool("plan_tools", "taskgraph", "--plan", plan, "--format", "full")  # O(output) + process spawn
        if r.returncode != 0:  # O(1)
            die(f"taskgraph failed: {(r.stderr or r.stdout).strip()}", r.returncode)  # O(1)
        try:  # O(1)
            data = json.loads(r.stdout)  # O(output) parse
        except json.JSONDecodeError as e:  # O(1)
            die(f"taskgraph printed invalid JSON: {e}")  # O(1)
    data = {"graph": {k: list(v) for k, v in (data.get("graph") or {}).items()},  # O(n + e)
            "seams": list(data.get("seams") or []),  # O(e)
            "status": dict(data.get("status") or {})}  # O(n)
    _GRAPHS[key] = data  # O(1)
    return data  # O(1)


def taskgraph(plan):  # O(n + e) (memoized parse, see taskgraph_full)
    """Task -> dependencies."""
    return {k: list(v) for k, v in taskgraph_full(plan)["graph"].items()}  # O(n + e)


def add_incidents(log, items):  # O(total context) + one append; or one spawn per item with SUPERLITE_SL
    """Log incidents; items are (file, line, context, kind, agent, resolution). In process
    they go through incident.append in one locked write; failures only warn."""
    if not items:  # O(1)
        return  # O(1)
    if in_process():  # O(1)
        inc = sibling("incident")  # O(1)
        entries = []  # O(1)
        for f, ln, ctx, k, ag, res in items:  # O(k) iterations; a bad item is skipped, not the batch
            try:  # O(1)
                entries.append(inc.make_entry(str(f), int(ln), ctx, agent=ag, kind=k,  # O(context)
                                              resolution=res or None))
            except (ValueError, TypeError) as e:  # O(1)
                print(f"warning: incident not logged ({f}:{ln}): {e}", file=sys.stderr)  # O(1)
        try:  # O(1)
            inc.append(log, entries)  # O(total context) one locked write
        except OSError as e:  # O(1)
            print(f"warning: incidents not logged: {e}", file=sys.stderr)  # O(1)
        return  # O(1)
    for file, line, context, kind, agent, resolution in items:  # O(k) iterations
        cmd = ["add", "--file", file, "--line", line, "--context", context,  # O(1)
               "--agent", agent, "--kind", kind, "--log", log]  # O(1)
        if resolution:  # O(1)
            cmd += ["--resolution", resolution]  # O(1)
        r = run_tool("incident", *cmd)  # O(output) + process spawn
        if r.returncode != 0:  # O(1)
            print(f"warning: incident not logged: {(r.stderr or r.stdout).strip()}",  # O(1)
                  file=sys.stderr)  # O(1)


def add_incident(log, file, line, context, kind, agent, resolution=None):  # O(context) + one append
    """Log one incident; failures only warn."""
    add_incidents(log, [(file, line, context, kind, agent, resolution)])  # O(context)


def read_text(path):  # O(L)
    p = Path(path)  # O(1)
    if not p.exists():  # O(1)
        die(f"file not found: {path}")  # O(1)
    return p.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")  # O(L)


def parse_plan(plan):  # O(L)
    """{task id: info}, in plan order.

    info: title, est_time (min), est_context (k tokens), complexity (heading `Complexity N`), owns (list), body.
    """
    lines = read_text(plan).split("\n")  # O(L)
    tasks, cur = {}, None  # O(1)
    for line in lines:  # O(L lines) iterations
        m = _TASK_HEADING.match(line)  # O(len line)
        if m:  # O(1)
            cur = {"id": m.group(1), "head": m.group(2), "body": []}  # O(1)
            tasks[cur["id"]] = cur  # O(1)
            continue  # O(1)
        if _HEADING.match(line) and len(_HEADING.match(line).group(1)) <= 3:  # O(len line) regex, run twice
            cur = None  # O(1)
            continue  # O(1)
        if cur is not None:  # O(1)
            cur["body"].append(line)  # O(1)
    out = {}  # O(1)
    for tid, t in tasks.items():  # O(n) iterations
        body = "\n".join(t["body"])  # O(len body)
        head = t["head"]  # O(1)
        parts = [p.strip() for p in head.split("·")]  # O(len head)
        if parts and parts[0] == "":  # O(1)
            parts = parts[1:]  # O(1)
        title = re.sub(r"\s+", " ", parts[0]) if parts else ""  # O(len title)
        out[tid] = {  # O(1)
            "id": tid,  # O(1)
            "title": title or tid,  # O(1)
            "est_time": _num(r"Est\.?\s*time:?\*{0,2}\s*([\d.]+)\s*min", head, body),  # O(len body) regex
            "est_context": _num(r"Est\.?\s*context:?\*{0,2}\s*([\d.]+)\s*k", head, body),  # O(len body) regex
            "complexity": _num(r"Complexity\s+(\d+)", head, ""),  # O(len head) regex, heading only
            "owns": parse_owns(_field(body, "Owns")),  # O(len body + owns text)
            "body": body,  # O(1)
        }  # O(1)
    return out  # O(1)


def _num(pattern, head, body):  # O(len text)
    for text in (head, body):  # O(1) iterations (2 texts)
        m = re.search(pattern, text, re.I)  # O(len text)
        if m:  # O(1)
            return float(m.group(1))  # O(1)
    return None  # O(1)


def _field(body, name):  # O(len body)
    m = re.search(rf"^\*\*{name}:\*\*[ \t]*(.*)$", body, re.M)  # O(len body)
    return m.group(1) if m else ""  # O(1)


def split_top(text, sep=","):  # O(len text)
    """Split on `sep` outside braces, parentheses and backtick spans."""
    parts, depth, tick, cur = [], 0, False, []  # O(1)
    for ch in text:  # O(len text) iterations
        if ch == "`":  # O(1)
            tick = not tick  # O(1)
        elif not tick and ch in "{(":  # O(1)
            depth += 1  # O(1)
        elif not tick and ch in "})":  # O(1)
            depth -= 1  # O(1)
        if ch == sep and depth == 0 and not tick:  # O(1)
            parts.append("".join(cur))  # O(len cur)
            cur = []  # O(1)
        else:  # O(1)
            cur.append(ch)  # O(1)
    parts.append("".join(cur))  # O(len cur)
    return parts  # O(1)


def brace_expand(s):  # O(P * len), P = number of expansions
    m = re.search(r"\{([^{}]*)\}", s)  # O(len)
    if not m:  # O(1)
        return [s]  # O(1)
    out = []  # O(1)
    for alt in m.group(1).split(","):  # O(alts) iterations
        out.extend(brace_expand(s[:m.start()] + alt.strip() + s[m.end():]))  # O(len) + recursion
    return out  # O(1)


def parse_owns(text):  # O(len + P * len)
    """Owned paths from an `**Owns:**` line. A trailing `/` means a directory."""
    text = re.sub(r"\([^()]*\)", "", text)  # parenthetical notes and children | O(len)
    paths = []  # O(1)
    for part in split_top(text):  # O(parts) iterations
        part = part.replace("`", "").strip().rstrip(".;").strip()  # O(len part)
        if not part:  # O(1)
            continue  # O(1)
        for p in brace_expand(part):  # O(P) iterations
            p = p.strip().replace("\\", "/")  # O(len path)
            if p:  # O(1)
                paths.append(p)  # O(1)
    return paths  # O(1)


def _glob_rx(g):  # O(len)
    out, i = "", 0  # O(1)
    while i < len(g):  # O(len)
        if g.startswith("**/", i):  # O(1)
            out, i = out + "(?:.*/)?", i + 3  # O(1)
        elif g[i] == "*":  # O(1)
            out, i = out + "[^/]*", i + 1  # O(1)
        else:  # O(1)
            out, i = out + re.escape(g[i]), i + 1  # O(1)
    return out  # O(1)


def owns_match(path, owns):  # O(f * len)
    path = path.replace("\\", "/")  # O(1)
    for o in owns:  # O(f) iterations
        if "*" in o:  # glob (Wave 0 owns): `**/` also matches the root | O(len)
            if re.fullmatch(_glob_rx(o), path):  # O(len)
                return True  # O(1)
        elif o.endswith("/"):  # O(1)
            if path.startswith(o):  # O(1)
                return True  # O(1)
        elif path == o or path.startswith(o + "/"):  # O(1)
            return True  # O(1)
    return False  # O(1)


def stable_filter(plan):  # O(S) read, or None when the plan has no stable.json
    """Stable task ids from the session (or legacy flat) stable.json, or None if absent/unreadable
    (then every non-draft task is offered, as before). Same resolver as `lint --stable`."""
    try:  # O(1)
        import session as sl_session  # O(1)
        f = sl_session.state_dir(plan) / "stable.json"  # O(S)
    except Exception:  # noqa: BLE001 | O(1)
        return None  # O(1)
    if not f.is_file():  # O(1)
        return None  # O(1)
    try:  # O(1)
        return set(json.loads(f.read_text(encoding="utf-8")).get("stable") or [])  # O(S)
    except (ValueError, AttributeError):  # O(1)
        return None  # O(1)


def csv(value):  # O(len)
    return [x.strip() for x in (value or "").split(",") if x.strip()]  # O(len)


def draft_blocked(graph, status):  # O(n + e)
    """Draft tasks plus every task that depends on one, directly or transitively."""
    blocked = {t for t in graph if (status or {}).get(t) == "draft"}  # O(n)
    changed = True  # O(1)
    while changed:  # O(n) rounds worst case
        changed = False  # O(1)
        for t, deps in graph.items():  # O(n + e) per round
            if t not in blocked and any(d in blocked for d in deps):  # O(deps)
                blocked.add(t)  # O(1)
                changed = True  # O(1)
    return blocked  # O(1)


def ready_tasks(graph, times, committed, running, cap, status=None, stable=None):  # O(n*(n + e) + R log R) worst case (draft closure); O(n + e + R log R) without drafts
    free = cap - len(running)  # O(1)
    if free <= 0:  # O(1)
        return []  # O(1)
    done, busy = set(committed), set(running)  # O(c)
    blocked = draft_blocked(graph, status)  # O(n + e) per round
    ready = [t for t, deps in graph.items()  # O(n + e)
             if t not in done and t not in busy and t not in blocked and all(d in done for d in deps)  # O(1)
             and (stable is None or t in stable)]  # O(1) stable.json ANDed with the draft filter
    order = {t: i for i, t in enumerate(graph)}  # O(n)
    ready.sort(key=lambda t: (-times.get(t, DEFAULT_TIME), order[t]))  # O(R log R)
    return ready[:free]  # O(cap)


def main(argv=None):  # O(L + n + e + R log R) + process spawn
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])  # O(1)
    ap.add_argument("--plan", default=DEFAULT_PLAN)  # O(1)
    ap.add_argument("--committed", default="")  # O(1)
    ap.add_argument("--running", default="")  # O(1)
    ap.add_argument("--cap", type=int, default=None, help="default: superlite.json cap, else 10")  # O(1)
    a = ap.parse_args(argv)  # O(1)
    if str(SCRIPTS) not in sys.path:  # O(1)
        sys.path.insert(0, str(SCRIPTS))  # O(1)
    import project_config  # O(1)
    try:  # O(1)
        cap = project_config.effective_cap(a.cap, Path(".").resolve())  # O(file)
    except project_config.ProjectConfigError as exc:  # O(1)
        print(str(exc), file=sys.stderr)  # O(1)
        return 2  # O(1)
    graph = taskgraph(a.plan)  # O(n + e) + process spawn
    status = taskgraph_full(a.plan).get("status", {})  # O(1) memoized
    info = parse_plan(a.plan)  # O(L)
    times = {t: (info[t]["est_time"] if t in info and info[t]["est_time"] is not None  # O(n)
                 else DEFAULT_TIME) for t in graph}  # O(1)
    for t in ready_tasks(graph, times, csv(a.committed), csv(a.running), cap, status, stable_filter(a.plan)):  # O(cap) iterations after O(n + e + R log R) ready_tasks
        print(t)  # O(1)
    return 0  # O(1)


if __name__ == "__main__":  # O(1)
    sys.exit(main())  # O(1)
