#!/usr/bin/env python3
"""ASCII task graph from a plan's %% taskgraph block.

    graph.py --plan PATH
    graph.render(plan_path) -> str

Writes .superlite/graph.txt (cached by plan SHA-256). Lines <=79, ASCII,
critical-path tasks marked with *. Stdlib only.
"""
# Complexity notation:
#   n = tasks, e = edges, L = plan bytes, W = 79
from __future__ import annotations

import argparse  # O(1)
import hashlib  # O(1)
import re  # O(1)
import sys  # O(1)
from pathlib import Path  # O(1)

WIDTH = 79  # O(1)
TASK_RE = re.compile(r"\b(T\d+[A-Za-z]?)\b")  # O(1)
EDGE_RE = re.compile(  # O(1)
    r"(T\d+[A-Za-z]?)\s*-->\s*(T\d+[A-Za-z]?)"
)


def _stable_root(plan_path: str) -> Path:  # O(1)
    root = Path(plan_path).resolve().parent  # O(1)
    if root.name == ".superlite":  # O(1)
        return root.parent  # O(1)
    if root.name == "plans" and root.parent.name == "docs":  # O(1)
        return root.parent.parent  # O(1)
    return root  # O(1)


def _parse_graph(text: str) -> dict[str, list[str]]:  # O(L)
    """deps[tid] = list of prerequisite ids (edges a --> b mean b waits on a)."""
    if "%% taskgraph" not in text:  # O(L)
        raise ValueError("no '%% taskgraph' block found in plan")  # O(1)
    block = text.split("%% taskgraph", 1)[1].split("```", 1)[0]  # O(L)
    deps: dict[str, set[str]] = {}  # O(1)
    for ln in block.splitlines():  # O(lines)
        s = ln.strip()  # O(s)
        if not s or s.startswith("%%"):  # O(1)
            continue  # O(1)
        if s.split()[0].lower() in (  # O(1)
            "flowchart", "graph", "subgraph", "end", "direction",
            "classdef", "style", "class", "click", "linkstyle",
        ):
            continue  # O(1)
        for a, b in EDGE_RE.findall(s):  # O(s)
            deps.setdefault(a, set())  # O(1)
            deps.setdefault(b, set()).add(a)  # O(1)
        for tid in TASK_RE.findall(s):  # O(s)
            deps.setdefault(tid, set())  # O(1)
    return {t: sorted(ds, key=_nkey) for t, ds in deps.items()}  # O(n log n)


def _nkey(tid: str):  # O(1)
    m = re.match(r"T(\d+)([A-Za-z]?)$", tid)  # O(1)
    return (int(m.group(1)), m.group(2)) if m else (0, tid)  # O(1)


def _path_key(p: list[str]):  # O(path)
    """Longer path wins; then lexicographically smaller ids (via _nkey)."""
    return (len(p), tuple(-a for a, _ in (_nkey(x) for x in p)),
            tuple(-(ord(b) if b else 0) for _, b in (_nkey(x) for x in p)))


def _longest_path(deps: dict[str, list[str]]) -> list[str]:  # O(n+e)
    """Deterministic critical path: max edge-count path, tie-break by id."""
    memo: dict[str, list[str]] = {}  # O(1)

    def path_of(t: str) -> list[str]:  # O(n+e) total
        if t in memo:  # O(1)
            return memo[t]  # O(1)
        best: list[str] = []  # O(1)
        for d in deps.get(t, ()):  # O(deg)
            cand = path_of(d)  # O(subtree)
            if not best or _path_key(cand) > _path_key(best):  # O(path)
                best = cand  # O(1)
        memo[t] = best + [t]  # O(path)
        return memo[t]  # O(1)

    if not deps:  # O(1)
        return []  # O(1)
    return max((path_of(t) for t in deps), key=_path_key)  # O(n)


def _tiers(deps: dict[str, list[str]]) -> list[list[str]]:  # O(n+e)
    """Topological levels: each task after the max tier of its deps."""
    tier: dict[str, int] = {}  # O(1)

    def level(t: str) -> int:  # O(n+e) total
        if t in tier:  # O(1)
            return tier[t]  # O(1)
        ds = deps.get(t, ())  # O(1)
        tier[t] = 0 if not ds else 1 + max(level(d) for d in ds)  # O(deg)
        return tier[t]  # O(1)

    for t in deps:  # O(n)
        level(t)  # O(1 amortized)
    if not tier:  # O(1)
        return []  # O(1)
    width = max(tier.values()) + 1  # O(n)
    rows: list[list[str]] = [[] for _ in range(width)]  # O(tiers)
    for t in sorted(tier, key=_nkey):  # O(n log n)
        rows[tier[t]].append(t)  # O(1)
    return rows  # O(1)


def _cell(tid: str, critical: set[str]) -> str:  # O(1)
    star = "*" if tid in critical else ""  # O(1)
    return f"[{tid}{star}]"  # O(1)


def _draw(deps: dict[str, list[str]], critical: set[str]) -> str:  # O(n)
    rows = _tiers(deps)  # O(n+e)
    lines: list[str] = []  # O(1)
    for i, row in enumerate(rows):  # O(tiers)
        cells = [_cell(t, critical) for t in row]  # O(row)
        line = "  ".join(cells)  # O(row)
        if len(line) > WIDTH:  # O(1)
            line = line[: WIDTH - 3] + "..."  # O(W)
        lines.append(line)  # O(1)
        if i + 1 < len(rows):  # O(1)
            lines.append("  v")  # O(1)
    lines.append("* = critical path")  # O(1)
    return "\n".join(lines) + "\n"  # O(n)


def _cache_path(plan_path: str) -> Path:  # O(1)
    return _stable_root(plan_path) / ".superlite" / "graph.txt"  # O(1)


def render(plan_path: str) -> str:  # O(L + n+e)
    """Render ASCII graph; cache under .superlite/graph.txt by plan hash."""
    p = Path(plan_path)  # O(1)
    if not p.is_file():  # O(1)
        raise FileNotFoundError(plan_path)  # O(1)
    raw = p.read_bytes()  # O(L)
    digest = hashlib.sha256(raw).hexdigest()  # O(L)
    cache = _cache_path(plan_path)  # O(1)
    if cache.is_file():  # O(1)
        prev = cache.read_text(encoding="utf-8")  # O(file)
        first = prev.splitlines()[0] if prev else ""  # O(1)
        if first.startswith("# hash:") and digest in first:  # O(1)
            return prev  # O(1)
    text = raw.decode("utf-8-sig")  # O(L)
    deps = _parse_graph(text)  # O(L)
    critical = set(_longest_path(deps))  # O(n+e)
    body = _draw(deps, critical)  # O(n)
    out = f"# hash: {digest}\n{body}"  # O(n)
    if not out.isascii():  # O(n)
        out = out.encode("ascii", "replace").decode("ascii")  # O(n)
    for ln in out.splitlines():  # O(n)
        if len(ln) > WIDTH:  # O(1)
            raise AssertionError(f"line wider than {WIDTH}: {ln!r}")  # O(1)
    cache.parent.mkdir(parents=True, exist_ok=True)  # O(1)
    cache.write_text(out, encoding="utf-8", newline="\n")  # O(n)
    return out  # O(1)


def main(argv=None) -> int:  # O(L + n+e)
    ap = argparse.ArgumentParser(prog="graph.py")  # O(1)
    ap.add_argument("--plan", required=True)  # O(1)
    a = ap.parse_args(argv)  # O(1)
    try:  # O(1)
        sys.stdout.write(render(a.plan))  # O(n)
    except FileNotFoundError as e:  # O(1)
        print(f"error: {e}", file=sys.stderr)  # O(1)
        return 2  # O(1)
    except ValueError as e:  # O(1)
        print(f"error: {e}", file=sys.stderr)  # O(1)
        return 2  # O(1)
    return 0  # O(1)


if __name__ == "__main__":  # O(1)
    sys.exit(main())  # O(1)
