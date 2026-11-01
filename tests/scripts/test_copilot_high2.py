"""Red-phase tests for the second round of Copilot HIGH findings on PR #25 (stale base, merge checkout, quoted cwd, review target)."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "swarm" / "scripts"
SESSION = SCRIPTS / "session.py"
HOOKS = ROOT / "hooks"
BASH = shutil.which("bash")

sys.path.insert(0, str(SCRIPTS))

import session  # noqa: E402


def run(*args, cwd=None):
    e = dict(os.environ, PYTHONIOENCODING="utf-8")
    e.pop("SUPERLITE_NO_AUTO_SESSION", None)
    e.pop("CLAUDE_PLUGIN_ROOT", None)
    e.pop("CLAUDE_CODE", None)
    cp = subprocess.run([sys.executable, str(SESSION), *map(str, args)], capture_output=True, cwd=cwd, env=e, timeout=120)
    cp.out, cp.err = cp.stdout.decode("utf-8"), cp.stderr.decode("utf-8")
    cp.both = cp.out + cp.err
    return cp


def git(cwd, *args, check=True):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=check)


def out(cwd, *args):
    return git(cwd, *args).stdout


@pytest.fixture
def repo(tmp_path):
    r = tmp_path.resolve() / "app"
    r.mkdir()
    out(r, "init", "-q", "-b", "main")
    out(r, "config", "user.email", "t@example.com")
    out(r, "config", "user.name", "t")
    (r / ".gitignore").write_text(".superlite/\n")
    (r / "app.py").write_text("one\ntwo\nthree\n")
    out(r, "add", "-A")
    out(r, "commit", "-q", "-m", "init")
    return r


def reg_of(r):
    return json.loads((r / ".superlite" / "sessions.json").read_text(encoding="utf-8"))


def row_of(r, slug):
    return next(s for s in reg_of(r)["sessions"] if s["slug"] == slug)


def branch(cwd):
    return out(cwd, "branch", "--show-current").strip()


def commit_file(cwd, name, text, msg):
    (Path(cwd) / name).write_text(text)
    out(cwd, "add", name)
    out(cwd, "commit", "-q", "-m", msg)
    return out(cwd, "rev-parse", "HEAD").strip()


def is_ancestor(repo, sha, ref):
    return git(repo, "merge-base", "--is-ancestor", sha, ref, check=False).returncode == 0


# ---- A: stale base after close --------------------------------------------------------------

def test_a_base_follows_main_checkout_branch_after_close(repo):
    """Copilot 4432404950: after close, a new ensure re-reads the main checkout's branch as base, not the stale registry base."""
    assert run("ensure", "--slug", "a", cwd=repo).returncode == 0
    cp = run("close", "--slug", "a", "--keep-branch", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert branch(repo) == "main"
    out(repo, "switch", "-q", "-c", "feature")
    feat = commit_file(repo, "feat.txt", "feature\n", "feature work")
    cp = run("ensure", "--slug", "b", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert is_ancestor(repo, feat, "superlite/sessions/b"), "b was cut from the stale base, not from feature"
    assert reg_of(repo)["base"] == "feature"


def test_a2_finished_active_parked_new_session_keeps_recorded_base(repo):
    """Copilot 4432404950 (unchanged path): a finished ACTIVE session is parked and the new one still uses the recorded base."""
    assert run("ensure", "--slug", "a", cwd=repo).returncode == 0
    a_commit = commit_file(repo, "a.txt", "a\n", "a work")
    (repo / ".superlite" / "orchestrator.json").write_text('{"mode":"ai"}\n')
    assert session.mark_run(repo, "finished") == "a"
    cp = run("ensure", "--slug", "b", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert branch(repo) == "superlite/sessions/b"
    assert reg_of(repo)["base"] == "main"
    assert not is_ancestor(repo, a_commit, "superlite/sessions/b")
    assert row_of(repo, "a")["status"] == "parked"


# ---- B: merge locally with a busy main checkout ---------------------------------------------

def test_b_shipping_merge_uses_temporary_checkout_when_main_hosts_other_session():
    """Copilot 4432404913: Merge locally must not switch a main checkout that hosts another open session."""
    text = (ROOT / "skills" / "shipping" / "SKILL.md").read_text(encoding="utf-8")
    m = re.search(r"\*\*Merge locally:\*\*(.*?)(?=\n- \*\*|\n## )", text, re.S)
    assert m, "Merge locally step not found"
    step = m.group(1)
    assert "temporary" in step, step
    assert "git worktree add" in step, step
    assert ".superlite/worktrees/" in step, step
    assert "session status" in step, step


# ---- C: JSON-escaped quotes in cwd -----------------------------------------------------------

HUMAN_UNAPPROVED = {"mode": "human", "approved": False}
AGENT = {"tool_name": "Agent", "tool_input": {"subagent_type": "superlite:implementer", "description": "T1",
                                              "prompt": "Build T1"}}


@pytest.fixture(params=["plain", 'q"x'], ids=["plain", "quoted"])
def ctree(tmp_path, request):
    hub = tmp_path / "hub"
    wt = hub / ".superlite" / "worktrees" / "sessions" / request.param
    (wt / ".superlite").mkdir(parents=True)
    (hub / ".superlite").mkdir(exist_ok=True)
    (hub / ".superlite" / "orchestrator.json").write_text(json.dumps({"mode": "ai", "approved": True}))
    (hub / ".superlite" / "stable.json").write_text("{}")
    (wt / ".superlite" / "orchestrator.json").write_text(json.dumps(HUMAN_UNAPPROVED))
    return hub, wt


def hook(name, hub, payload):
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(hub)}
    return subprocess.run([BASH, str(HOOKS / name)], input=json.dumps(payload), capture_output=True, text=True,
                          env=env, cwd=str(hub))


def test_c_guard_resolves_run_from_cwd_with_escaped_quote(ctree):
    """Copilot 4432404898: block-coordinator-edits must find the worktree run when cwd holds a JSON-escaped quote."""
    hub, wt = ctree
    r = hook("block-coordinator-edits", hub, {**AGENT, "cwd": str(wt)})
    assert r.returncode == 2, (wt.name, r.stderr)


def test_c_record_reply_lands_in_worktree_with_escaped_quote(ctree):
    """Copilot 4432404898: record-human-reply must write into the worktree whose path holds a quote, not the hub."""
    hub, wt = ctree
    r = hook("record-human-reply", hub, {"cwd": str(wt), "prompt": "hello there"})
    assert r.returncode == 0
    assert (wt / ".superlite" / "human-replies.jsonl").exists(), wt.name
    assert not (hub / ".superlite" / "human-replies.jsonl").exists()


# ---- D: review target (--branch) -------------------------------------------------------------

def test_d1_ensure_branch_registers_current_branch_in_place(repo):
    """Copilot 4432404875: `ensure --branch <B>` on the checked-out branch keeps it and the edit, and names it in the row."""
    out(repo, "switch", "-q", "-c", "feature")
    commit_file(repo, "feat.txt", "feature\n", "feature work")
    (repo / "app.py").write_text("one\nEDITED\nthree\n")
    cp = run("ensure", "--slug", "rev", "--branch", "feature", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert branch(repo) == "feature"
    assert (repo / "app.py").read_text() == "one\nEDITED\nthree\n"
    assert row_of(repo, "rev")["branch"] == "feature"
    assert cp.out.strip().splitlines()[-1] == f"SESSION worktree={repo} slug=rev branch=feature"


def test_d2_ensure_branch_parallel_worktree_checks_out_existing_branch(repo):
    """Copilot 4432404875: `ensure --branch <B> --move yes` puts the new worktree on existing B, not a fresh branch off base."""
    assert run("ensure", "--slug", "a", cwd=repo).returncode == 0
    tmp = repo.parent / "tmp-feature-wt"
    out(repo, "worktree", "add", "-q", "-b", "feature", str(tmp), "main")
    feat = commit_file(tmp, "feat.txt", "feature\n", "feature work")
    out(repo, "worktree", "remove", "--force", str(tmp))
    cp = run("ensure", "--slug", "rev", "--branch", "feature", "--move", "yes", cwd=repo)
    assert cp.returncode == 0, cp.both
    wt = row_of(repo, "rev")["worktree"]
    assert branch(wt) == "feature"
    assert git(wt, "rev-parse", "HEAD").stdout.strip() == feat
    assert (Path(wt) / "feat.txt").read_text() == "feature\n"
    assert cp.out.strip().splitlines()[-1] == f"SESSION worktree={wt} slug=rev branch=feature"


def test_d3_review_command_resolves_target_before_ensure_and_passes_branch():
    """Copilot 4432404875: commands/review.md resolves the target first and hands it to ensure via --branch."""
    lines = (ROOT / "commands" / "review.md").read_text(encoding="utf-8").splitlines()
    idx = next((i for i, l in enumerate(lines) if "session ensure" in l), None)
    assert idx is not None, "no ensure line"
    assert re.search(r"--branch\s+<target>", lines[idx]), lines[idx]
    assert "target" in "\n".join(lines[:idx]).lower(), "target is not resolved before the ensure line"


# ---- review round: stale-base fallback --------------------------------------------------------

def test_a3_detached_host_with_deleted_base_still_starts(repo):
    """A closed session's base is gone and the host sits on a detached HEAD: ensure and create fall back to an existing branch."""
    assert run("ensure", "--slug", "a", cwd=repo).returncode == 0
    assert run("close", "--slug", "a", cwd=repo).returncode == 0
    out(repo, "branch", "-m", "main", "trunk")
    out(repo, "checkout", "-q", "--detach")
    cp = run("ensure", "--slug", "z", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert row_of(repo, "z")["branch"] == "superlite/sessions/z"
    assert reg_of(repo)["base"] == "trunk"
    out(repo, "checkout", "-q", "--detach")
    assert run("close", "--slug", "z", cwd=repo).returncode == 0
    out(repo, "checkout", "-q", "--detach")
    cp = run("create", "--slug", "y", cwd=repo)
    assert cp.returncode == 0, cp.both


def test_d4_adopted_branch_survives_close_and_discard(repo):
    """An adopted user branch is never deleted by close or discard, with or without --keep-branch."""
    out(repo, "branch", "other")
    assert run("ensure", "--slug", "r", "--branch", "other", cwd=repo).returncode == 0
    assert row_of(repo, "r").get("adopted_branch") is True
    assert run("close", "--slug", "r", cwd=repo).returncode == 0
    assert "other" in out(repo, "branch", "--format=%(refname:short)").split()
    assert run("discard", "--slug", "r", cwd=repo).returncode == 0
    assert "other" in out(repo, "branch", "--format=%(refname:short)").split()
