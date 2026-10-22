"""Who orchestrates: the human/AI gate on lint --stable, dispatch, wave0 and build_prime; finish line; bench prompts."""
from __future__ import annotations

import json
from pathlib import Path

from test_plan_tools import SCRIPTS, run
from test_routing import mech_plan

ORCH = SCRIPTS / "orchestrator.py"
REPO = SCRIPTS.parents[2]
LINE = "Orchestrator: AI, do not ask me to choose or to approve."
NO_REPLY = ("BLOCKED: no human approval reply since the review plan was written; show the plan, ask the human to reply approve, "
            "end your turn")
BLOCK = "BLOCKED: human orchestrator selected, show the plan, ask for approval, then run sl orchestrator approve"


def reply(tmp_path, review, approves=True, offset_ms=5000, text="approve"):
    """One line of .superlite/human-replies.jsonl as the UserPromptSubmit hook writes it (t relative to the review file's mtime)."""
    t = int(Path(review).stat().st_mtime * 1000) + offset_ms
    f = tmp_path / ".superlite" / "human-replies.jsonl"
    f.parent.mkdir(exist_ok=True)
    with f.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"t": t, "approves": approves, "text": text}) + "\n")


def approve_human(tmp_path, by="human", review=None):
    """A real approval: a review plan, a fresh approving human reply, then approve --review."""
    f = review or review_file(tmp_path)
    reply(tmp_path, f)
    return orch(tmp_path, "approve", "--by", by, "--review", f)


def orch(tmp_path, *args):
    return run(ORCH, *args, "--plan", tmp_path / "plan.md", cwd=tmp_path)


def gated(tmp_path, p):
    """The four gated tools, each as (name, process)."""
    sl = tmp_path / ".superlite"
    return {
        "lint --stable": run(SCRIPTS / "plan_tools.py", "lint", "--stable", "--plan", p, cwd=tmp_path),
        "dispatch": run(SCRIPTS / "dispatch.py", "--plan", p, "--pool", sl / "pool.json", "--ledger", sl / "progress.md", cwd=tmp_path),
        "wave0": run(SCRIPTS / "wave0.py", "--plan", p, cwd=tmp_path),
        "build_prime": run(SCRIPTS / "build_prime.py", "--plan", p, "--out", sl / "prime.md", cwd=tmp_path),
    }


def test_each_gated_tool_is_blocked_in_human_mode_and_runs_after_approve(tmp_path):
    p = mech_plan(tmp_path, 2)
    assert "human" in orch(tmp_path, "set", "human").out
    for name, cp in gated(tmp_path, p).items():
        assert cp.returncode == 4, (name, cp.both)
        assert BLOCK in cp.err and len(cp.err.strip().splitlines()) == 1, name
    assert not (tmp_path / ".superlite" / "stable.json").exists() and not (tmp_path / ".superlite" / "briefs").exists()
    ap = approve_human(tmp_path, "carol")
    assert ap.returncode == 0 and "human, approved" in ap.out and "carol" in ap.out
    for name, cp in gated(tmp_path, p).items():
        assert cp.returncode in (0, 1) and BLOCK not in cp.both, (name, cp.both)  # lint may exit 1 for FIX tasks, never 4
    assert (tmp_path / ".superlite" / "stable.json").exists()


def test_schedule_prune_and_plain_lint_stay_allowed_in_human_mode(tmp_path):
    p = mech_plan(tmp_path, 2)
    orch(tmp_path, "set", "human")
    for args in (("schedule", "--plan", p, "--insert"), ("prune", "--plan", p), ("lint", "--plan", p)):
        cp = run(SCRIPTS / "plan_tools.py", *args, cwd=tmp_path)
        assert BLOCK not in cp.both and cp.returncode != 4, (args, cp.both)
    assert "%% schedule" in p.read_text(encoding="utf-8")


def test_ai_mode_and_no_file_are_ungated(tmp_path):
    p = mech_plan(tmp_path, 2)
    for name, cp in gated(tmp_path, p).items():  # no orchestrator.json
        assert BLOCK not in cp.both, (name, cp.both)
    orch(tmp_path, "set", "ai")
    st = json.loads((tmp_path / ".superlite" / "orchestrator.json").read_text(encoding="utf-8"))
    assert st["mode"] == "ai" and st["approved"] is True and st["by"] == "user" and st["time"]
    for name, cp in gated(tmp_path, p).items():
        assert BLOCK not in cp.both, (name, cp.both)


def test_approve_needs_human_mode(tmp_path):
    cp = orch(tmp_path, "approve")
    assert cp.returncode == 2 and "human mode only" in cp.err
    orch(tmp_path, "set", "ai")
    assert orch(tmp_path, "approve").returncode == 2


def test_status_lines(tmp_path):
    assert "not set (ungated)" in orch(tmp_path, "status").out
    orch(tmp_path, "set", "human")
    assert "human, awaiting approval" in orch(tmp_path, "status").out
    approve_human(tmp_path)
    assert "human, approved" in orch(tmp_path, "status").out
    orch(tmp_path, "set", "ai")
    assert orch(tmp_path, "status").out.startswith("orchestrator: AI")


def test_set_ai_can_record_the_headless_default_as_a_ruling(tmp_path):
    orch(tmp_path, "set", "ai", "--by", "coordinator", "--ruling", "orchestrator defaulted to AI - headless run, no human can answer - cost if wrong: none")
    text = (tmp_path / ".superlite" / "progress.md").read_text(encoding="utf-8")
    assert text.strip() == "Ruling: orchestrator defaulted to AI - headless run, no human can answer - cost if wrong: none"


def finish(tmp_path):
    sl = tmp_path / ".superlite"
    sl.mkdir(exist_ok=True)
    (sl / "progress.md").write_text("# Progress ledger\n", encoding="utf-8")
    return run(SCRIPTS / "finish.py", "--ledger", sl / "progress.md", "--log", sl / "i.jsonl", cwd=tmp_path).out


def test_finish_reports_the_mode_and_the_approval_time(tmp_path):
    assert "orchestrator: not set (ungated)" in finish(tmp_path)
    orch(tmp_path, "set", "human")
    assert "orchestrator: human, awaiting approval" in finish(tmp_path)
    approve_human(tmp_path, "dana")
    out = finish(tmp_path)
    st = json.loads((tmp_path / ".superlite" / "orchestrator.json").read_text(encoding="utf-8"))
    assert f"orchestrator: human, approved {st['approved_time']} by dana" in out
    orch(tmp_path, "set", "ai")
    assert "orchestrator: AI" in finish(tmp_path)


def bench_prompts():
    b = REPO / "bench"
    return sorted([*b.glob("prompt*.md"), *b.glob("tasks/*/prompt*.md")])


def test_every_bench_prompt_says_the_orchestrator_is_ai():
    files = bench_prompts()
    assert len(files) >= 10
    missing = [str(f.relative_to(REPO)) for f in files if LINE not in f.read_text(encoding="utf-8")]
    assert not missing, missing


def test_skill_size_caps_and_the_new_names():
    skills = REPO / "skills"
    assert len((skills / "using-superlite" / "SKILL.md").read_text(encoding="utf-8")) <= 2000
    assert len((skills / "subagent-driven-development" / "SKILL.md").read_text(encoding="utf-8")) <= 12500
    brain = (skills / "brainstorming" / "SKILL.md").read_text(encoding="utf-8")
    assert "Human orchestrator: I approve the plan before it runs" in brain and "AI orchestrator: hands off, it does everything" in brain
    assert brain.count("AskUserQuestion") >= 1 and "ONE `AskUserQuestion` call" in brain
    plans = (skills / "writing-plans" / "SKILL.md").read_text(encoding="utf-8")
    assert "**Human orchestrator**" in plans and "**AI orchestrator**" in plans and "A human approves" not in plans


# ---------------------------------------------------------------- review plan (human orchestrator, phase 1)

import hashlib  # noqa: E402

REVIEW = REPO / "skills" / "writing-plans" / "review-plan.md"


def review_file(tmp_path, name="plan.review.md"):
    f = tmp_path / "docs" / "plans" / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("# Review plan\n\n## Summary\nA word counter.\n", encoding="utf-8")
    return f


def test_approve_review_records_path_and_sha256(tmp_path):
    orch(tmp_path, "set", "human")
    f = review_file(tmp_path)
    reply(tmp_path, f)
    cp = orch(tmp_path, "approve", "--by", "erin", "--review", f)
    assert cp.returncode == 0, cp.both
    st = json.loads((tmp_path / ".superlite" / "orchestrator.json").read_text(encoding="utf-8"))
    assert st["review_path"] == str(f).replace("\\", "/") and st["review_sha256"] == hashlib.sha256(f.read_bytes()).hexdigest()
    assert st["approved"] is True and st["approved_by"] == "erin"
    assert f"review plan {st['review_path']}" in cp.out


def test_approve_review_with_a_missing_file_fails_and_does_not_approve(tmp_path):
    orch(tmp_path, "set", "human")
    cp = orch(tmp_path, "approve", "--review", tmp_path / "nope.review.md")
    assert cp.returncode == 2 and "review plan not found" in cp.err
    st = json.loads((tmp_path / ".superlite" / "orchestrator.json").read_text(encoding="utf-8"))
    assert st["approved"] is False and "review_path" not in st
    assert "awaiting approval" in orch(tmp_path, "status").out


def state(tmp_path):
    return json.loads((tmp_path / ".superlite" / "orchestrator.json").read_text(encoding="utf-8"))


def test_approve_refuses_without_review(tmp_path):
    orch(tmp_path, "set", "human")
    f = review_file(tmp_path)
    reply(tmp_path, f)  # even with a fresh approving reply
    cp = orch(tmp_path, "approve", "--by", "frank")
    assert cp.returncode == 2 and "write the review plan first" in cp.err
    assert state(tmp_path)["approved"] is False


def test_approve_refuses_without_any_human_reply(tmp_path):
    orch(tmp_path, "set", "human")
    f = review_file(tmp_path)
    cp = orch(tmp_path, "approve", "--review", f)
    assert cp.returncode == 3 and cp.err.strip() == NO_REPLY
    assert state(tmp_path)["approved"] is False and "review_path" not in state(tmp_path)


def test_approve_refuses_a_stale_reply_older_than_the_review_plan(tmp_path):
    orch(tmp_path, "set", "human")
    f = review_file(tmp_path)
    reply(tmp_path, f, offset_ms=-60_000)  # the human said approve before the plan was (re)written
    cp = orch(tmp_path, "approve", "--review", f)
    assert cp.returncode == 3 and "no human approval reply since the review plan was written" in cp.err
    assert "no qualifying human reply yet" in orch(tmp_path, "status", "--review", f).out


def test_approve_refuses_a_non_approving_reply(tmp_path):
    orch(tmp_path, "set", "human")
    f = review_file(tmp_path)
    reply(tmp_path, f, approves=False, text="change the storage to a database")
    cp = orch(tmp_path, "approve", "--review", f)
    assert cp.returncode == 3 and state(tmp_path)["approved"] is False


def test_approve_accepts_a_fresh_approving_reply_after_a_changing_one(tmp_path):
    orch(tmp_path, "set", "human")
    f = review_file(tmp_path)
    reply(tmp_path, f, approves=False, offset_ms=1000, text="use files")
    reply(tmp_path, f, approves=True, offset_ms=9000, text="approve")
    cp = orch(tmp_path, "approve", "--by", "gina", "--review", f)
    assert cp.returncode == 0, cp.both
    assert state(tmp_path)["approved"] is True and state(tmp_path)["approved_by"] == "gina"


def test_status_says_whether_an_approving_reply_exists(tmp_path):
    orch(tmp_path, "set", "human")
    assert "no approving human reply yet" in orch(tmp_path, "status").out
    f = review_file(tmp_path)
    assert "no qualifying human reply yet" in orch(tmp_path, "status", "--review", f).out
    reply(tmp_path, f)
    out = orch(tmp_path, "status", "--review", f).out
    assert "awaiting approval" in out and "approving reply at" in out


def test_ai_mode_needs_no_reply_and_no_approve(tmp_path):
    p = mech_plan(tmp_path, 2)
    orch(tmp_path, "set", "ai")
    for name, cp in gated(tmp_path, p).items():
        assert BLOCK not in cp.both, (name, cp.both)
    assert orch(tmp_path, "approve").returncode == 2  # approve is for human mode only


def test_status_and_finish_show_the_review_plan_path(tmp_path):
    orch(tmp_path, "set", "human")
    f = review_file(tmp_path)
    reply(tmp_path, f)
    orch(tmp_path, "approve", "--review", f)
    path = str(f).replace("\\", "/")
    assert f"review plan {path}" in orch(tmp_path, "status").out
    assert f"review plan {path}" in finish(tmp_path)
    p = mech_plan(tmp_path, 2)
    assert BLOCK not in run(SCRIPTS / "plan_tools.py", "lint", "--stable", "--plan", p, cwd=tmp_path).both  # the gate opened


REQUIRED_SECTIONS = ["## Summary", "## What you will get", "## What will be built", "## How it fits together", "## How it gets built",
                     "## Decisions you can change", "## Risks", "## Out of scope", "## How we will know it works", "## Rough time and cost"]


def test_review_plan_template_names_every_required_section_and_has_two_diagrams():
    text = REVIEW.read_text(encoding="utf-8")
    for h in REQUIRED_SECTIONS:
        assert h in text, h
    assert text.count("```mermaid") >= 2  # the example carries both diagrams
    for word in ("Size (S/M/L)", "at most 12 nodes", "rough until", "Do not", "Do\n", "five-line summary", "sha256", "**Review plan:**"):
        assert word in text, word


def test_skills_mention_both_phases_and_keep_their_caps():
    skills = REPO / "skills"
    plans = (skills / "writing-plans" / "SKILL.md").read_text(encoding="utf-8")
    brain = (skills / "brainstorming" / "SKILL.md").read_text(encoding="utf-8")
    sdd = (skills / "subagent-driven-development" / "SKILL.md").read_text(encoding="utf-8")
    for text in (plans, brain):
        assert ".review.md" in text and "review-plan.md" in text and "approve --review" in text
    assert "**Review plan:**" in plans and "machine plan" in plans and "machine plan" in brain
    assert "approve --review PATH" in sdd and "review plan" in sdd
    assert len((skills / "using-superlite" / "SKILL.md").read_text(encoding="utf-8")) <= 2000 and len(sdd) <= 12500
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "review plan" in readme.lower() and "You approve first" in readme


def test_texts_state_the_hard_rule_plain_text_approval_and_the_small_form():
    skills = REPO / "skills"
    plans = (skills / "writing-plans" / "SKILL.md").read_text(encoding="utf-8")
    brain = (skills / "brainstorming" / "SKILL.md").read_text(encoding="utf-8")
    sdd = (skills / "subagent-driven-development" / "SKILL.md").read_text(encoding="utf-8")
    ref = (skills / "subagent-driven-development" / "orchestrator.md").read_text(encoding="utf-8")
    review = REVIEW.read_text(encoding="utf-8")
    for text in (plans, brain):
        assert "not AskUserQuestion" in text and "END THE TURN" in text
        assert "Reply approve to start, or tell me what to change" in text
    assert "Nothing is built, dispatched or written except this file" in plans and "small requests too" not in plans
    assert "yes in chat" in brain and "yes in chat" in (skills / "using-superlite" / "SKILL.md").read_text(encoding="utf-8")
    assert "Ruling: [scope]" in plans and "Changed from the approved plan" in plans and "goes back to the human" not in plans
    assert "before the human's approving reply" in sdd and "plain text" in sdd
    assert "human-replies.jsonl" in ref and "exit 3" in ref and "The hard rule (human mode)" in ref
    assert "One-page form" not in review and "Reply approve to start" in review and "Ruling: [scope]" in review
    assert "Small inline work" in ref and "Changed from the approved" in ref and "coord-guard" not in ref
    assert "the orchestrator-choice" not in brain  # the choice itself stays an AskUserQuestion batch
    assert "ONE `AskUserQuestion` call" in brain and "Human orchestrator: I approve the plan before it runs" in brain


def test_readme_has_the_human_mode_sentence():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "nothing is linted, dispatched or sent to an agent until you reply approve" in readme and "you are not asked again" in readme
