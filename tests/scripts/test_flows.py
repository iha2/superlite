"""Baseline of today's flows: each mode's tool-call sequence run through the repo's launcher in a temp git repo.

Asserts exit codes, state files and git state, never wording, except the frozen lines (see scenario.FROZEN).
The same file must pass unchanged after the restructure.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import fake_agent
import scenario

FIXTURE = Path(__file__).parent / "fixtures" / "flow_plan.md"
MVP_SOURCE = Path(__file__).parent / "test_mvp_check.py"  # holds a sample MVP plan and mock
IDS = ["T1", "T2", "T3", "T4"]


@pytest.fixture
def repo(tmp_path):
    r = scenario.Repo(tmp_path)
    assert r.root.is_relative_to(tmp_path.resolve())  # the temp repo never overlaps the real one
    assert not r.root.is_relative_to(scenario.ROOT)
    return r


def ensure(repo, slug):
    r = repo.cli("session", "ensure", "--slug", slug)
    assert r.code == 0, r.err
    m = scenario.frozen("session", r.out)
    assert m and m["slug"] == slug and Path(m["path"]).resolve() == repo.root
    assert repo.branch() != "main"
    assert repo.session_row(slug)["run"] == "open"


def plan_in_repo(repo, name="docs/plans/2026-11-01-flow.md") -> Path:
    return repo.write(name, FIXTURE.read_text(encoding="utf-8"))


def build_plan(repo, plan: Path) -> None:
    """lint --stable, dispatch, fake agents, checkpoint every task."""
    r = repo.cli("plan_tools", "lint", "--plan", plan, "--stable")
    assert r.code == 0, r.err
    assert scenario.frozen("stable", r.out)
    assert repo.state("stable.json") is not None
    assert repo.cli("dispatch", "--plan", plan).code == 0
    for tid in IDS:
        fake_agent.do(tid, plan, repo)
    r = repo.cli("checkpoint", "--plan", plan, *IDS)
    assert r.code == 0, r.err
    assert [m["id"] for m in scenario.FROZEN["checkpoint"].finditer(r.out)] == IDS
    assert repo.state("progress.md").count("- checkpoint T") == len(IDS)


def test_flow_regular(repo):
    ensure(repo, "regular")
    assert repo.cli("orchestrator", "set", "ai").code == 0
    assert repo.state("orchestrator.json")["mode"] == "ai"
    plan = plan_in_repo(repo)
    build_plan(repo, plan)
    scenario.end_flow(repo, "regular", plan=str(plan))
    repo.assert_closed_clean("regular")


def test_flow_small(repo):
    ensure(repo, "small")
    repo.write("src/hello.py", "print('hi')\n")
    scenario.end_flow(repo, "small")
    repo.assert_closed_clean("small")
    assert (repo.root / "src" / "hello.py").is_file()  # uncommitted work stays in the folder


def test_flow_debug(repo):
    ensure(repo, "debug")
    repo.write("tests/test_regress.py", "def test_regress():\n    assert True\n")
    repo.write("src/fix.py", "def fixed():\n    return True\n")
    scenario.end_flow(repo, "debug")
    repo.assert_closed_clean("debug")
    assert (repo.root / "src" / "fix.py").is_file() and (repo.root / "tests" / "test_regress.py").is_file()


def test_flow_research(repo):
    ensure(repo, "research")
    repo.write("docs/research/notes.md", "# Notes\n\nfindings\n")
    scenario.end_flow(repo, "research")
    repo.assert_closed_clean("research")
    assert (repo.root / "docs" / "research" / "notes.md").is_file()


def test_flow_review(repo):
    repo.git("checkout", "-q", "-b", "feature-x")
    repo.write("x.txt", "x\n")
    repo.git("add", "-A")
    repo.git("commit", "-q", "-m", "feature work")
    repo.git("checkout", "-q", "main")
    feature_tip = repo.log("feature-x")[0]
    r = repo.cli("session", "ensure", "--slug", "review", "--branch", "feature-x")
    assert r.code == 0, r.err
    assert scenario.frozen("session", r.out)["branch"] == "feature-x"
    assert repo.branch() == "feature-x"
    scenario.end_flow(repo, "review")
    repo.assert_closed_clean("review")
    assert repo.log("feature-x")[0] == feature_tip


def test_flow_feature(repo):
    ensure(repo, "feat")
    assert repo.cli("orchestrator", "set", "ai").code == 0
    repo.write("docs/plans/2026-11-01-feat.feature.md",
               "# Feat\n\n**Goal:** a feature.\n\n## Acceptance checks\n- it works\n")
    r = repo.cli("proto", "start", "--topic", "feat", "--run", "echo hi")
    assert r.code == 0, r.err
    sandbox = repo.root / ".claude" / "worktrees" / "feat-proto"
    assert sandbox.is_dir() and "superlite/feat-proto" in repo.git("branch", "--list")
    assert repo.cli("proto", "tryout").code == 0
    assert repo.state("feature.json")["status"] == "tryout"
    r = repo.cli("proto", "tryout", "--approve", "--ruling", "[acceptance] ok - fine - none")
    assert r.code == 0, r.err
    assert repo.state("feature.json")["status"] == "approved"
    assert repo.cli("proto", "discard").code == 0
    assert not sandbox.exists()
    (repo.root / "docs/plans/2026-11-01-feat.feature.md").unlink()  # checkpoint rejects other untracked files as out-of-scope
    plan = plan_in_repo(repo, "docs/plans/2026-11-01-feat.integration.md")
    build_plan(repo, plan)
    scenario.end_flow(repo, "feat", plan=str(plan))
    repo.assert_closed_clean("feat")


def test_flow_mvp(repo):
    src = MVP_SOURCE.read_text(encoding="utf-8")
    ensure(repo, "mvp")
    assert repo.cli("orchestrator", "set", "ai").code == 0
    rel = "docs/plans/2026-11-01-notes.mvp.md"
    repo.write(rel, re.search(r'^PLAN = """(.*?)"""', src, re.S | re.M).group(1))
    repo.write(".superlite/mvp/mock.html", re.search(r'^MOCK = """(.*?)"""', src, re.S | re.M).group(1))
    assert repo.cli("mvp_check", "build", "--plan", rel, "--mock", ".superlite/mvp/mock.html").code == 0
    assert (repo.root / ".superlite" / "mvp" / "prototype.html").is_file()
    assert repo.cli("mvp_check", "check", "--plan", rel).code == 0
    assert repo.cli("mvp_check", "ai", "--plan", rel).code == 0
    assert repo.state("mvp/mvp-feedback.json") is not None
    assert repo.cli("mvp_check", "check", "--plan", rel, "--require-feedback").code == 0
    (repo.root / rel).unlink()  # checkpoint rejects other untracked files as out-of-scope
    plan = plan_in_repo(repo)
    build_plan(repo, plan)
    scenario.end_flow(repo, "mvp", plan=str(plan))
    repo.assert_closed_clean("mvp")


def test_human_mode_refuses_dispatch_until_approved(repo):
    ensure(repo, "human")
    plan = plan_in_repo(repo)
    review = repo.write("docs/plans/2026-11-01-flow.review.md", "# Review plan\n\nBuild four small modules.\n")
    assert repo.cli("orchestrator", "set", "human").code == 0
    assert repo.cli("dispatch", "--plan", plan).code == 4
    assert repo.state("stable.json") is None
    assert repo.cli("orchestrator", "approve", "--review", review).code != 0  # no recorded human reply yet
    repo.human_reply(review)
    r = repo.cli("orchestrator", "approve", "--review", review)
    assert r.code == 0, r.err
    assert repo.state("orchestrator.json")["approved"] is True
    assert repo.cli("plan_tools", "lint", "--plan", plan, "--stable").code == 0
    assert repo.state("stable.json") is not None
    assert repo.cli("dispatch", "--plan", plan).code == 0
    scenario.end_flow(repo, "human")
    repo.assert_closed_clean("human")


def test_ask_move_and_ask_untracked_change_nothing(repo):
    ensure(repo, "first")
    repo.write("u.txt", "untracked\n")
    r = repo.cli("session", "ensure", "--slug", "second")
    assert r.code == 4 and scenario.frozen("ask_move", r.out)["slug"] == "first"
    assert repo.branch() == "superlite/sessions/first" and repo.session_row("second") is None
    r = repo.cli("session", "ensure", "--slug", "second", "--move", "yes")
    assert r.code == 4 and scenario.frozen("ask_untracked", r.out)["n"] == "1"
    assert repo.branch() == "superlite/sessions/first" and repo.session_row("second") is None
    r = repo.cli("session", "ensure", "--slug", "second", "--move", "yes", "--untracked", "keep")
    assert r.code == 0, r.err
    assert repo.session_row("second")["run"] == "open"
    assert (repo.root / "u.txt").is_file()


def _ready(dispatch_out: str, done: list) -> list:
    """Task ids a dispatch output names (`T2,T4 fresh tier=...`), minus the wave-0 skeleton T0 and finished ones."""
    ids = []
    for line in dispatch_out.splitlines():
        head = line.split()[0] if line.split() else ""
        ids += [t for t in head.split(",") if re.fullmatch(r"T[1-9]\d*", t)]
    return [t for t in ids if t not in done]


def test_fake_swarm_run(repo):
    ensure(repo, "swarm")
    assert repo.cli("orchestrator", "set", "ai").code == 0
    plan = plan_in_repo(repo)
    lint = repo.cli("plan_tools", "lint", "--plan", plan, "--stable")
    assert lint.code == 0 and scenario.frozen("stable", lint.out)["ids"].split() == IDS
    done: list = []
    for _ in range(6):  # one edge: at most three waves
        d = repo.cli("dispatch", "--plan", plan)
        assert d.code == 0, d.err
        ready = _ready(d.out, done)
        if not ready:
            break
        for tid in ready:
            fake_agent.do(tid, plan, repo)
        r = repo.cli("checkpoint", "--plan", plan, "--dispatch", *ready)
        assert r.code == 0, r.err
        done += ready
    assert sorted(done) == IDS
    p = repo.cli("plan_tools", "packet", "--plan", plan)
    assert p.code == 0, p.err
    packet_names = [f.name for f in (repo.root / ".superlite").iterdir()]
    ledger = repo.state("progress.md")  # session close parks .superlite, so read it before
    scenario.end_flow(repo, "swarm", plan=str(plan))
    for tid in IDS:
        assert ledger.count(f"- checkpoint {tid} ") == 1, tid
    assert "review-packet.md" in packet_names
    repo.assert_closed_clean("swarm")
