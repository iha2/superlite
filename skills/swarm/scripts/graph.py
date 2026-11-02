#!/usr/bin/env python3
"""Mermaid flowchart of a plan's %% taskgraph block, for the chat to draw.

    graph.py --plan PATH
    graph.render(plan_path, critical=None) -> str

Writes graph.txt in the run folder (cached by plan SHA-256, the drawing format and the critical ids).
Always `flowchart TD` (the `graph` keyword does not render in Claude Code). Nodes are
labelled with id and title, in task order; critical-path tasks get the `critical`
class: the given ids, else the longest path by task count. Stdlib only.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import planfile  # noqa: E402
import plan_tools  # noqa: E402

FORMAT = "flowchart"  # in the cache key, so a cache from another renderer is redrawn


def _parse_graph(text: str) -> dict[str, list[str]]:  # O(L)
    """deps[tid] = list of prerequisite ids (edges a --> b mean b waits on a)."""
    if "%% taskgraph" not in text:
        raise ValueError("no '%% taskgraph' block found in plan")
    plan = planfile.Plan(text)
    deps: dict[str, set[str]] = {tid: set() for tid in plan.tasks}
    for a, b in plan.edges:
        deps.setdefault(a, set())
        deps.setdefault(b, set()).add(a)
    out = {t: sorted(ds, key=_nkey) for t, ds in deps.items()}
    cycle = plan_tools.find_cycle(out)
    if cycle:
        raise ValueError("cycle in taskgraph: " + " -> ".join(cycle))
    return out


def _nkey(tid: str):
    m = re.match(r"T(\d+)([A-Za-z]?)$", tid)
    return (int(m.group(1)), m.group(2)) if m else (0, tid)


def _path_key(p: list[str]):  # O(path)
    """Longer path wins; then lexicographically smaller ids (via _nkey)."""
    return (len(p), tuple(-a for a, _ in (_nkey(x) for x in p)),
            tuple(-(ord(b) if b else 0) for _, b in (_nkey(x) for x in p)))


def _longest_path(deps: dict[str, list[str]]) -> list[str]:  # O(n+e)
    """Deterministic critical path: max edge-count path, tie-break by id."""
    memo: dict[str, list[str]] = {}

    def path_of(t: str) -> list[str]:  # O(n+e) total
        if t in memo:
            return memo[t]
        best: list[str] = []
        for d in deps.get(t, ()):
            cand = path_of(d)
            if not best or _path_key(cand) > _path_key(best):
                best = cand
        memo[t] = best + [t]
        return memo[t]

    if not deps:
        return []
    return max((path_of(t) for t in deps), key=_path_key)


_SAFE = str.maketrans({'"': "'", "`": "'", "[": "(", "]": ")", "<": "(", ">": ")", "#": "no. "})


def _label(tid: str, title: str) -> str:  # O(len title)
    """Quoted node label. Characters that break or restyle a label become plain look-alikes, since the chat
    shows mermaid entity codes (#34;) as literal text; other characters, accents included, pass through."""
    return '"' + f"{tid} {title}".strip().translate(_SAFE) + '"'


def _flowchart(deps: dict[str, list[str]], titles: dict[str, str], critical: set[str]) -> str:  # O(n+e)
    order = sorted(deps, key=_nkey)
    lines = ["flowchart TD"]
    lines += [f"  {t}[{_label(t, titles.get(t, ''))}]" for t in order]
    lines += [f"  {a} --> {b}" for b in order for a in deps[b]]
    marked = [t for t in order if t in critical]
    if marked:
        lines += ["  classDef critical stroke:#d33,stroke-width:3px", f"  class {','.join(marked)} critical"]
    return "\n".join(lines) + "\n"


def _cache_path(plan_path: str) -> Path:
    return plan_tools.run_dir(plan_path) / "graph.txt"


def render(plan_path: str, critical: list[str] | None = None) -> str:  # O(L + n+e)
    """Render the flowchart; cache graph.txt in the run folder by plan hash plus
    the critical ids (None: the longest path is marked; an empty list marks nothing)."""
    p = Path(plan_path)
    if not p.is_file():
        raise FileNotFoundError(plan_path)
    raw = p.read_bytes()
    head = f"# hash: {hashlib.sha256(raw).hexdigest()} format: {FORMAT}"
    if critical is not None:
        head += " critical: " + " ".join(critical)
    cache = _cache_path(plan_path)
    if cache.is_file():
        prev = cache.read_text(encoding="utf-8")
        if prev.split("\n", 1)[0] == head:
            return prev
    text = raw.decode("utf-8-sig")
    deps = _parse_graph(text)
    titles = {tid: t.title for tid, t in planfile.Plan(text).tasks.items()}
    body = _flowchart(deps, titles, set(critical) if critical is not None else set(_longest_path(deps)))
    out = f"{head}\n{body}"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(out, encoding="utf-8", newline="\n")
    return out


def main(argv=None) -> int:  # O(L + n+e)
    ap = argparse.ArgumentParser(prog="graph.py")
    ap.add_argument("--plan", required=True)
    a = ap.parse_args(argv)
    try:
        sys.stdout.write(render(a.plan))
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
