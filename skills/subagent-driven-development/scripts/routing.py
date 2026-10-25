#!/usr/bin/env python3
"""Deterministic tier routing (D36): a task's tier comes from its plan features and config/routing.json.

    routing.py --plan P [--config config/routing.json] [--json]

Prints `Tn tier` per task (approved tasks only). The same plan and config always give the same tiers.
Library: load_cfg, compute_tiers (used by `plan_tools lint --stable`), read_tiers (stable.json, else
computed), tier_agent, tier_limits, agent_tier (a pool agent's tier). Stdlib only.
"""
# Complexity notation:
#   n = tasks, r = rules in the table, L = plan text size
import argparse  # O(1)
import json  # O(1)
import re  # O(1)
import sys  # O(1)
from pathlib import Path  # O(1)

sys.path.insert(0, str(Path(__file__).resolve().parent))  # O(1)

CONFIG = Path(__file__).resolve().parent.parent / "config" / "routing.json"  # O(1)
FALLBACK = {"default_tier": "sonnet",  # built-in copy for a missing file | O(1)
            "tiers": {"sonnet": {"agent": "superlite:implementer", "prime": "slim", "max_tasks": 20, "max_context_k": 80,
                                 "max_idle_min": 50}},
            "rules": [{"tier": "sonnet", "when": {}}], "lane": {}}


def load_cfg(path=None) -> dict:  # O(file)
    p = Path(path) if path else CONFIG  # O(1)
    try:  # O(1)
        return json.loads(p.read_text(encoding="utf-8"))  # O(file)
    except (OSError, ValueError):  # O(1)
        return dict(FALLBACK)  # O(1)


def task_features(task, focus, cp, times) -> dict:  # O(k*s + pkgs)
    import plan_tools as pt  # lazy | O(1)
    f = task.explicit_features  # O(k*s)
    parts = {seg.lower() for o in task.owns for seg in re.split(r"[/.]", o)}  # O(o)
    cats = {c for pkg, cases in focus.items() if pkg in parts for c in cases}  # O(pkgs)
    kind = f.get("kind", "design")  # O(1)
    return {"task": task.id, "band": __import__("history").band_of(kind, f.get("loc"), f.get("tests")), "kind": kind, "loc": f.get("loc"), "components": f.get("components"), "tests": f.get("tests"),  # O(1)
            "focus": cats, "time": times.get(task.id), "critical_path": task.id in cp,
            "mechanical": kind == "mechanical" and not pt.not_mechanical_reasons(task, focus)}


def matches(when: dict, ft: dict) -> bool:  # O(keys)
    for k, v in when.items():  # O(keys)
        if k in ("kind", "tests", "mechanical", "critical_path"):  # O(1)
            if ft.get(k) != v:  # O(1)
                return False  # O(1)
        elif k in ("loc_lte", "components_lte", "time_lte"):  # O(1)
            have = ft.get(k[:-4])  # O(1)
            if have is None or have > v:  # O(1)
                return False  # O(1)
        elif k == "focus_any":  # O(1)
            if not (ft["focus"] & set(v)):  # O(1)
                return False  # O(1)
        elif k == "focus_none":  # O(1)
            if ft["focus"] & set(v):  # O(1)
                return False  # O(1)
        else:  # an unknown key never matches: the safe side | O(1)
            return False  # O(1)
    return True  # O(1)


NEVER_ON_CRITICAL_PATH = frozenset({"haiku"})  # fixed rule (user ruling), not configurable | O(1)


def pick_tier(ft: dict, cfg: dict, stats=None, gated=None) -> str:  # O(r)
    default = cfg.get("default_tier", "sonnet")  # O(1)
    tier = _first_rule(ft, cfg)  # O(r)
    if ft.get("critical_path") and tier in NEVER_ON_CRITICAL_PATH:  # O(1)
        return default  # O(1)
    gate = cfg.get("stats_gate") or {}  # measured history: a band with enough samples and a low pass rate stays on the default tier | O(1)
    if stats and gate and tier != default:  # O(1)
        s = (stats.get("bands_by_tier") or {}).get(f"{tier}|{ft.get('band')}") or {}  # O(1)
        if s.get("n", 0) >= gate.get("min_n", 8) and s.get("ftp_rate") is not None and s["ftp_rate"] < gate.get("min_first_time_pass", 0.8):  # O(1)
            if gated is not None:  # O(1)
                gated.append(ft.get("task"))  # O(1)
            return default  # O(1)
    return tier  # O(1)


def _first_rule(ft: dict, cfg: dict) -> str:  # O(r)
    for rule in cfg.get("rules", []):  # O(r)
        if rule.get("if_config") and not cfg.get(rule["if_config"]):  # O(1)
            continue  # O(1)
        ev = rule.get("evidence") or {}  # O(1)
        floor = rule.get("min_first_time_pass")  # a band measured below its minimum stays on the default tier | O(1)
        if floor is not None and ev.get("rate") is not None and float(ev["rate"]) < float(floor):  # O(1)
            continue  # O(1)
        if matches(rule.get("when", {}), ft):  # O(keys)
            return rule["tier"]  # O(1)
    return cfg.get("default_tier", "sonnet")  # O(1)


def compute_tiers(plan, cp, times, cfg=None, stats=None, gated=None) -> dict:  # O(n*(k*s + r))
    """{task id: tier} for every approved task. cp = critical-path ids, times = predicted minutes, stats = history
    stats (bands_by_tier), gated = list that receives the tasks kept off a tier by the stats gate."""
    import plan_tools as pt  # O(1)
    cfg = cfg or load_cfg()  # O(file)
    focus = pt.review_focus_cases(plan)  # O(L)
    cp = set(cp)  # O(n)
    return {t.id: pick_tier(task_features(t, focus, cp, times), cfg, stats, gated)  # O(n)
            for t in plan.tasks if t.status != "draft"}


def tiers_for_plan(plan_path: str, cfg=None) -> dict:  # O(schedule + n*(k*s + r))
    import plan_tools as pt  # O(1)
    plan = pt.Plan.load(plan_path)  # O(L)
    args = pt.build_parser().parse_args(["schedule", "--plan", plan_path])  # O(1)
    rep = pt.schedule_report(plan, args)  # O(n*(n+e))
    import history  # O(1)
    return compute_tiers(plan, rep["critical_path"], rep["times"], cfg, history.project_stats(pt.stable_root(plan_path) / ".superlite"))  # O(n*(k*s + r))


KIND_MISS = re.compile(r"kind-miss\s+(T\d+[A-Za-z]?)")  # O(1)


def missed_tasks(plan_path: str) -> set:  # O(incident log)
    """Tasks with a `kind-miss Tn` incident: they never go to the haiku tier again."""
    import plan_tools as pt  # O(1)
    f = pt.stable_root(plan_path) / ".superlite" / "incidents.jsonl"  # O(1)
    out = set()  # O(1)
    if f.is_file():  # O(1)
        for ln in f.read_text(encoding="utf-8", errors="replace").splitlines():  # O(I)
            m = KIND_MISS.search(ln)  # O(w)
            if m:  # O(1)
                out.add(m.group(1))  # O(1)
    return out  # O(1)


def read_tiers(plan_path: str, cfg=None) -> dict:  # stable.json or a computation, minus kind-miss tasks
    tiers = dict(_read_tiers(plan_path, cfg))  # O(n)
    default = (cfg or load_cfg()).get("default_tier", "sonnet")  # O(file)
    for t in missed_tasks(plan_path):  # O(misses)
        if t in tiers:  # O(1)
            tiers[t] = default  # first-time pass is the gate: no second go on Haiku | O(1)
    return tiers  # O(n)


def _read_tiers(plan_path: str, cfg=None) -> dict:  # O(stable.json) or a computation
    import plan_tools as pt  # O(1)
    f = pt.stable_root(plan_path) / ".superlite" / "stable.json"  # O(1)
    try:  # O(1)
        t = json.loads(f.read_text(encoding="utf-8")).get("tiers")  # O(file)
    except (OSError, ValueError):  # O(1)
        t = None  # O(1)
    return t if t else tiers_for_plan(plan_path, cfg)  # O(1) or computation


def tier_agent(cfg: dict, tier: str) -> str:  # O(1)
    return (cfg.get("tiers", {}).get(tier) or cfg["tiers"][cfg.get("default_tier", "sonnet")])["agent"]  # O(1)


def tier_limits(cfg: dict, tier: str) -> dict:  # O(1)
    t = cfg.get("tiers", {}).get(tier) or {}  # O(1)
    return {"max_tasks": t.get("max_tasks", 3), "max_context_k": t.get("max_context_k", 80),  # O(1)
            "max_idle_min": t.get("max_idle_min", 50)}


def agent_tier(agent: dict, cfg: dict) -> str:  # O(tiers)
    """A pool agent's tier: its `tier`, else the tier whose agent type it names, else the default tier."""
    if agent.get("tier"):  # O(1)
        return agent["tier"]  # O(1)
    kind = str(agent.get("agent") or agent.get("type") or agent.get("model") or "")  # O(1)
    for name, t in cfg.get("tiers", {}).items():  # O(tiers)
        if kind and (kind == t.get("agent") or kind == name):  # O(1)
            return name  # O(1)
    return cfg.get("default_tier", "sonnet")  # O(1)


def miss(argv) -> int:  # O(1) + one log append
    """`routing.py miss --task Tn --plan P [--reason R]`: log the kind-miss incident, print the fix routing."""
    ap = argparse.ArgumentParser(prog="routing.py miss")  # O(1)
    ap.add_argument("--task", required=True)  # O(1)
    ap.add_argument("--plan", required=True)  # O(1)
    ap.add_argument("--reason", default="NEEDS_CONTEXT, BLOCKED or red")  # O(1)
    a = ap.parse_args(argv)  # O(1)
    import next_tasks as nt  # O(1)
    import plan_tools as pt  # O(1)
    log = pt.stable_root(a.plan) / ".superlite" / "incidents.jsonl"  # O(1)
    log.parent.mkdir(parents=True, exist_ok=True)  # O(1)
    nt.add_incidents(str(log), [("-", 0, f"kind-miss {a.task}: {a.reason}", "assumption", "coordinator", "")])  # O(1)
    d = load_cfg().get("default_tier", "sonnet")  # O(file)
    print(f"{a.task} kind-miss -> fixer-sonnet note=.superlite/fixes/F-{a.task}-1.md tier={d} (never retried on haiku)")  # O(1)
    return 0  # O(1)


def main(argv=None):  # O(schedule + n*r)
    argv = list(sys.argv[1:] if argv is None else argv)  # O(1)
    if argv and argv[0] == "miss":  # O(1)
        return miss(argv[1:])  # O(1)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])  # O(1)
    ap.add_argument("--plan", required=True)  # O(1)
    ap.add_argument("--config")  # O(1)
    ap.add_argument("--json", action="store_true")  # O(1)
    a = ap.parse_args(argv)  # O(1)
    tiers = tiers_for_plan(a.plan, load_cfg(a.config))  # O(schedule + n*r)
    if a.json:  # O(1)
        print(json.dumps(tiers, indent=1))  # O(n)
    else:  # O(1)
        for t, tier in tiers.items():  # O(n)
            print(f"{t} {tier}")  # O(1)
    return 0  # O(1)


if __name__ == "__main__":  # O(1)
    sys.exit(main())  # O(1)
