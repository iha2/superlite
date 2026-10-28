"""Bridge between the bench score JSONs and superlite's run history (skills/swarm/scripts/history.py).

    python bench/history_import.py import bench/results/*.json [--history FILE]   score JSONs -> history rows (append-only, no duplicates)
    python bench/report_mechanical.py --history FILE                              the report, read from history instead of score files

The rows follow history.py's documented v1 schema; its own `import_file` converts the task records, and this module only
fills what that importer leaves empty (agent_type) and skips (run, task) pairs the history already has. History rows
hold aggregates only, so a report built from them has no per-run timeline: no critical-path counterfactual, no lane
reuse, and cache writes are treated as 5-minute writes when re-pricing."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "skills" / "swarm" / "scripts"))
import history  # noqa: E402
import taskmetrics as tm  # noqa: E402


def rows_from_scores(paths: list[str | Path]) -> list[dict]:
    """History rows for score JSONs, via history.import_file; the agent type of each task comes from the record."""
    out = []
    for p in paths:
        rows = history.import_file(p)
        try:
            tasks = {t["task"]: t for t in json.loads(Path(p).read_text(encoding="utf-8")).get("tasks") or []}
        except (OSError, ValueError, AttributeError):
            tasks = {}
        for r in rows:
            rec = tasks.get(r["task"]) or {}
            r["agent_type"] = rec.get("agent_type") or r.get("agent_type")
            if rec.get("tier_planned"):
                r["tier"] = rec["tier_planned"]
        out += rows
    return out


def import_scores(paths: list[str | Path], hist: str | Path | None = None) -> int:
    """Append the rows not already in the history (same run and task); return how many were added."""
    seen = {(r.get("run"), r.get("task")) for r in history.read_rows(hist)}
    new = [r for r in rows_from_scores(paths) if (r["run"], r["task"]) not in seen]
    if new:
        history.append_rows(new, hist)
    return len(new)


def _task(r: dict) -> dict:
    tk = r.get("tokens") or {}
    slack = r.get("slack_min")
    t = {"task": r["task"], "kind": r.get("kind"), "loc": r.get("loc"), "components": r.get("components"), "tests": r.get("tests"),
         "model": r.get("model"), "agent_type": r.get("agent_type"), "tier_planned": r.get("tier"), "tier_source": "history",
         "duration_s": r.get("duration_s"), "cost_usd": r.get("cost_usd") or 0.0, "tool_calls": r.get("tool_calls"),
         "first_time_pass": r.get("first_time_pass"), "rework_s": r.get("rework_s") or 0, "rework_cost": r.get("rework_cost") or 0,
         "on_critical_path": r.get("critical_path"), "slack_s": slack * 60 if isinstance(slack, (int, float)) else None,
         "tokens": {"in": tk.get("in") or 0, "out": tk.get("out") or 0, "cache_read": tk.get("cache_read") or 0,
                    "cache_write": tk.get("cache_write") or 0, "cache_write_5m": tk.get("cache_write") or 0, "cache_write_1h": 0},
         "complete": True, "status": None, "status_reason": None, "dispatches": None}
    t["group"] = tm.group_of(t)
    t["pool_violation"] = bool(t["model"] and t["tier_planned"] and t["model"] != t["tier_planned"])
    return t


def runs_from_history(path: str | Path | None = None, baseline: str = "sonnet") -> list[dict]:
    """The report's `runs` structure (run, tasks, mechanical block) rebuilt from history rows, one run per run id."""
    import harness
    by: dict[str, list[dict]] = {}
    for r in history.read_rows(path):
        if r.get("task"):
            by.setdefault(str(r.get("run")), []).append(_task(r))
    prices, speeds = harness.load_prices(), harness.load_speeds()
    runs = []
    for run, tasks in sorted(by.items()):
        runs.append({"run": run, "tasks": tasks, "mechanical": tm.mechanical_block(tasks, {}, prices, speeds, None, None, baseline)})
    return runs


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["import"])
    ap.add_argument("files", nargs="+")
    ap.add_argument("--history", help="history file (default: history.jsonl in the superlite home, see history.home())")
    a = ap.parse_args(argv)
    n = import_scores(a.files, a.history)
    print(f"{n} rows added to {a.history or history.home() / 'history.jsonl'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
