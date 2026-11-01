"""Properties over random task graphs and pools: what the scheduler may never do, what a checkpoint records, and
that the tools that print for agents print the same bytes every time.

Every example runs the repo's own launcher in a throwaway git repo (scenario.Repo), so a property holds for the real
tools, not a model of them. The examples are few and each costs about a second.
"""
from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

hypothesis = pytest.importorskip("hypothesis", reason="hypothesis is a dev dependency; install it to run the properties")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

import fake_agent  # noqa: E402
import scenario  # noqa: E402

pytestmark = pytest.mark.slow

SCRIPTS = scenario.SCRIPTS
sys.path.insert(0, str(SCRIPTS))
import planfile  # noqa: E402

PROP = settings(max_examples=12, deadline=None, derandomize=True,
                suppress_health_check=list(HealthCheck))
SAME = settings(max_examples=3, deadline=None, derandomize=True,  # a determinism example runs the tool three times
                suppress_health_check=list(HealthCheck))


# ---------------------------------------------------------------- plans and repos

def make_plan(tasks: dict, edges=()) -> str:
    """tasks: {id: {"mech": bool, "eg": bool, "minutes": float}}; edges: [(producer, consumer)].
    An edge is waited on unless the producer has an e.g. example."""
    deps: dict = {}
    for a, b in edges:
        deps.setdefault(b, []).append(a)
    out = ["# Generated plan", "", "**Goal:** generated.", "**Architecture:** modules.",
           "**Tech stack:** Python 3.12, pytest.", "**Target:** 20 min", "",
           "## Task graph", "", "```mermaid", "%% taskgraph", "flowchart LR"]
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


class Scratch:
    """A scenario repo in a directory this object owns (hypothesis examples cannot use function fixtures)."""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sl-prop-"))
        self.repo = scenario.Repo(self.tmp)

    def __enter__(self):
        return self.repo

    def __exit__(self, *exc):
        shutil.rmtree(self.tmp, ignore_errors=True)


def plan_cmd(repo, sub, *args, **kw):
    """`plan <sub>` where the plan area exists, else the older plan_tools name (the launcher answers exit 2)."""
    r = repo.cli("plan", sub, *args, **kw)
    return r if r.code != 2 or "unknown tool" not in r.err else repo.cli("plan_tools", sub, *args, **kw)


# ---------------------------------------------------------------- scheduler properties

@st.composite
def runs(draw):
    n = draw(st.integers(2, 7))
    ids = [f"T{i}" for i in range(1, n + 1)]
    tasks = {t: {"mech": draw(st.booleans()), "eg": draw(st.booleans()),
                 "minutes": draw(st.sampled_from([1.0, 1.5, 2.0, 3.0, 4.0]))} for t in ids}
    edges = [(a, b) for i, a in enumerate(ids) for b in ids[i + 1:] if draw(st.integers(0, 3)) == 0]
    done = draw(st.integers(0, n - 1))  # T1..Tdone are checkpointed: closed under every edge, which only runs forward
    agents = []
    for k in range(draw(st.integers(0, 5))):
        agents.append({"id": f"a{k}", "tier": draw(st.sampled_from(["haiku", "sonnet"])), "tasks": [],
                       "context_k": draw(st.integers(0, 60)), "idle_min": draw(st.integers(0, 10))})
    return tasks, edges, ids[:done], agents, draw(st.integers(1, 6)), draw(st.randoms(use_true_random=False))


LINE = re.compile(r"^(?P<tasks>T\d+[a-z]*(?:,T\d+[a-z]*)*) (?:agent=(?P<agent>\S+)|poll (?P<poll>\S+)|fresh) tier=(?P<tier>\w+)")


@given(runs())
@PROP
def test_dispatch_never_breaks_the_scheduling_rules(run):
    tasks, edges, committed, agents, cap, rnd = run
    with Scratch() as repo:
        plan = repo.write("docs/plans/2026-11-01-gen.md", make_plan(tasks, edges))
        waited = planfile.read(str(plan)).waited_edges()
        assert plan_cmd(repo, "lint", "--plan", plan, "--stable").code == 0
        stable = repo.state("stable.json")
        # some agents are mid-task on a ready, not yet checkpointed task
        ready = [t for t in tasks if t not in committed and all(p in committed for p, c in waited if c == t)]
        running = rnd.sample(ready, rnd.randint(0, min(len(ready), len(agents)))) if ready else []
        for agent, t in zip(agents, running):
            agent["current"] = t
        busy = {a["id"] for a in agents if a.get("current")}
        repo.write(".superlite/pool.json", json.dumps({"agents": agents}))
        repo.write(".superlite/progress.md", "# Progress ledger\n\n" + "".join(f"- checkpoint {t} {'a' * 40} {t}\n" for t in committed))
        r = repo.cli("dispatch", "--plan", plan, "--cap", cap, "--batch", 1)
        assert r.code == 0, r.err
        offered, used_agents = [], []
        for ln in r.out.splitlines():
            m = LINE.match(ln)
            assert m or not ln.strip() or ln.startswith("T0 wave0"), f"unrecognised dispatch line {ln!r}"
            if not m or ln.startswith("T0 "):
                continue
            ids = m["tasks"].split(",")
            offered += ids
            by_id = {a["id"]: a for a in agents}
            named = [m["agent"]] if m["agent"] else (m["poll"].split(",") if m["poll"] else [])
            for aid in named:
                used_agents.append(aid)
                assert aid not in busy, f"offered busy agent {aid}: {ln}"
                assert by_id[aid]["tier"] == m["tier"], f"crossed tiers: {ln}"
            for t in ids:
                assert stable["tiers"][t] == m["tier"], f"{t} routed to {m['tier']}, lint said {stable['tiers'][t]}"
                if m["tier"] == "haiku":
                    assert t not in stable["critical_path"], f"fast tier on the critical path: {ln}"
        assert len(set(offered)) == len(offered), f"a task offered twice: {offered}"
        assert len(set(used_agents)) == len(used_agents), f"an agent on two lines: {used_agents}"
        for t in offered:
            assert t not in committed and t not in running
            assert all(p in committed for p, c in waited if c == t), f"{t} offered before its waited inputs: {waited}"
        assert len(offered) <= max(0, cap - len(running)), f"--cap {cap} with {len(running)} running offered {offered}"


# ---------------------------------------------------------------- checkpoint properties

def snapshot(repo) -> tuple:
    """HEAD, branch, index and every working file: what a checkpoint must leave alone."""
    files = {p.relative_to(repo.root).as_posix(): p.read_bytes() for p in sorted(repo.root.rglob("*"))
             if p.is_file() and ".git" not in p.relative_to(repo.root).parts and ".superlite" not in p.relative_to(repo.root).parts}
    return repo.git("rev-parse", "HEAD"), repo.branch(), repo.git("ls-files", "-s"), repo.git("status", "--porcelain=v1", "-uall"), files


@st.composite
def edits(draw):
    n = draw(st.integers(2, 5))
    ids = [f"T{i}" for i in range(1, n + 1)]
    written = {t: draw(st.sampled_from(["both", "src", "test"])) for t in ids if draw(st.booleans()) or t == ids[0]}
    order = draw(st.permutations(sorted(written)))
    k = draw(st.integers(1, len(order)))
    return ids, written, list(order[:k]), draw(st.booleans())


@given(edits())
@PROP
def test_checkpoint_records_exactly_the_tasks_own_files(case):
    ids, written, picked, batched = case
    with Scratch() as repo:
        plan = repo.write("docs/plans/2026-11-01-gen.md", make_plan({t: {} for t in ids}))
        for t, which in written.items():
            fake_agent.do(t, plan, repo)
            n = t[1:]
            if which == "src":
                (repo.root / f"tests/test_m{n}.py").unlink()
            elif which == "test":
                (repo.root / f"src/m{n}.py").unlink()
        before = snapshot(repo)
        groups = [picked] if batched else [[t] for t in picked]
        shas = {}
        for g in groups:
            r = repo.cli("checkpoint", "--plan", plan, *g)
            assert r.code == 0, r.err
            if len(g) == 1:
                shas[g[0]] = r.out.split()[-1]
            else:
                shas.update({m["id"]: m["sha"] for m in scenario.FROZEN["checkpoint"].finditer(r.out)})
        assert set(shas) == set(picked)
        for t, sha in shas.items():
            n = t[1:]
            want = {"both": [f"src/m{n}.py", f"tests/test_m{n}.py"], "src": [f"src/m{n}.py"], "test": [f"tests/test_m{n}.py"]}[written[t]]
            got = repo.git("diff-tree", "-r", "--no-commit-id", "--name-only", f"{sha}^", sha).split()
            assert sorted(got) == sorted(want), f"{t} recorded {got}, owns {want}"
        assert snapshot(repo) == before, "a checkpoint moved HEAD, the branch, the index or a working file"


# ---------------------------------------------------------------- determinism

def run_tool(repo, args, cwd=None, shuffle: int | None = None) -> tuple:
    env = repo.env()
    if shuffle is not None:  # same variables, another order
        items = list(env.items())
        random.Random(shuffle).shuffle(items)
        env = dict(items)
    cmd = [scenario.BASH, str(scenario.LAUNCHER), *map(str, args)]
    p = subprocess.run(cmd, cwd=cwd or repo.root, env=env, capture_output=True)
    return p.returncode, p.stdout


@pytest.fixture(scope="module")
def det_repo(tmp_path_factory):
    repo = scenario.Repo(tmp_path_factory.mktemp("det"))
    tasks = {"T1": {"minutes": 2.0}, "T2": {"minutes": 2.0}, "T3": {"minutes": 2.0}, "T4": {"mech": True, "minutes": 1.0},
             "T5": {"mech": True, "minutes": 1.0, "eg": True}}
    repo.write("docs/plans/2026-11-01-det.md", make_plan(tasks, [("T1", "T3"), ("T2", "T3"), ("T5", "T4")]))
    (repo.root / "docs" / "sub").mkdir()
    return repo


def reset_run_state(repo):
    """A tool run twice must not depend on what the first run left behind, except the files it is meant to leave."""
    shutil.rmtree(repo.root / ".superlite", ignore_errors=True)


SHUFFLES = st.integers(0, 10_000)


@given(SHUFFLES)
@SAME
def test_lint_stable_prints_the_same_bytes_every_time(det_repo, seed):
    plan = det_repo.root / "docs/plans/2026-11-01-det.md"
    args = ["plan", "lint", "--plan", plan, "--stable"]
    if det_repo.cli("plan", "lint", "--help").code == 2:
        args[0:2] = ["plan_tools", "lint"]
    reset_run_state(det_repo)
    first = run_tool(det_repo, args)
    reset_run_state(det_repo)
    again = run_tool(det_repo, args, shuffle=seed)
    reset_run_state(det_repo)
    elsewhere = run_tool(det_repo, args, cwd=det_repo.root / "docs" / "sub", shuffle=seed + 1)
    assert first[0] == again[0] == elsewhere[0] == 0
    assert first == again == elsewhere


@given(SHUFFLES)
@SAME
def test_dispatch_prints_the_same_bytes_every_time(det_repo, seed):
    plan = det_repo.root / "docs/plans/2026-11-01-det.md"
    lint = ["plan", "lint", "--plan", plan, "--stable"]
    if det_repo.cli("plan", "lint", "--help").code == 2:
        lint[0:2] = ["plan_tools", "lint"]
    reset_run_state(det_repo)
    assert run_tool(det_repo, lint)[0] == 0
    args = ["dispatch", "--plan", plan, "--cap", 4]
    first = run_tool(det_repo, args)
    again = run_tool(det_repo, args, shuffle=seed)
    elsewhere = run_tool(det_repo, args, cwd=det_repo.root / "docs" / "sub", shuffle=seed + 1)
    assert first[0] == again[0] == elsewhere[0] == 0
    assert first == again == elsewhere


@given(SHUFFLES)
@SAME
def test_build_prime_writes_the_same_bytes_every_time(det_repo, seed):
    plan = det_repo.root / "docs/plans/2026-11-01-det.md"
    lint = ["plan", "lint", "--plan", plan, "--stable"]
    if det_repo.cli("plan", "lint", "--help").code == 2:
        lint[0:2] = ["plan_tools", "lint"]
    reset_run_state(det_repo)
    assert run_tool(det_repo, lint)[0] == 0
    out = det_repo.root / ".superlite" / "prime.md"
    args = ["build_prime", "--plan", plan, "--out", out]
    seen = []
    for cwd, sh in ((None, None), (None, seed), (det_repo.root / "docs" / "sub", seed + 1)):
        out.unlink(missing_ok=True)
        code, stdout = run_tool(det_repo, args, cwd=cwd, shuffle=sh)
        assert code == 0
        seen.append((stdout, out.read_bytes()))
    assert seen[0] == seen[1] == seen[2]


WORDS = ["fix", "bug", "build", "new", "app", "rename", "review", "add", "feature", "typo", "mvp", "status", "login", "page"]


@given(st.lists(st.sampled_from(WORDS), min_size=1, max_size=6), st.sampled_from(["empty folder", "existing code", "run in progress"]),
       SHUFFLES)
@SAME
def test_suggest_flow_prints_the_same_line_every_time(det_repo, words, state, seed):
    args = ["suggest_flow", "--text", " ".join(words), "--state", state]
    first = run_tool(det_repo, args)
    again = run_tool(det_repo, args, shuffle=seed)
    elsewhere = run_tool(det_repo, args, cwd=det_repo.root / "docs" / "sub", shuffle=seed + 1)
    assert first[0] == again[0] == elsewhere[0] == 0 and first[1].startswith(b"suggest:")
    assert first == again == elsewhere


@given(SHUFFLES)
@SAME
def test_review_packet_is_the_same_bytes_every_time(det_repo, seed):
    plan = det_repo.root / "docs/plans/2026-11-01-det.md"
    packet = det_repo.root / ".superlite" / "review-packet.md"
    seen = []
    for cwd, sh in ((None, None), (None, seed), (det_repo.root / "docs" / "sub", seed + 1)):
        packet.unlink(missing_ok=True)
        args = ["plan", "packet", "--plan", plan, "--root", det_repo.root]
        if det_repo.cli("plan", "packet", "--help").code == 2:
            args[0:2] = ["plan_tools", "packet"]
        code, stdout = run_tool(det_repo, args, cwd=cwd, shuffle=sh)
        assert code == 0
        seen.append((stdout, packet.read_bytes()))
    assert seen[0] == seen[1] == seen[2]
