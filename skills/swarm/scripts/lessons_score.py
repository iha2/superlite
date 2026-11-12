"""Scoring math for lessons: module key, decay weight, baseline, usefulness U, rank, brief line, selection.

    lessons_score.module_key(path)                    directory of a file, "." at the root
    lessons_score.baseline(modules, module, before, run_index)   first-time-pass rate before a run
    lessons_score.usefulness(deliveries, base, run_index)        decayed hit rate minus baseline
    lessons_score.rank(lesson) / line(lesson) / select(lessons, budget)

Pure functions, no I/O. Stdlib only.
"""
from __future__ import annotations

HALF_LIFE = 10
GRACE = 3
GRACE_RANK = 0.1
LINE_CUT = 160


def module_key(path: str) -> str:  # O(len(path))
    head, sep, _ = str(path).replace("\\", "/").rpartition("/")
    return head if sep and head else "."


def weight(age: int) -> float:  # O(1)
    return 0.5 ** (max(age, 0) / HALF_LIFE)


def baseline(modules: dict, module: str, before: str | None, run_index: list) -> float:  # O(runs)
    if before is not None and before in run_index:
        runs = run_index[:run_index.index(before)]
    else:
        runs = run_index
    seen = modules.get(module, {})
    tasks = ftp = 0
    for r in runs:
        rec = seen.get(r)
        if rec:
            tasks += rec.get("tasks", 0)
            ftp += rec.get("ftp", 0)
    return ftp / tasks if tasks > 0 else 0.5


def usefulness(deliveries: list, base: float, run_index: list) -> float:  # O(deliveries)
    if not deliveries:
        return 0.0
    base = min(1.0, max(0.0, base))
    pos = {r: i for i, r in enumerate(run_index)}
    last = len(run_index) - 1
    sw = sh = 0.0
    for d in deliveries:
        w = weight(last - pos[d["run"]]) if d.get("run") in pos else 1.0
        sw += w
        sh += w if d.get("hit") else 0.0
    return (sh + base) / (sw + 1.0) - base


def rank(lesson: dict) -> float:  # O(1)
    since = lesson.get("deliveries", [])[lesson.get("grace_from", 0):]
    return GRACE_RANK if len(since) < GRACE else lesson.get("u", 0.0)


def line(lesson: dict) -> str:  # O(len(text))
    tags = [lesson.get("kind", "")]
    if lesson.get("proven"):
        tags.append("proven")
    n = len(lesson.get("runs", []))
    tags.append(f"{n} run" if n == 1 else f"{n} runs")
    head, tail = f"- {lesson.get('module', '.')}: ", f" ({', '.join(tags)})"
    text = str(lesson.get("text", ""))
    room = max(LINE_CUT - len(head) - len(tail), 0)
    if len(text) > room:
        text = text[:max(room - 1, 0)] + "…" if room else ""
    return head + text + tail


def _order(les: dict) -> tuple:  # O(1)
    return (not les.get("proven"), -rank(les), str(les.get("id", "")), les.get("module", ""), str(les.get("text", "")))


def select(lessons: list, budget: int) -> list:  # O(n^2)
    ordered = sorted(lessons, key=_order)
    for p in [x for x in ordered if x.get("kind") == "practice"]:
        i = ordered.index(p)
        first = next((j for j, x in enumerate(ordered)
                      if x.get("kind") == "pitfall" and x.get("module") == p.get("module")), None)
        if first is not None and first < i:
            ordered.insert(first, ordered.pop(i))
    out, used = [], 0
    for les in ordered:
        n = len(line(les)) + 1
        if used + n > budget:
            break
        out.append(les)
        used += n
    return out
