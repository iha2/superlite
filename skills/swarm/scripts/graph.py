#!/usr/bin/env python3
"""ASCII task graph from a plan's %% taskgraph block.

    graph.py --plan PATH
    graph.render(plan_path, critical=None) -> str

Writes graph.txt in the run folder (cached by plan SHA-256 and the critical ids).
Lines <=79, ASCII, critical-path tasks marked with *: the given ids, else the
longest path by task count. Stdlib only.
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

WIDTH = 79


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


def _tiers(deps: dict[str, list[str]]) -> list[list[str]]:  # O(n+e)
    """Topological levels: each task after the max tier of its deps."""
    tier: dict[str, int] = {}

    def level(t: str) -> int:  # O(n+e) total
        if t in tier:
            return tier[t]
        ds = deps.get(t, ())
        tier[t] = 0 if not ds else 1 + max(level(d) for d in ds)
        return tier[t]

    for t in deps:
        level(t)
    if not tier:
        return []
    width = max(tier.values()) + 1
    rows: list[list[str]] = [[] for _ in range(width)]
    for t in sorted(tier, key=_nkey):
        rows[tier[t]].append(t)
    return rows


def _cell(tid: str, critical: set[str]) -> str:
    star = "*" if tid in critical else ""
    return f"[{tid}{star}]"


def _draw(deps: dict[str, list[str]], critical: set[str]) -> str:  # O(n)
    rows = _tiers(deps)
    lines: list[str] = []
    for i, row in enumerate(rows):
        cells = [_cell(t, critical) for t in row]
        line = "  ".join(cells)
        if len(line) > WIDTH:
            line = line[: WIDTH - 3] + "..."
        lines.append(line)
        if i + 1 < len(rows):
            lines.append("  v")
    lines.append("* = critical path")
    return "\n".join(lines) + "\n"


def _cache_path(plan_path: str) -> Path:
    return plan_tools.run_dir(plan_path) / "graph.txt"


def render(plan_path: str, critical: list[str] | None = None) -> str:  # O(L + n+e)
    """Render ASCII graph; cache graph.txt in the run folder by plan hash plus
    the critical ids (none given: the longest path is starred)."""
    p = Path(plan_path)
    if not p.is_file():
        raise FileNotFoundError(plan_path)
    raw = p.read_bytes()
    head = f"# hash: {hashlib.sha256(raw).hexdigest()}"
    if critical:
        head += " critical: " + " ".join(critical)
    cache = _cache_path(plan_path)
    if cache.is_file():
        prev = cache.read_text(encoding="utf-8")
        if prev.split("\n", 1)[0] == head:
            return prev
    deps = _parse_graph(raw.decode("utf-8-sig"))
    stars = set(critical) if critical else set(_longest_path(deps))
    body = _draw(deps, stars)
    if not body.isascii():
        body = body.encode("ascii", "replace").decode("ascii")
    for ln in body.splitlines():  # the head line may be wider: it is stripped before showing
        if len(ln) > WIDTH:
            raise AssertionError(f"line wider than {WIDTH}: {ln!r}")
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
