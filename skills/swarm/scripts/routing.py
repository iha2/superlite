#!/usr/bin/env python3
"""Deterministic tier routing (D36): a task's tier comes from its plan features and config/routing.json.

    routing.py --plan P [--config config/routing.json] [--json]

Prints `Tn tier` per task (approved tasks only). The same plan and config always give the same tiers.
Library: load_cfg, compute_tiers (used by `plan_tools lint --stable`), read_tiers (stable.json, else
computed), tier_agent, tier_limits, agent_tier (a pool agent's tier). Stdlib only.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402

CONFIG = Path(__file__).resolve().parent.parent / "config" / "routing.json"
FALLBACK = {"default_tier": "sonnet",  # built-in copy for a missing file
            "tiers": {"sonnet": {"agent": "superlite:implementer", "prime": "slim", "max_tasks": 20, "max_context_k": 80,
                                 "max_idle_min": 50}},
            "rules": [{"tier": "sonnet", "when": {}}], "lane": {}}


def load_cfg(path=None) -> dict:  # O(file)
    return base.read_json(Path(path) if path else CONFIG, None) or dict(FALLBACK)


def task_features(task, focus, cp, times) -> dict:  # O(k*s + pkgs)
    import plan_tools as pt  # lazy
    f = task.explicit_features
    parts = {seg.lower() for o in task.owns for seg in re.split(r"[/.]", o)}
    cats = {c for pkg, cases in focus.items() if pkg in parts for c in cases}
    kind = f.get("kind", "design")
    import history
    return {"task": task.id, "band": history.band_of(kind, f.get("loc"), f.get("tests")), "kind": kind, "loc": f.get("loc"), "components": f.get("components"), "tests": f.get("tests"),
            "focus": cats, "time": times.get(task.id), "critical_path": task.id in cp,
            "mechanical": kind == "mechanical" and not pt.not_mechanical_reasons(task, focus)}


def _match_one(k: str, v, ft: dict) -> bool:
    if k in ("kind", "tests", "mechanical", "critical_path"):
        return ft.get(k) == v
    if k in ("loc_lte", "components_lte", "time_lte"):
        have = ft.get(k[:-4])
        return have is not None and have <= v
    if k == "focus_any":
        return bool(ft["focus"] & set(v))
    if k == "focus_none":
        return not ft["focus"] & set(v)
    return False  # an unknown key never matches: the safe side


def matches(when: dict, ft: dict) -> bool:  # O(keys)
    return all(_match_one(k, v, ft) for k, v in when.items())


NEVER_ON_CRITICAL_PATH = frozenset({"haiku"})  # fixed rule (user ruling), not configurable


def pick_tier(ft: dict, cfg: dict, stats=None, gated=None) -> str:  # O(r)
    default = cfg.get("default_tier", "sonnet")
    tier = _first_rule(ft, cfg)
    if ft.get("critical_path") and tier in NEVER_ON_CRITICAL_PATH:
        return default
    gate = cfg.get("stats_gate") or {}  # measured history: a band with enough samples and a low pass rate stays on the default tier
    if stats and gate and tier != default:
        s = (stats.get("bands_by_tier") or {}).get(f"{tier}|{ft.get('band')}") or {}
        if s.get("n", 0) >= gate.get("min_n", 8) and s.get("ftp_rate") is not None and s["ftp_rate"] < gate.get("min_first_time_pass", 0.8):
            if gated is not None:
                gated.append(ft.get("task"))
            return default
    return tier


def _first_rule(ft: dict, cfg: dict) -> str:  # O(r)
    for rule in cfg.get("rules", []):
        if rule.get("if_config") and not cfg.get(rule["if_config"]):
            continue
        if matches(rule.get("when", {}), ft):
            return rule["tier"]
    return cfg.get("default_tier", "sonnet")


def compute_tiers(plan, cp, times, cfg=None, stats=None, gated=None) -> dict:  # O(n*(k*s + r))
    """{task id: tier} for every approved task. cp = critical-path ids, times = predicted minutes, stats = history
    stats (bands_by_tier), gated = list that receives the tasks kept off a tier by the stats gate."""
    import plan_tools as pt
    cfg = cfg or load_cfg()
    focus = pt.review_focus_cases(plan)
    cp = set(cp)
    return {t.id: pick_tier(task_features(t, focus, cp, times), cfg, stats, gated)
            for t in plan.tasks if t.status != "draft"}


def tiers_for_plan(plan_path: str, cfg=None) -> dict:  # O(schedule + n*(k*s + r))
    import plan_tools as pt
    plan = pt.Plan.load(plan_path)
    args = pt.build_parser().parse_args(["schedule", "--plan", plan_path])
    rep = pt.schedule_report(plan, args)
    import history
    return compute_tiers(plan, rep["critical_path"], rep["times"], cfg, history.project_stats(pt.run_dir(plan_path)))


KIND_MISS = re.compile(r"kind-miss\s+(T\d+[A-Za-z]?)")


def missed_tasks(plan_path: str) -> set:  # O(incident log)
    """Tasks with a `kind-miss Tn` incident: they never go to the haiku tier again."""
    import plan_tools as pt
    f = pt.run_dir(plan_path) / "incidents.jsonl"
    if not f.is_file():
        return set()
    hits = (KIND_MISS.search(ln) for ln in f.read_text(encoding="utf-8", errors="replace").splitlines())
    return {m.group(1) for m in hits if m}


def read_tiers(plan_path: str, cfg=None) -> dict:  # stable.json or a computation, minus kind-miss tasks
    tiers = dict(_read_tiers(plan_path, cfg))
    default = (cfg or load_cfg()).get("default_tier", "sonnet")
    for t in missed_tasks(plan_path):
        if t in tiers:
            tiers[t] = default  # first-time pass is the gate: no second go on Haiku
    return tiers


def _read_tiers(plan_path: str, cfg=None) -> dict:  # O(stable.json) or a computation
    import plan_tools as pt
    saved = base.read_json(pt.run_dir(plan_path) / "stable.json", {})
    tiers = saved.get("tiers") if isinstance(saved, dict) else None
    return tiers if tiers else tiers_for_plan(plan_path, cfg)


def tier_agent(cfg: dict, tier: str) -> str:
    return (cfg.get("tiers", {}).get(tier) or cfg["tiers"][cfg.get("default_tier", "sonnet")])["agent"]


def tier_limits(cfg: dict, tier: str) -> dict:
    """The limits of a tier, all from its config entry; a tier without them is a config error."""
    t = (cfg.get("tiers") or {}).get(tier)
    keys = ("max_tasks", "max_context_k", "max_idle_min")
    if not t or any(k not in t for k in keys):
        raise ValueError(f"routing.json: tier {tier!r} must set {', '.join(keys)}")
    return {k: t[k] for k in keys}


def agent_tier(agent: dict, cfg: dict) -> str:  # O(tiers)
    """A pool agent's tier: its `tier`, else the tier whose agent type it names, else the default tier."""
    if agent.get("tier"):
        return agent["tier"]
    kind = str(agent.get("agent") or agent.get("type") or agent.get("model") or "")
    for name, t in cfg.get("tiers", {}).items():
        if kind and (kind == t.get("agent") or kind == name):
            return name
    return cfg.get("default_tier", "sonnet")


def miss(argv) -> int:
    """`routing.py miss --task Tn --plan P [--reason R]`: log the kind-miss incident, print the fix routing."""
    ap = argparse.ArgumentParser(prog="routing.py miss")
    ap.add_argument("--task", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--reason", default="NEEDS_CONTEXT, BLOCKED or red")
    a = ap.parse_args(argv)
    import next_tasks as nt
    import plan_tools as pt
    log = pt.run_dir(a.plan) / "incidents.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    nt.add_incidents(str(log), [("-", 0, f"kind-miss {a.task}: {a.reason}", "assumption", "coordinator", "")])
    d = load_cfg().get("default_tier", "sonnet")
    print(f"{a.task} kind-miss -> fixer-sonnet note={log.parent.name}/fixes/F-{a.task}-1.md tier={d} (never retried on haiku)")
    return 0


def main(argv=None):  # O(schedule + n*r)
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "miss":
        return miss(argv[1:])
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--plan", required=True)
    ap.add_argument("--config")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    tiers = tiers_for_plan(a.plan, load_cfg(a.config))
    if a.json:
        print(json.dumps(tiers, indent=1))
    else:
        for t, tier in tiers.items():
            print(f"{t} {tier}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
