"""Who orchestrates: the human/AI gate on lint --stable, dispatch, wave0 and build_prime; finish line; bench prompts."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from test_plan_tools import SCRIPTS, run
from test_routing import mech_plan

ORCH = SCRIPTS / "orchestrator.py"
NO_REPLY = ("BLOCKED: no human approval reply since the review plan was written; show the plan, ask the human to reply approve, "
            "end your turn")
BLOCK = "BLOCKED: human orchestrator selected, show the plan, ask for approval, then run superlite-cli orchestrator approve"


def reply(tmp_path, review, approves=True, offset_ms=5000, text="approve"):
    """One line of .superlite/human-replies.jsonl as the UserPromptSubmit hook writes it (t relative to the review file's mtime)."""
    t = int(Path(review).stat().st_mtime * 1000) + offset_ms
    f = tmp_path / ".superlite" / "human-replies.jsonl"
    f.parent.mkdir(exist_ok=True)
    with f.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"t": t, "approves": approves, "text": text}) + "\n")


def approve_human(tmp_path, by="human", review=None, text="approve"):
    """A real approval: a review plan, a fresh human reply, then the coordinator sends approve."""
    f = review or review_file(tmp_path)
    reply(tmp_path, f, text=text)
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


def test_schedule_and_plain_lint_stay_allowed_in_human_mode(tmp_path):
    p = mech_plan(tmp_path, 2)
    before = p.read_bytes()
    orch(tmp_path, "set", "human")
    for args in (("schedule", "--plan", p), ("lint", "--plan", p)):
        cp = run(SCRIPTS / "plan_tools.py", *args, cwd=tmp_path)
        assert BLOCK not in cp.both and cp.returncode != 4, (args, cp.both)
    cp = run(SCRIPTS / "plan_tools.py", "schedule", "--plan", p, cwd=tmp_path)
    assert cp.returncode == 0 and cp.out.strip(), cp.both
    assert p.read_bytes() == before


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


# ---------------------------------------------------------------- review plan (human orchestrator, phase 1)

import hashlib  # noqa: E402

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


def test_approve_follows_the_model_when_a_human_has_answered(tmp_path):
    # The hook would not call this a stock phrase. The coordinator understood it as approval and sent approve.
    orch(tmp_path, "set", "human")
    f = review_file(tmp_path)
    reply(tmp_path, f, approves=False, text="looks good enough, rename the title")
    cp = orch(tmp_path, "approve", "--review", f)
    assert cp.returncode == 0, cp.both
    assert state(tmp_path)["approved"] is True
    assert state(tmp_path)["approved_reply"] == "looks good enough, rename the title"


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


def test_orchestrator_uses_the_run_of_a_parent_folder(tmp_path):
    # The hook records replies at the project root; superlite-cli run from a subfolder must read and write the same .superlite.
    assert "human" in orch(tmp_path, "set", "human").out
    sub = tmp_path / "movedocs" / "api"
    sub.mkdir(parents=True)
    f = review_file(tmp_path)
    reply(tmp_path, f)
    cp = run(ORCH, "approve", "--review", f, cwd=sub)
    assert cp.returncode == 0, cp.both
    assert not (sub / ".superlite").exists()
    assert json.loads((tmp_path / ".superlite" / "orchestrator.json").read_text())["approved"] is True
    assert run(ORCH, "set", "human", cwd=sub).returncode == 0
    assert json.loads((tmp_path / ".superlite" / "orchestrator.json").read_text())["approved"] is False
    assert not (sub / ".superlite").exists()


def test_set_in_a_folder_without_a_run_still_starts_one_there(tmp_path):
    assert run(ORCH, "set", "human", cwd=tmp_path).returncode == 0
    assert (tmp_path / ".superlite" / "orchestrator.json").exists()


SESSION = SCRIPTS / "session.py"


def git_repo(path):
    path.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@example.com"],
                 ["config", "user.name", "t"]):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)
    (path / ".gitignore").write_text(".superlite/\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True, capture_output=True)
    return path


def test_set_in_parallel_worktree_writes_its_own_state(tmp_path):
    """orchestrator set inside a parallel session worktree writes that worktree's state, not the main checkout's."""
    repo = git_repo(tmp_path.resolve() / "app")
    assert run(SESSION, "ensure", "--slug", "a", cwd=repo).returncode == 0
    cp = run(SESSION, "ensure", "--slug", "b", "--move", "yes", cwd=repo)
    assert cp.returncode == 0, cp.both
    wt = repo / ".superlite" / "worktrees" / "sessions" / "b"
    main_file = repo / ".superlite" / "orchestrator.json"
    main_file.write_text(json.dumps({"mode": "ai", "approved": True}), encoding="utf-8")
    main_bytes = main_file.read_bytes()
    assert not (wt / ".superlite" / "orchestrator.json").exists()
    cp = run(ORCH, "set", "human", cwd=wt)
    assert cp.returncode == 0, cp.both
    own = wt / ".superlite" / "orchestrator.json"
    assert own.is_file(), "set did not write the parallel worktree's own state"
    assert json.loads(own.read_text(encoding="utf-8"))["mode"] == "human"
    assert main_file.read_bytes() == main_bytes
    sub = wt / "sub" / "dir"
    sub.mkdir(parents=True)
    st = run(ORCH, "status", cwd=sub)
    assert st.returncode == 0, st.both
    assert "human" in st.out and "AI" not in st.out, st.out


def test_status_inside_git_dir_uses_the_checkout_root(tmp_path):
    """orchestrator status from inside .git of a repo with no .superlite does not create state there."""
    repo = git_repo(tmp_path.resolve() / "app")
    cp = run(ORCH, "status", cwd=repo / ".git")
    assert cp.returncode == 0, cp.both
    assert not (repo / ".git" / ".superlite").exists()


def test_approve_from_a_subfolder_records_an_absolute_review_path_the_reply_hook_reads(tmp_path):
    import os
    import time
    from test_human_gate import lines, reply as hook_reply
    orch(tmp_path, "set", "human")
    f = review_file(tmp_path)
    reply(tmp_path, f)
    cp = run(ORCH, "approve", "--review", "plans/plan.review.md", "--plan", tmp_path / "plan.md", cwd=tmp_path / "docs")
    assert cp.returncode == 0, cp.both
    st = json.loads((tmp_path / ".superlite" / "orchestrator.json").read_text(encoding="utf-8"))
    assert Path(st["review_path"]).is_absolute() and Path(st["review_path"]).samefile(f)
    t = time.time()
    os.utime(tmp_path / ".superlite" / "orchestrator.json", (t - 100, t - 100))
    os.utime(f, (t - 50, t - 50))  # the plan was amended after the approval
    (tmp_path / ".superlite" / "human-replies.jsonl").unlink()
    hook_reply(tmp_path / "docs", "approve")
    assert [e["text"] for e in lines(tmp_path)] == ["approve"]
