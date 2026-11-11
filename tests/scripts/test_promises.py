"""Promise tests: one per architecture decision, named after it, so a failure reads "superlite broke promise D11".

Most run inside a fake swarm run: `Swarm` drives the repo's own launcher (dispatch, fake agents that write each task's
Owns files, checkpoint) in a throwaway git repo (scenario.Repo, isolated HOME) and records a trace of every round.
The tests then read the trace. A plan is always copied into the temp repo before any tool touches it, because a tool
pointed at a plan inside this checkout would read the live run's state.

Covered elsewhere, not repeated here:
- every test run is a background run (D22): test_guard_matrix.py (a raw test command is always rewritten);
- the tier guard and the question guard (D6/D39 as a hook decision): test_guard_matrix.py (`agent_fixer_below_tier`);
- hung tests are reported, not killed: test_testprobe.py.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import fake_agent
import scenario

ROOT = scenario.ROOT
SCRIPTS = scenario.SCRIPTS
sys.path.insert(0, str(SCRIPTS))
import planfile  # noqa: E402

BASH = scenario.BASH
LINE = re.compile(r"^(?P<tasks>T\d+[a-z]*(?:,T\d+[a-z]*)*) (?:agent=(?P<agent>\S+)|poll (?P<poll>\S+)|fresh) tier=(?P<tier>\w+)")
LEDGER = re.compile(r"^- checkpoint (T\d+[a-z]*) ([0-9a-f]{7,40})", re.M)
PEER = "superpowers@claude-plugins-official"

pytestmark = pytest.mark.slow


# ---------------------------------------------------------------- plans and helpers

def make_plan(tasks: dict, edges=(), review: str | None = None, budget: str | None = None) -> str:
    """tasks: {id: {"mech": bool, "eg": bool, "minutes": float}}; edges: [(producer, consumer)].
    An edge is waited on unless the producer has an e.g. example."""
    deps: dict = {}
    for a, b in edges:
        deps.setdefault(b, []).append(a)
    out = ["# Promise plan", "", "**Goal:** exercise the promises.", "**Architecture:** modules.",
           "**Tech stack:** Python 3.12, pytest.", "**Target:** 20 min"]
    if review:
        out.append(f"**Review plan:** {review}")
    if budget:
        out.append(f"**Budget-usd:** {budget}")
    out += ["", "## Task graph", "", "```mermaid", "%% taskgraph", "flowchart LR"]
    out += [f"  {a} --> {b}" for a, b in edges] or ["  T1"]
    out += ["```", ""]
    for tid, f in tasks.items():
        n = tid[1:]
        out += [f"### {tid} mod{n} · Complexity 2 · Est. context 20k", f"**Owns:** src/m{n}.py, tests/test_m{n}.py",
                f"**Est. time:** {f.get('minutes', 2.0)} min (1 component, about 20 LOC, tests: fast)"]
        if f.get("mech"):
            out += ["**Kind:** mechanical", "**Components:** 1", "**LOC owned:** 20", "**Tests:** fast",
                    f"**Test code:** assert m{n}() == {n}"]
        out.append(f"**Produces:** m{n}() -> int")
        if tid in deps:
            out.append("**Consumes:** " + " · ".join(f"m{d[1:]}() -> int" for d in deps[tid]))
        if f.get("eg"):
            out.append(f"e.g. m{n}() -> {n}")
        out.append("")
    return "\n".join(out)


# Seven tasks: two fast-tier (mechanical, off the critical path), a waited chain T1 -> T3 -> T7, a join at T6 and T7.
RUN_TASKS = {"T1": {"minutes": 3.0}, "T2": {"minutes": 2.0}, "T3": {"minutes": 2.0}, "T4": {"mech": True, "minutes": 1.0},
             "T5": {"mech": True, "minutes": 1.0}, "T6": {"minutes": 2.0}, "T7": {"minutes": 1.0}}
RUN_EDGES = [("T1", "T3"), ("T3", "T7"), ("T4", "T6"), ("T5", "T6"), ("T6", "T7")]
RUN_IDS = list(RUN_TASKS)


@pytest.fixture
def repo(tmp_path):
    return scenario.Repo(tmp_path)


def plan_cmd(repo, sub, *args):
    """`plan <sub>` where the plan area exists, else the older plan_tools name (the launcher answers exit 2)."""
    r = repo.cli("plan", sub, *args)
    return r if r.code != 2 or "unknown tool" not in r.err else repo.cli("plan_tools", sub, *args)


def ensure(repo, slug):
    r = repo.cli("session", "ensure", "--slug", slug)
    assert r.code == 0, r.err
    return r


def write_plan(repo, tasks=None, edges=None, name="docs/plans/2026-11-01-promise.md", **kw) -> Path:
    return repo.write(name, make_plan(RUN_TASKS if tasks is None else tasks, RUN_EDGES if edges is None else edges, **kw))


def ledger(repo) -> dict:
    text = repo.state("progress.md") or ""
    return {m[1]: m[2] for m in LEDGER.finditer(text)}


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ---------------------------------------------------------------- the fake swarm run

class Swarm:
    """Dispatch, start every offered task on an agent, let fake agents finish the oldest, checkpoint, repeat."""

    def __init__(self, repo, plan, cap=3, check_prime=False):
        self.repo, self.plan, self.cap, self.check_prime = repo, Path(plan), cap, check_prime
        self.pool = repo.root / ".superlite" / "pool.json"
        self.order: list = []  # running tasks, oldest first
        self.trace: list = []
        self.codes: list = []  # every launcher exit code the run saw, with the command
        self.waited = planfile.read(str(plan)).waited_edges()
        self.tasks = list(planfile.read(str(plan)).tasks)

    def cli(self, *args):
        r = self.repo.cli(*args)
        self.codes.append((args[0], r.code))
        return r

    def start(self):
        r = plan_cmd(self.repo, "lint", "--plan", self.plan, "--stable")
        self.codes.append(("lint", r.code))
        assert r.code == 0, r.out + r.err
        self.stable = self.repo.state("stable.json")
        self.prime_line = self.build_prime()
        return r

    def build_prime(self, *extra):
        r = self.cli("build_prime", "--plan", self.plan, *extra)
        assert r.code == 0, r.out + r.err
        return r.out.strip()

    def agents(self) -> list:
        return json.loads(self.pool.read_text(encoding="utf-8"))["agents"] if self.pool.is_file() else []

    def save(self, agents):
        self.repo.write(".superlite/pool.json", json.dumps({"agents": agents}))

    def prime_hashes(self):
        sl = self.repo.root / ".superlite"
        return tuple(sha256(f) for f in (sl / "prime.md", sl / "prime-haiku.md") if f.is_file())

    def round(self, finish=1, dispatch_args=()):
        done = set(ledger(self.repo))
        agents = self.agents()
        busy = {a["id"]: a["current"] for a in agents if a.get("current")}
        r = self.cli("dispatch", "--plan", self.plan, "--pool", self.pool, "--cap", self.cap, "--batch", 1, *dispatch_args)
        assert r.code == 0, r.out + r.err
        offered = []
        for ln in r.out.splitlines():
            m = LINE.match(ln)
            if m and not ln.startswith("T0 "):
                offered.append({"task": m["tasks"], "agent": m["agent"], "poll": (m["poll"] or "").split(",") if m["poll"] else [],
                                "tier": m["tier"], "line": ln})
        running = list(self.order)
        ready = [t for t in self.stable["stable"] if t not in done and t not in running
                 and all(p in done for p, c in self.waited if c == t)]
        entry = {"done": done, "busy": busy, "agents": [dict(a) for a in agents], "running": running, "offered": offered,
                 "ready": ready, "expected": min(len(ready), max(0, self.cap - len(running))), "out": r.out,
                 "prime": self.prime_hashes(), "prime_line": self.build_prime() if self.check_prime else None}
        self.trace.append(entry)
        by_id = {a["id"]: a for a in agents}
        for o in offered:
            if o["agent"]:
                aid = o["agent"]
            elif o["poll"]:
                aid = sorted(o["poll"], key=lambda k: (by_id[k].get("context_k", 0), k))[0]
            else:
                aid = f"a{len(by_id) + 1}"
                by_id[aid] = {"id": aid, "tier": o["tier"], "tasks": [], "context_k": 20, "idle_min": 0}
            by_id[aid]["current"] = o["task"]
            self.order.append(o["task"])
        self.save(list(by_id.values()))
        self.finish_oldest(finish)
        return entry

    def finish_oldest(self, n=1):
        for t in list(self.order)[:n]:
            fake_agent.do(t, self.plan, self.repo)
            r = self.cli("checkpoint", "--plan", self.plan, t)
            assert r.code == 0, r.out + r.err
            self.order.remove(t)

    def run(self, rounds=30, finish=1):
        for _ in range(rounds):
            if set(self.tasks) <= set(ledger(self.repo)):
                return self
            self.round(finish)
        raise AssertionError(f"the fake run did not finish: done {sorted(ledger(self.repo))} of {self.tasks}")


@pytest.fixture(scope="module")
def ai_run(tmp_path_factory):
    """One whole fake run in AI mode, shared by the promises that read its trace."""
    repo = scenario.Repo(tmp_path_factory.mktemp("airun"))
    ensure(repo, "ai")
    assert repo.cli("orchestrator", "set", "ai").code == 0
    plan = write_plan(repo)
    swarm = Swarm(repo, plan, check_prime=True)
    swarm.start()
    swarm.run(finish=2)
    return swarm


# ---------------------------------------------------------------- D7, D48: one approval gate, then no stops

BUILD_TOOLS = [("plan", "lint", "--stable"), ("dispatch",), ("wave0",), ("build_prime",)]


def build_tool(repo, plan, tool):
    if tool[0] == "plan":
        r = plan_cmd(repo, tool[1], "--plan", plan, *tool[2:])
    else:
        r = repo.cli(*tool, "--plan", plan)
    return r


def test_d7_d48_one_approval_gate_then_no_stops(repo):
    ensure(repo, "human")
    review = repo.write("docs/plans/2026-11-01-promise.review.md", "# Review plan\n\nPlain-language plan.\n")
    plan = write_plan(repo, review="docs/plans/2026-11-01-promise.review.md")
    assert repo.cli("orchestrator", "set", "human").code == 0
    for tool in BUILD_TOOLS:  # before approval every build tool refuses
        r = build_tool(repo, plan, tool)
        assert r.code == 4, (tool, r)
    r = repo.cli("orchestrator", "approve", "--review", review)  # a bare yes with no reply on record is refused
    assert r.code == 3
    repo.human_reply(review)
    r = repo.cli("orchestrator", "approve", "--review", review)
    assert r.code == 0, r.err
    st = repo.state("orchestrator.json")
    assert st["approved"] is True
    assert st["review_sha256"] == sha256(review) and st["review_path"].endswith("2026-11-01-promise.review.md")
    header = re.search(r"^\*\*Review plan:\*\*\s*(\S+)", plan.read_text(encoding="utf-8"), re.M)
    assert header and Path(header[1]).name == Path(st["review_path"]).name  # the machine plan names the approved review plan
    repo.git("add", str(review.relative_to(repo.root)))  # see the xfail below: an untracked review plan blocks checkpoints
    repo.git("commit", "-q", "-m", "review plan")
    swarm = Swarm(repo, plan)
    swarm.start()
    swarm.run(finish=2)  # lint, dispatch, build_prime, checkpoint: no gate denial after the one approval
    assert all(code == 0 for _, code in swarm.codes), swarm.codes
    assert repo.cli("finish", "--plan", plan).code == 0


def test_d7_the_review_plan_beside_the_plan_does_not_block_checkpoints(repo):
    review = repo.write("docs/plans/2026-11-01-promise.review.md", "# Review plan\n")
    plan = write_plan(repo, review="docs/plans/2026-11-01-promise.review.md")
    fake_agent.do("T2", plan, repo)
    assert repo.cli("checkpoint", "--plan", plan, "T2").code == 0
    assert review.is_file()


# ---------------------------------------------------------------- D11: no agent queues behind another

def test_d11_no_agent_queues_behind_another(ai_run):
    assert len(ai_run.trace) >= 4
    assert any(e["busy"] for e in ai_run.trace), "the run never had a busy agent, so nothing was tested"
    for i, e in enumerate(ai_run.trace):
        used = []
        for o in e["offered"]:
            named = [o["agent"]] if o["agent"] else o["poll"]
            for aid in named:
                assert aid not in e["busy"], f"round {i}: {o['line']} goes to busy agent {aid}"
                assert next(a for a in e["agents"] if a["id"] == aid)["tier"] == o["tier"], f"round {i}: crosses tiers"
            used += named
        assert len(set(used)) == len(used), f"round {i}: an agent on two lines"
        got = [t for o in e["offered"] for t in o["task"].split(",")]
        assert len(got) == e["expected"], f"round {i}: ready {e['ready']} with {len(e['running'])} running, cap {ai_run.cap}, offered {got}"
        assert set(got) <= set(e["ready"])


# ---------------------------------------------------------------- D4, D36: the prime stays byte-identical

def test_d4_d36_the_prime_stays_byte_identical(ai_run):
    first = ai_run.trace[0]
    assert first["prime"], "no prime written"
    for i, e in enumerate(ai_run.trace):
        assert e["prime"] == first["prime"], f"round {i}: the prime on disk changed during the run"
        assert e["prime_line"] == ai_run.prime_line, f"round {i}: rebuilding the prime gave other bytes"


def test_d4_d36_the_prime_survives_fix_task_patches(repo):
    """T2 consumes an interface nobody produces (a FIX task); patching it and re-linting leaves the built prime's bytes alone."""
    ensure(repo, "fix")
    tasks = {"T1": {}, "T2": {}, "T3": {"mech": True, "minutes": 1.0}}
    plan = write_plan(repo, tasks, [])
    broken = plan.read_text(encoding="utf-8").replace("### T2 mod2", "### T2 mod2", 1).replace(
        "**Produces:** m2() -> int", "**Produces:** m2() -> int\n**Consumes:** m9() -> int", 1)
    plan.write_text(broken, encoding="utf-8")
    swarm = Swarm(repo, plan)
    r = swarm.start()
    assert "T2" not in swarm.stable["stable"] and any(f["task"] == "T2" for f in swarm.stable["fix"]), r.out
    prime, line = swarm.prime_hashes(), swarm.prime_line
    plan.write_text(broken.replace("m9() -> int", "m1() -> int"), encoding="utf-8")  # the patch
    assert swarm.build_prime() == line and swarm.prime_hashes() == prime  # patched, not yet re-linted: same prime
    assert plan_cmd(repo, "lint", "--plan", plan, "--stable").code == 0
    assert "T2" in repo.state("stable.json")["stable"]
    assert swarm.prime_hashes() == prime, "re-linting after the patch rewrote the prime on disk"


# ---------------------------------------------------------------- D34: dispatched tasks are frozen

def test_d34_dispatched_tasks_are_frozen(repo):
    ensure(repo, "frozen")
    plan = write_plan(repo)
    original = plan.read_text(encoding="utf-8")
    assert plan_cmd(repo, "lint", "--plan", plan, "--stable").code == 0
    ledger_file = repo.root / ".superlite" / "progress.md"
    ledger_file.write_text("# Progress ledger\n\n- unblocked: dispatched T0 T2 T4 at 12:00:00; waiting on fixes: none\n", encoding="utf-8")
    plan.write_text(original.replace("**Owns:** src/m4.py, tests/test_m4.py", "**Owns:** src/m4.py, tests/test_m4.py, src/extra.py"), encoding="utf-8")
    r = plan_cmd(repo, "lint", "--plan", plan, "--stable")
    assert re.search(r"^\s*(PLAN: )?FROZEN: T4\b", r.out, re.M), r.out
    plan.write_text(original + "\n", encoding="utf-8")  # a rewrite that keeps every dispatched block byte-identical
    r = plan_cmd(repo, "lint", "--plan", plan, "--stable")
    assert "FROZEN:" not in r.out, r.out


# ---------------------------------------------------------------- D29, D30, D36: only the plan's tiers

def test_d29_d30_d36_opus_is_rejected_at_lint(repo):
    ensure(repo, "opus")
    plan = write_plan(repo)
    text = plan.read_text(encoding="utf-8").replace("**Kind:** mechanical", "**Kind:** mechanical\n**Model:** opus", 1)
    plan.write_text(text, encoding="utf-8")
    r = plan_cmd(repo, "lint", "--plan", plan, "--stable")
    assert "OPUS-TASK: T4" in r.out
    assert "T4" not in repo.state("stable.json")["stable"]


def test_d29_d30_d36_the_fast_tier_is_never_on_the_critical_path(ai_run):
    stable = ai_run.stable
    assert stable["critical_path"], stable
    fast = [t for t, tier in stable["tiers"].items() if tier == "haiku"]
    assert fast, "the plan has no fast-tier task, so nothing was tested"
    assert not set(fast) & set(stable["critical_path"])
    for e in ai_run.trace:
        for o in e["offered"]:
            assert o["tier"] == stable["tiers"][o["task"]]
            assert not (o["tier"] == "haiku" and o["task"] in stable["critical_path"])


def test_d29_d30_d36_after_a_fast_tier_miss_the_task_never_goes_back(repo):
    ensure(repo, "miss")
    plan = write_plan(repo)
    swarm = Swarm(repo, plan)
    swarm.start()
    first = swarm.round(finish=0)
    assert next(o for o in first["offered"] if o["task"] == "T4")["tier"] == "haiku"
    r = repo.cli("routing", "miss", "--task", "T4", "--plan", plan, "--reason", "promise test")
    assert r.code == 0, r.err
    swarm.save([])  # the agent is gone; nothing is checkpointed, so T4 is offered again
    swarm.order.clear()
    again = swarm.round(finish=0)
    assert next(o for o in again["offered"] if o["task"] == "T4")["tier"] == "sonnet"
    swarm.save([])
    swarm.order.clear()
    assert next(o for o in swarm.round(finish=0)["offered"] if o["task"] == "T4")["tier"] == "sonnet"


# ---------------------------------------------------------------- D6, D39: fixes go up the tiers, never down

def pre_tool(repo, tool_input):
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PROJECT_DIR", "CURSOR_PROJECT_DIR")}
    env["CLAUDE_PROJECT_DIR"] = str(repo.root)
    raw = json.dumps({"tool_name": "Agent", "tool_input": tool_input})
    return subprocess.run([BASH, str(ROOT / "hooks" / "pre-tool")], input=raw, capture_output=True, text=True, env=env, timeout=60)


def test_d6_d39_fixes_go_up_the_tiers_never_down(repo):
    ensure(repo, "fixes")
    assert repo.cli("orchestrator", "set", "ai").code == 0
    plan = write_plan(repo)
    repo.write(".superlite/pool.json", "{}")  # a build is on
    assert repo.cli("checkpoint", "--plan", plan, "--blocked", "T5", "--tier", "opus").code == 0
    assert pre_tool(repo, {"subagent_type": "fixer-sonnet", "prompt": "Fix BLOCKED T5"}).returncode == 2
    assert pre_tool(repo, {"subagent_type": "fixer-opus", "prompt": "Fix BLOCKED T5"}).returncode == 0
    # no fixer runs on the fast tier: there is no such agent, and the fast tier's agent is an implementer
    names = sorted(p.stem for p in (ROOT / "agents").glob("*.md"))
    assert not [n for n in names if n.startswith("fixer") and "haiku" in n], names
    cfg = json.loads((SCRIPTS.parent / "config" / "routing.json").read_text(encoding="utf-8"))
    assert "fixer" not in cfg["tiers"]["haiku"]["agent"]


# ---------------------------------------------------------------- D13: whoever finds a defect fixes it, with a test

def test_d13_a_checkpoint_accepts_regression_tests_from_any_task(repo):
    plan = write_plan(repo)
    fake_agent.do("T2", plan, repo)
    repo.write("tests/regression/test_probe_deep_1.py", "def test_probe_deep_1():\n    assert True\n")
    r = repo.cli("checkpoint", "--plan", plan, "T2")
    assert r.code == 0, r.out + r.err
    names = repo.git("diff-tree", "-r", "--no-commit-id", "--name-only", f"{r.out.split()[-1]}^", r.out.split()[-1]).split()
    assert "tests/regression/test_probe_deep_1.py" in names and "src/m2.py" in names




# ---------------------------------------------------------------- D20, D25: the final reviewer sees only the packet

def test_d20_d25_the_packet_stands_alone_and_is_capped(repo):
    plan = write_plan(repo, {"T1": {}, "T2": {}}, [])
    for i in range(1, 9):
        repo.write(f"src/big{i}.py", "\n".join(f"def f{i}_{j}():\n    return {j}" for j in range(300)) + "\n")
    repo.git("add", "-A")
    repo.git("commit", "-q", "-m", "base")
    base = repo.git("rev-parse", "HEAD").strip()
    for i in range(1, 9):  # a large diff in eight files, each also named by an incident
        repo.write(f"src/big{i}.py", "\n".join(f"def g{i}_{j}():\n    return {j + 1}" for j in range(300)) + "\n")
        r = repo.cli("incident", "add", "--file", f"src/big{i}.py", "--line", 3, "--context", "boom", "--agent", "T1", "--kind", "test-failure")
        assert r.code == 0, r.err
    cap_tokens = 6000
    r = plan_cmd(repo, "packet", "--plan", plan, "--base", base, "--max-tokens", cap_tokens)
    assert r.code == 0, r.err
    text = (repo.root / ".superlite" / "review-packet.md").read_text(encoding="utf-8")
    assert len(text) <= int(cap_tokens * 1.9) + 100  # the token cap, in characters
    assert ".superlite" not in text, "the packet sends the reviewer to run-state files"
    blocks = re.findall(r"^### (\S+)\n```diff\n(.*?)\n```", text, re.M | re.S)
    assert blocks, "no source excerpts in the packet"
    assert all(len(body.splitlines()) <= 120 for _, body in blocks)
    assert sum(len(body.splitlines()) for _, body in blocks) <= 600
    fm = re.match(r"^---\n(.*?)\n---", (ROOT / "agents" / "final-reviewer.md").read_text(encoding="utf-8"), re.S)
    tools = re.search(r"^tools:\s*(.*)$", fm[1], re.M)[1]
    assert {t.strip() for t in tools.split(",")} <= {"Read", "Grep", "Glob"}  # read-only


# ---------------------------------------------------------------- D46, D47: rulings, not stalls

def test_d46_d47_ai_mode_gates_nothing_and_rulings_surface_at_finish(ai_run):
    repo = ai_run.repo  # the shared AI run: it ended with every launcher call at exit 0
    assert all(code == 0 for _, code in ai_run.codes), ai_run.codes  # nothing was gated
    r = repo.cli("orchestrator", "set", "ai", "--ruling", "AI orchestrator - the request was headless - none")
    assert r.code == 0, r.err
    assert "Ruling: AI orchestrator" in repo.state("progress.md")
    with (repo.root / ".superlite" / "progress.md").open("a", encoding="utf-8") as fh:
        fh.write("Ruling: [scope] drop the export command - nobody uses it - low\n")
        fh.write("Ruling: [design] use a dict - simpler - none\n")
    r = repo.cli("finish", "--plan", ai_run.plan)
    assert r.code == 0, r.err
    block = r.out.split("Changed from the approved plan:", 1)[1].split("\n\n", 1)[0]
    assert block.startswith(" 1") and "drop the export command" in block and "use a dict" not in block


# ---------------------------------------------------------------- D37: a soft budget

def usage_transcript(path: Path, out_tokens: int) -> None:
    rows = [{"type": "user", "timestamp": "2026-10-18T10:00:00Z", "message": {"role": "user", "content": "Implement T1 slug"}},
            {"type": "assistant", "timestamp": "2026-10-18T10:00:30Z",
             "message": {"id": "m1", "model": "claude-sonnet-5-5", "content": [{"type": "text", "text": "Status: DONE"}],
                         "usage": {"input_tokens": 100, "output_tokens": out_tokens, "cache_read_input_tokens": 0,
                                   "cache_creation_input_tokens": 0}}}]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_d37_a_soft_budget_warns_at_70_and_stops_at_100(repo):
    transcript = repo.root / "a1.jsonl"
    usage_transcript(transcript, 400_000)
    repo.write(".superlite/metrics.jsonl", "\n".join(json.dumps(x) for x in [
        {"ev": "launch", "t": 1_700_000_000_000, "agent_id": "a1", "type": "superlite:implementer", "model": "sonnet", "desc": "T1 slug"},
        {"ev": "stop", "t": 1_700_000_090_000, "agent_id": "a1", "type": "superlite:implementer", "transcript": str(transcript),
         "last": "Status: DONE"}]) + "\n")
    spent = None
    seen = {}
    for label, ratio in (("probe", None), ("69", 0.69), ("70", 0.71), ("100", 1.01)):
        if ratio is None:  # first pass: no cap, just read what was spent
            plan = write_plan(repo, {"T1": {}}, [], name="docs/plans/2026-11-01-budget.md")
            spent = json.loads(repo.cli("budget", "--plan", plan, "--json").out)["cost_usd"]
            assert spent > 0
            continue
        plan = write_plan(repo, {"T1": {}}, [], name="docs/plans/2026-11-01-budget.md", budget=f"{spent / ratio:.6f}")
        (repo.root / ".superlite" / "budget.state").unlink(missing_ok=True)
        first = repo.cli("budget", "--plan", plan, "--changed")
        second = repo.cli("budget", "--plan", plan, "--changed")
        assert first.code == second.code == 0
        assert second.out == "", "an unchanged budget line printed twice"
        seen[label] = first.out
    assert "WARN" not in seen["69"] and "OVER CAP" not in seen["69"]
    assert "WARN" in seen["70"] and "OVER CAP" not in seen["70"]
    assert "OVER CAP" in seen["100"]


# ---------------------------------------------------------------- using-superlite loads in every harness

SKILL = ROOT / "skills" / "using-superlite" / "SKILL.md"
SKILL_BODY = SKILL.read_text(encoding="utf-8").split("\n---\n", 1)[1]
SKILL_LINES = [ln.strip() for ln in SKILL_BODY.splitlines() if ln.strip()]
SESSION_START_CAP = 3500  # bytes of context a steady-state session start adds (measured 2.5 KB)
FIRST_SESSION_CAP = 7500  # the first session of a version also prints the intro screen once


def session_start(repo, host: str) -> dict:
    env = {"PATH": os.environ["PATH"], "HOME": str(repo.home), "SUPERLITE_HOME": str(repo.home)}
    env["CURSOR_PLUGIN_ROOT" if host == "cursor" else "CLAUDE_PLUGIN_ROOT"] = str(ROOT)
    p = subprocess.run([BASH, str(ROOT / "hooks" / "session-start")], cwd=repo.root, env=env, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return {"size": len(p.stdout.encode("utf-8")), "json": json.loads(p.stdout)}


@pytest.mark.parametrize("host", ["claude", "cursor"])
def test_using_superlite_loads_through_the_session_start_hook(repo, host):
    first = session_start(repo, host)
    steady = session_start(repo, host)
    key = (lambda j: j["hookSpecificOutput"]["additionalContext"]) if host == "claude" else (lambda j: j["additional_context"])
    for run, cap in ((first, FIRST_SESSION_CAP), (steady, SESSION_START_CAP)):
        context = key(run["json"])
        assert all(ln in context for ln in SKILL_LINES), "the skill is not delivered whole"
        assert re.search(r"^Project state: (empty folder|existing code|run in progress)\.$", context, re.M)
        assert run["size"] <= cap, f"{run['size']} bytes over the {cap} cap"
    if host == "cursor":
        assert "hookSpecificOutput" not in steady["json"]


# ---------------------------------------------------------------- peers are suspended for the run

def find_node():
    candidates = [shutil.which("node")] + sorted(Path.home().glob(".nvm/versions/node/*/bin/node"), reverse=True)
    for c in filter(None, candidates):
        v = subprocess.run([str(c), "--version"], capture_output=True, text=True).stdout.strip().lstrip("v").split(".")[0]
        if v.isdigit() and int(v) >= 16:
            return str(c)
    return None


def test_using_superlite_loads_through_the_opencode_plugin(tmp_path):
    node = find_node()
    if not node:
        pytest.skip("node 16 or newer is not installed")
    script = tmp_path / "boot.mjs"
    script.write_text(f"""import {{ SuperlitePlugin }} from {json.dumps((ROOT / '.opencode/plugins/superlite.js').as_uri())};
const hooks = await SuperlitePlugin({{ client: null, directory: '.' }});
const out = {{ messages: [{{ info: {{ role: 'user', sessionID: 's1' }}, parts: [{{ type: 'text', text: 'hello' }}] }}] }};
await hooks['experimental.chat.messages.transform']({{}}, out);
process.stdout.write(JSON.stringify(out.messages[0].parts.map(p => p.text)));
""", encoding="utf-8")
    p = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert p.returncode == 0, p.stderr
    parts = json.loads(p.stdout)
    assert parts[-1] == "hello" and all(ln in parts[0] for ln in SKILL_LINES)
    assert len(parts[0].encode("utf-8")) <= 9000  # the skill plus OpenCode's tool mapping (measured about 6.5 KB)


def settings(root: Path) -> dict:
    f = root / ".claude" / "settings.local.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}


def test_peers_are_suspended_for_the_run_and_released_by_the_last_finish(repo):
    env = {"CLAUDE_CODE": "1"}
    ensure(repo, "one")
    two = repo.cli("session", "ensure", "--slug", "two", "--move", "yes")
    assert two.code == 0, two.out + two.err
    homes = [Path(repo.session_row(slug)["worktree"]) for slug in ("one", "two")]
    for home in homes:  # a fake superpowers install in each checkout
        (home / ".claude").mkdir(exist_ok=True)
        (home / ".claude" / "settings.local.json").write_text(json.dumps({"enabledPlugins": {PEER: True}}), encoding="utf-8")
        r = repo.cli("orchestrator", "set", "ai", env=env, cwd=home)
        assert r.code == 0 and "peers: suspended" in r.out, r.out + r.err
        assert settings(home)["enabledPlugins"][PEER] is False
    out = repo.cli("finish", env=env, cwd=homes[0])  # the first run finishes while the second is open
    assert out.code == 0, out.err
    assert "peers: suspended" in out.out and "other run" in out.out
    assert settings(homes[0])["enabledPlugins"][PEER] is False and settings(homes[1])["enabledPlugins"][PEER] is False
    out = repo.cli("finish", env=env, cwd=homes[1])  # the last one releases all
    assert out.code == 0, out.err
    assert "peers: clear" in out.out
    assert all(settings(h)["enabledPlugins"][PEER] is True for h in homes)


# ---------------------------------------------------------------- resume never redoes finished work, stop stops cleanly

def test_resume_never_redoes_finished_work(repo):
    ensure(repo, "resume")
    assert repo.cli("orchestrator", "set", "ai").code == 0
    plan = write_plan(repo)
    first = Swarm(repo, plan)
    first.start()
    for _ in range(3):
        first.round(finish=1)
    done = ledger(repo)
    in_flight = list(first.order)
    assert done and in_flight, (done, in_flight)
    refs = repo.git("for-each-ref", "refs/superlite").splitlines()
    # the session dies: the pool is lost, the ledger and the saved checkpoints stay
    (repo.root / ".superlite" / "pool.json").unlink()
    second = Swarm(repo, plan)
    second.stable = repo.state("stable.json")
    second.prime_line = second.build_prime()
    entry = second.round(finish=0)
    sent = [t for o in entry["offered"] for t in o["task"].split(",")]
    assert not set(sent) & set(done), f"checkpointed tasks were sent again: {sent}"
    assert set(in_flight) & set(sent), "tasks that were running at the stop are not re-dispatched"
    second.finish_oldest(len(second.order))
    second.run()
    for t, sha in done.items():
        assert ledger(repo)[t] == sha, f"{t} was checkpointed a second time"
    assert set(refs) <= set(repo.git("for-each-ref", "refs/superlite").splitlines())


def test_stop_stops_cleanly(repo):
    """`/superlite:stop` is a command text; the code it leans on is that a full cap sends nothing and in-flight work checkpoints."""
    ensure(repo, "stop")
    assert repo.cli("orchestrator", "set", "ai").code == 0
    plan = write_plan(repo)
    swarm = Swarm(repo, plan, cap=3)
    swarm.start()
    swarm.round(finish=0)
    running = list(swarm.order)
    assert running
    stopped = Swarm(repo, plan, cap=len(running))  # stop = no free slot: nothing new may leave dispatch
    stopped.stable, stopped.order = swarm.stable, running
    entry = stopped.round(finish=0)
    assert [o["task"] for o in entry["offered"]] == [], entry["out"]
    stopped.finish_oldest(len(running))  # the in-flight tasks still checkpoint
    assert set(running) <= set(ledger(repo))


# ---------------------------------------------------------------- lean by design

PROMPT_HOOK = ROOT / "hooks" / "prompt"
PRIME_CAP = 2600  # bytes of the slim prime for the reference plan below (measured at about 2.0 KB)
PROMPT_CAP = 700  # bytes added to a turn by the prompt hook with a session open


def test_lean_by_design(repo):
    ensure(repo, "lean")
    # session start: the steady-state payload is capped above (test_using_superlite_loads_through_the_session_start_hook)
    session_start(repo, "claude")  # the first session of a version prints the intro screen
    steady = session_start(repo, "claude")
    assert steady["size"] <= SESSION_START_CAP, steady["size"]
    # per turn: the prompt hook with a session open
    env = repo.env({"CLAUDE_PLUGIN_ROOT": str(ROOT)})
    raw = json.dumps({"prompt": "continue", "cwd": str(repo.root)})
    p = subprocess.run([BASH, str(PROMPT_HOOK)], input=raw, cwd=repo.root, env=env, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0
    assert len(p.stdout.encode("utf-8")) <= PROMPT_CAP, p.stdout
    # the slim prime on the reference plan
    plan = repo.write("docs/plans/2026-11-01-ref.md", (Path(__file__).parent / "fixtures" / "flow_plan.md").read_text(encoding="utf-8"))
    assert plan_cmd(repo, "lint", "--plan", plan, "--stable").code == 0
    r = repo.cli("build_prime", "--plan", plan)
    assert r.code == 0, r.err
    size = int(r.out.split()[1])
    assert size <= PRIME_CAP, f"slim prime is {size} bytes"
