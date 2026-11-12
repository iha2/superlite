#!/usr/bin/env python3
"""Per-repo lessons: what a task brief carries and what the user can steer.

    lessons.py list                                   cached and candidate lessons, best first
    lessons.py show ID                                one lesson as JSON
    lessons.py drop|pin|restore ID                    steer one lesson
    lessons.py report [--last N]                      failures, trend, what works here, cached lessons
    lessons.py brief [--task Tn] [--plan P] [--agent implementer|fixer|reviewer|drafter]
                     [--file F] [--files P...]        lesson lines for a task message (empty when none apply)

Lessons live in the hub run dir (`lessons.json`); a brief appends one line to this checkout's `deliveries.jsonl`:
{"v": 1, "run": R, "task": T or null, "agent": A, "lessons": [ids]}. `--task` is optional with `--file` or
`--files` (a drafter has no task ids yet; a batched reviewer passes its files). Add `--hub DIR` or `--sl DIR` to override the folders.
Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import history  # noqa: E402
import lessons_score  # noqa: E402
import lessons_store  # noqa: E402
import plan_tools  # noqa: E402
import run_archive  # noqa: E402
import state  # noqa: E402

BUDGETS = {"implementer": 1000, "fixer": 1000, "reviewer": 2000, "drafter": 2000}
HEADER = "In this repo (learned from past runs):\n"
AGENTS = tuple(BUDGETS)


def brief(hub: Path, sl: Path, run: str, task: str | None, owns: list, agent: str = "implementer") -> str:  # O(n log n) in lessons
    mods = {lessons_score.module_key(p) for p in owns}
    cached = [x for x in lessons_store.load(hub)["lessons"].values()
              if x["status"] == "cached" and x["module"] in mods]
    chosen = lessons_score.select(cached, BUDGETS.get(agent, BUDGETS["implementer"]))
    if not chosen:
        return ""
    sl = Path(sl)
    sl.mkdir(parents=True, exist_ok=True)
    row = {"v": 1, "run": run, "task": task, "agent": agent, "lessons": [x["id"] for x in chosen]}
    with open(sl / "deliveries.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return HEADER + "".join(lessons_score.line(x) + "\n" for x in chosen)


def _runs(rows: list, last_n: int) -> list:  # O(h)
    order: list = []
    for r in rows:
        if r.get("run") not in order:
            order.append(r.get("run"))
    return order[-last_n:] if last_n > 0 else []


def _pct(a: float, b: float) -> str:  # O(1)
    return f"{int(100 * a / b)}%" if b else "n/a"


def _lesson_row(les: dict) -> str:  # O(d) in deliveries
    d = les["deliveries"]
    hits = sum(1 for x in d if x.get("hit"))
    base = les["baseline"] if les.get("baseline") is not None else 0.5
    return (f"- {les['id']} {les['module']} {les['kind']}: U {les['u']:.2f}, deliveries {len(d)}, "
            f"hits {hits}, baseline {base:.2f}")


def _trend_row(run: str, rows: list) -> str:  # O(h)
    ftp = sum(1 for r in rows if r.get("first_time_pass"))
    cost = sum(r.get("cost_usd") or 0 for r in rows)
    rework = sum(r.get("rework_cost") or 0 for r in rows)
    return (f"- {run}: first-time pass {_pct(ftp, len(rows))}, "
            f"findings {sum(r.get('findings') or 0 for r in rows)}, "
            f"rework+fixer cost {_pct(rework, cost)}")


def report(hub: Path, rows: list, last_n: int = 10) -> str:  # O(n + h)
    rows = history.unique_rows(rows)
    store = lessons_store.load(hub)
    cached = sorted((x for x in store["lessons"].values() if x["status"] == "cached"),
                    key=lambda x: (-x["u"], x["id"]))
    out = ["failures by category:"]
    cats: dict = {}
    for x in store["lessons"].values():
        if x["kind"] == "pitfall" and x["status"] != "dropped":
            cats[x["module"]] = cats.get(x["module"], 0) + 1
    out += [f"- {m}: {n} pitfall(s)" for m, n in sorted(cats.items())] or ["- none yet"]
    out.append(f"trend (last {last_n} runs):")
    runs = _runs(rows, last_n)
    out += [_trend_row(run, [r for r in rows if r.get("run") == run]) for run in runs]
    if not runs:
        out.append("- no runs yet")
    out.append("what works here:")
    out += [_lesson_row(x) for x in cached if x["kind"] != "pitfall"] or ["- nothing proven yet"]
    out.append("cached lessons:")
    out += [_lesson_row(x) for x in cached] or ["- none"]
    return "\n".join(out) + "\n"


def _parser() -> argparse.ArgumentParser:  # O(1)
    ap = argparse.ArgumentParser(prog="lessons", description="Per-repo lessons: list, steer, brief, report.")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--hub", help="hub run dir holding lessons.json (default: the main checkout's)")
    common.add_argument("--sl", help="this checkout's run dir (default: the current checkout's)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", parents=[common], help="all lessons, best first")
    for name in ("show", "drop", "pin", "restore"):
        sub.add_parser(name, parents=[common], help=f"{name} one lesson").add_argument("id")
    p = sub.add_parser("brief", parents=[common], help="lesson lines for a task message")
    p.add_argument("--task", help="task id (optional with --file or --files)")
    p.add_argument("--plan")
    p.add_argument("--run", help="run id (default: the current run)")
    p.add_argument("--agent", default="implementer", choices=AGENTS)
    p.add_argument("--file", help="the one file a fixer works on")
    p.add_argument("--files", nargs="*", default=[], help="the files a drafter reads")
    p = sub.add_parser("report", parents=[common], help="failures, trend, what works here")
    p.add_argument("--last", type=int, default=10)
    p.add_argument("--history", help="history file (default: the user-level history)")
    return ap


def _owns(a) -> list:  # O(files)
    if a.file or a.files:
        return ([a.file] if a.file else []) + list(a.files)
    if a.plan and a.task:
        task = plan_tools.Plan.load(a.plan).by_id.get(a.task)
        return list(task.owns) if task else []
    return []


def _run_brief(a, hub: Path, sl: Path) -> int:  # O(n log n)
    run = a.run or (run_archive.read_current_run(sl) or {}).get("id") or "unknown"
    sys.stdout.write(brief(hub, sl, run, a.task, _owns(a), a.agent))
    return 0


def _run_steer(a, hub: Path) -> int:  # O(n)
    found: list = []
    lessons_store.update(hub, lambda s: found.append(lessons_store.control(s, a.id, a.cmd)))
    if not found[0]:
        print(f"no lesson {a.id}", file=sys.stderr)
        return 1
    return 0


def main(argv: list | None = None) -> int:  # O(n)
    a = _parser().parse_args(argv)
    cwd = Path.cwd()
    hub = Path(a.hub) if a.hub else lessons_store.hub_dir(cwd)
    sl = Path(a.sl) if a.sl else state.run_dir(cwd)
    store = lessons_store.load(hub)
    if a.cmd == "list":
        for x in sorted(store["lessons"].values(), key=lambda x: (-x["u"], x["id"])):
            print(f"{x['id']} {x['status']} U {x['u']:.2f} {lessons_score.line(x)}")
    elif a.cmd == "show":
        les = store["lessons"].get(a.id)
        if les is None:
            print(f"no lesson {a.id}", file=sys.stderr)
            return 1
        print(json.dumps(les, indent=2, sort_keys=True))
    elif a.cmd in ("drop", "pin", "restore"):
        return _run_steer(a, hub)
    elif a.cmd == "brief":
        return _run_brief(a, hub, sl)
    else:
        repo = history.repository_id(sl)
        rows = [r for r in history.read_rows(a.history) if r.get("repository") == repo]
        sys.stdout.write(report(hub, rows, a.last))
    return 0


if __name__ == "__main__":
    sys.exit(main())
