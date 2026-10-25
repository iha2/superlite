"""Deterministic tier routing (D36): tier table, strict pool matching, mechanical lane, mini prime, dispatch lines."""
from __future__ import annotations

import hashlib
import json

from test_plan_tools import SCRIPTS, pt, run
from test_stable_split import DISPATCH

ROUTING = SCRIPTS / "routing.py"
BUILD_PRIME = SCRIPTS / "build_prime.py"

MECH = ("**Kind:** mechanical\n**Components:** 1\n**LOC owned:** 60\n**Tests:** fast\n"
        "Tests: `test_{n}` asserts `{n}(2) == 4`.\n")


def task(tid, mech, mins, produces=None, consumes=None):
    body = MECH.format(n=tid.lower()) if mech else "**Components:** 3\n**LOC owned:** 300\n**Tests:** slow\n"
    lines = [f"### {tid} task {tid.lower()} · Complexity 3 · Est. context 30k", f"**Owns:** src/pkg/{tid.lower()}.py, tests/test_{tid.lower()}.py",
             f"**Est. time:** {mins} min", body.rstrip()]
    if produces:
        lines.append(f"**Produces:** {produces}")
    if consumes:
        lines.append(f"**Consumes:** {consumes}")
    return "\n".join(lines) + "\n\n"


def plan(tmp_path, tasks, edges, name="plan.md"):
    head = ("# Plan\n**Goal:** x.\n**Tech stack:** Python\n\nGlobal constraints (from SPEC): stdlib only.\n\n```mermaid\n%% taskgraph\nflowchart LR\n"
            + "".join(f"  {a} --> {b}\n" for a, b in edges) + "```\n\n")
    p = tmp_path / name
    p.write_text(head + "".join(tasks), encoding="utf-8")
    return p


def mech_plan(tmp_path, n, name="plan.md"):
    """n independent mechanical tasks (1.5 min) beside one long design task T9, so none of them is on the critical path."""
    return plan(tmp_path, [task(f"T{i}", True, 1.5) for i in range(1, n + 1)] + [task("T9", False, 6)], [], name=name)


def haiku_lines(d):
    return {t: v for t, v in d.items() if "tier=haiku" in v or v.startswith(("agent=h", "agent=t0"))}


def mixed(tmp_path):
    """T1 design -> T2 mechanical (critical path); T3 mechanical off the path; T4 design off the path."""
    return plan(tmp_path, [task("T1", False, 5, "pkg.t1.f(x: int) -> int"), task("T2", True, 1.5, "pkg.t2.f(x: int) -> int", "pkg.t1.f(x: int) -> int"),
                           task("T3", True, 1.5, "pkg.t3.f(x: int) -> int"), task("T4", False, 3, "pkg.t4.f(x: int) -> int")],
                [("T1", "T2")])


def tiers(p, *extra):
    cp = run(ROUTING, "--plan", p, "--json", *extra)
    assert cp.returncode == 0, cp.both
    return json.loads(cp.out)


def test_tier_table_defaults(tmp_path):
    assert tiers(mixed(tmp_path)) == {"T1": "sonnet", "T2": "sonnet", "T3": "haiku", "T4": "sonnet"}


def test_same_mechanical_task_haiku_with_slack_sonnet_on_critical_path(tmp_path):
    alone = plan(tmp_path, [task("T1", True, 1.5)], [], name="alone.md")  # the only task: it is the critical path
    beside = plan(tmp_path, [task("T1", True, 1.5), task("T9", False, 6)], [], name="beside.md")  # T9 is longer: T1 has slack
    assert tiers(alone)["T1"] == "sonnet"
    assert tiers(beside)["T1"] == "haiku"


def test_critical_path_exemption_is_not_configurable(tmp_path):
    cfg = json.loads((SCRIPTS.parent / "config" / "routing.json").read_text(encoding="utf-8"))
    assert "haiku_on_critical_path" not in cfg
    cfg["haiku_on_critical_path"] = True  # ignored
    cfg["rules"].insert(0, {"tier": "haiku", "when": {"mechanical": True}})  # even a rule that asks for it
    f = tmp_path / "routing.json"
    f.write_text(json.dumps(cfg), encoding="utf-8")
    alone = plan(tmp_path, [task("T1", True, 1.5)], [], name="alone.md")
    assert tiers(alone, "--config", f)["T1"] == "sonnet"


def test_critical_path_mechanical_task_never_goes_to_an_idle_haiku_agent(tmp_path):
    alone = plan(tmp_path, [task("T1", True, 1.5)], [], name="alone.md")
    d, _ = dispatch(tmp_path, alone, [{"id": "t0", "tier": "haiku", "tasks": ["T0"]}])
    assert d["T1"].startswith("fresh tier=sonnet") and "t0" not in d["T1"]


def test_rules_are_data_a_new_rule_changes_the_tier(tmp_path):
    cfg = json.loads((SCRIPTS.parent / "config" / "routing.json").read_text(encoding="utf-8"))
    cfg["rules"].insert(0, {"tier": "haiku", "when": {"kind": "design", "loc_lte": 400, "critical_path": False, "focus_none": ["deep"]}})
    f = tmp_path / "routing.json"
    f.write_text(json.dumps(cfg), encoding="utf-8")
    assert tiers(mixed(tmp_path), "--config", f)["T4"] == "haiku"  # T4: design, 300 LOC, off the critical path
    assert tiers(mixed(tmp_path), "--config", f)["T1"] == "sonnet"  # T1 is on the critical path


def test_tiers_are_deterministic_and_in_stable_json(tmp_path):
    p = mixed(tmp_path)
    a, b = tiers(p), tiers(p)
    assert a == b
    cp = pt("lint", "--stable", "--plan", p, cwd=tmp_path)
    data = json.loads((tmp_path / ".superlite" / "stable.json").read_text(encoding="utf-8"))
    assert {t: v for t, v in a.items() if t in data["stable"]} == data["tiers"]
    assert "TIERS: " in cp.out and "T3=haiku" in cp.out


def pool(tmp_path, agents):
    f = tmp_path / "pool.json"
    f.write_text(json.dumps({"agents": agents}), encoding="utf-8")
    return f


def dispatch(tmp_path, p, agents, *extra, batch="1"):
    """One line per task unless `batch` is None (the config's batching) or a number."""
    extra = extra + (("--batch", batch) if batch else ())
    cp = run(DISPATCH, "--plan", p, "--pool", pool(tmp_path, agents), "--ledger", tmp_path / "l.md", *extra, cwd=tmp_path)
    assert cp.returncode == 0, cp.both
    return {ln.split()[0]: ln.split(" ", 1)[1] for ln in cp.out.splitlines()}, cp.err


def test_dispatch_lines_carry_tier_and_fresh_agent_type(tmp_path):
    d, _ = dispatch(tmp_path, mixed(tmp_path), [])
    assert d["T1"] == "fresh tier=sonnet agent=superlite:implementer"
    assert d["T3"] == "fresh tier=haiku agent=superlite:implementer-haiku"


def test_design_task_never_goes_to_a_haiku_agent_and_vice_versa(tmp_path):
    p = mixed(tmp_path)
    # the haiku agent built T1's files (best score for T4/T1 by any file rule); the sonnet agent knows nothing
    h = {"id": "h1", "tier": "haiku", "tasks": ["T1"], "files": ["src/pkg/t4.py", "src/pkg/t1.py"]}
    s = {"id": "s1", "tier": "sonnet", "tasks": [], "files": ["src/pkg/t3.py"]}
    d, _ = dispatch(tmp_path, p, [h, s])
    for t in ("T1", "T4"):  # design tasks: h1 is never a candidate, whatever its file score
        assert "h1" not in d[t] and "tier=sonnet" in d[t], d[t]
    assert d["T3"] == "agent=h1 tier=haiku"  # mechanical -> haiku agent, not s1 even with its file signal


def test_poll_only_among_same_tier(tmp_path):
    p = plan(tmp_path, [task("T1", False, 3), task("T2", False, 3)], [])
    agents = [{"id": "s1", "tier": "sonnet", "tasks": []}, {"id": "s2", "tier": "sonnet", "tasks": []}, {"id": "h1", "tier": "haiku", "tasks": []}]
    d, _ = dispatch(tmp_path, p, agents)
    polls = [v for v in d.values() if v.startswith("poll")]
    assert polls and all("h1" not in v for v in polls)


def test_idle_haiku_agent_is_reused_without_a_poll(tmp_path):
    h = {"id": "t0", "tier": "haiku", "tasks": ["T0"]}
    d, _ = dispatch(tmp_path, mech_plan(tmp_path, 1), [h])
    assert d["T1"] == "agent=t0 tier=haiku"


def test_fresh_haiku_when_none_idle_eligible(tmp_path):
    busy = {"id": "t0", "tier": "haiku", "tasks": ["T0"], "busy": True, "current": "T0"}
    d, _ = dispatch(tmp_path, mech_plan(tmp_path, 1), [busy])
    assert d["T1"] == "fresh tier=haiku agent=superlite:implementer-haiku"  # never queued behind the busy agent
    d, _ = dispatch(tmp_path, mech_plan(tmp_path, 1), [])
    assert d["T1"].startswith("fresh tier=haiku")


def test_limits_retire_a_haiku_agent(tmp_path):
    worn = {"id": "w", "tier": "haiku", "tasks": [f"t{i}" for i in range(20)]}  # max_tasks 20
    big = {"id": "b", "tier": "haiku", "tasks": ["a"], "context_k": 59}  # + task context over max_context_k 60
    d, err = dispatch(tmp_path, mech_plan(tmp_path, 1), [worn, big])
    assert d["T1"].startswith("fresh tier=haiku")
    assert "evict=w 20 tasks done" in err and "evict=b context over 60k" in err


def test_choice_among_idle_haiku_agents_is_deterministic(tmp_path):
    a = {"id": "a", "tier": "haiku", "tasks": ["x", "y"], "context_k": 10}
    b = {"id": "b", "tier": "haiku", "tasks": ["x"], "context_k": 10}  # same context, fewer tasks
    c = {"id": "c", "tier": "haiku", "tasks": ["x"], "context_k": 30}
    for agents in ([a, b, c], [c, b, a], [b, c, a]):
        d, _ = dispatch(tmp_path, mech_plan(tmp_path, 1), agents)
        assert d["T1"] == "agent=b tier=haiku"
    lowest = {"id": "l", "tier": "haiku", "tasks": ["x", "y", "z"], "context_k": 5}
    d, _ = dispatch(tmp_path, mech_plan(tmp_path, 1), [a, b, lowest])
    assert d["T1"] == "agent=l tier=haiku"  # lowest context wins before fewest tasks


def test_three_ready_one_idle_agent_takes_one_and_two_start_fresh(tmp_path):
    h = {"id": "t0", "tier": "haiku", "tasks": ["T0"]}
    d, _ = dispatch(tmp_path, mech_plan(tmp_path, 3), [h])
    h_lines = sorted(v.split(" tier")[0] for v in haiku_lines(d).values())
    assert h_lines == ["agent=t0", "fresh", "fresh"]


def test_t0_is_the_first_haiku_agent_and_is_reused_after_done(tmp_path):
    t0 = {"id": "t0", "tier": "haiku", "tasks": ["T0"], "context_k": 20}  # idle after DONE
    d, _ = dispatch(tmp_path, mech_plan(tmp_path, 1), [t0])
    assert d["T1"] == "agent=t0 tier=haiku"


def test_no_cross_tier_assignment_in_either_direction(tmp_path):
    p = mixed(tmp_path)
    h = {"id": "h1", "tier": "haiku", "tasks": []}
    s = {"id": "s1", "tier": "sonnet", "tasks": []}
    d, _ = dispatch(tmp_path, p, [h, s])
    assert "s1" not in d["T3"] and d["T3"] == "agent=h1 tier=haiku"  # haiku-tier task: haiku agent only
    for t in ("T1", "T4"):
        assert "h1" not in d[t]  # sonnet-tier tasks never see the haiku agent


def test_legacy_agent_without_tier_is_sonnet(tmp_path):
    d, _ = dispatch(tmp_path, mech_plan(tmp_path, 1), [{"id": "old", "tasks": []}])
    assert d["T1"].startswith("fresh tier=haiku")  # an untiered agent is sonnet: never takes the haiku task


def test_mini_prime_is_small_and_hash_stable(tmp_path):
    p = mixed(tmp_path)
    spec = tmp_path / "SPEC.md"
    spec.write_text("# Spec\n## 1. a\n" + "word " * 4000 + "\n", encoding="utf-8")
    out_h = tmp_path / "h.md"
    runs = [run(BUILD_PRIME, "--plan", p, "--tier", "haiku", "--out", out_h, cwd=tmp_path).out.split() for _ in range(2)]
    assert runs[0] == runs[1] and runs[0][0] == hashlib.sha256(out_h.read_bytes()).hexdigest()
    full = run(BUILD_PRIME, "--plan", p, "--spec", spec, "--sections", "1", "--prime", "full", "--out", tmp_path / "f.md", cwd=tmp_path).out.split()
    assert int(runs[0][1]) < int(full[1]) / 2
    text = out_h.read_text(encoding="utf-8")
    assert text.startswith("# superlite prime (mini)") and "## Stub spec" in text and "stdlib only" in text
    assert "T3" in text.split("## Interfaces your tasks touch")[1] and "T4" not in text.split("## Interfaces your tasks touch")[1]


def test_min_first_time_pass_band_below_minimum_stays_on_sonnet(tmp_path):
    cfg = json.loads((SCRIPTS.parent / "config" / "routing.json").read_text(encoding="utf-8"))
    cfg["rules"][0]["min_first_time_pass"] = 0.8
    f = tmp_path / "routing.json"
    cfg["rules"][0]["evidence"] = {"n": 12, "rate": 0.9}
    f.write_text(json.dumps(cfg), encoding="utf-8")
    assert tiers(mixed(tmp_path), "--config", f)["T3"] == "haiku"  # measured above the minimum
    cfg["rules"][0]["evidence"] = {"n": 12, "rate": 0.5}
    f.write_text(json.dumps(cfg), encoding="utf-8")
    assert tiers(mixed(tmp_path), "--config", f)["T3"] == "sonnet"  # below: the band stays on Sonnet
    del cfg["rules"][0]["evidence"]
    f.write_text(json.dumps(cfg), encoding="utf-8")
    assert tiers(mixed(tmp_path), "--config", f)["T3"] == "haiku"  # no evidence yet: no band disabled


def test_kind_miss_logs_incident_and_task_never_returns_to_haiku(tmp_path):
    p = mech_plan(tmp_path, 2)
    d, _ = dispatch(tmp_path, p, [])
    assert "tier=haiku" in d["T1"] or "tier=haiku" in d["T2"]
    cp = run(ROUTING, "miss", "--task", "T1", "--plan", p, cwd=tmp_path)
    assert cp.returncode == 0, cp.both
    assert cp.out.startswith("T1 kind-miss -> fixer-sonnet note=.superlite/fixes/F-T1-1.md tier=sonnet")
    log = (tmp_path / ".superlite" / "incidents.jsonl").read_text(encoding="utf-8")
    assert "kind-miss T1" in log
    d, _ = dispatch(tmp_path, p, [])
    assert d["T1"].startswith("fresh tier=sonnet")  # never retried on haiku
    assert "tier=haiku" in d["T2"]
