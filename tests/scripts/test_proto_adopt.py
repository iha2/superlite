"""proto start/discard under Claude Code: Claude creates the sandbox, superlite adopts it (D5 revised)."""
import json
import os
import subprocess
import sys

import pytest

from test_plan_tools import SCRIPTS

PROTO = SCRIPTS / "proto.py"


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def proto(cwd, *args, cc=True):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env.pop("CLAUDECODE", None)
    if cc:
        env["CLAUDECODE"] = "1"
    return subprocess.run([sys.executable, str(PROTO), *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=120)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "app"
    r.mkdir()
    git(r, "init", "-q", "-b", "feat")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "t")
    (r / ".gitignore").write_text(".superlite/\n.claude/worktrees/\n")
    (r / "a.txt").write_text("a\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    return r


def sandbox(repo, topic="x"):
    wt = repo / ".claude" / "worktrees" / f"{topic}-proto"
    git(repo, "worktree", "add", str(wt), "-b", f"worktree-{topic}-proto", "HEAD")
    return wt


def feature(repo):
    return json.loads((repo / ".superlite" / "feature.json").read_text(encoding="utf-8"))


def test_create_line_from_hub(repo):
    cp = proto(repo, "start", "--topic", "x")
    assert cp.returncode == 0
    assert cp.stdout.strip() == "proto: create EnterWorktree name=x-proto base=feat"
    assert not (repo / ".superlite" / "feature.json").exists()
    pend = json.loads((repo / ".superlite" / "proto-pending.json").read_text(encoding="utf-8"))
    assert pend["topic"] == "x" and pend["worktree"].endswith(".claude/worktrees/x-proto")
    assert not (repo / ".claude" / "worktrees" / "x-proto").exists()


def test_off_base_falls_back(repo):
    git(repo, "branch", "other")
    git(repo, "commit", "-q", "--allow-empty", "-m", "more")
    cp = proto(repo, "start", "--topic", "x", "--base", "other")
    assert cp.returncode == 0 and "create EnterWorktree" not in cp.stdout
    assert (repo / ".claude" / "worktrees" / "x-proto").is_dir()
    assert feature(repo)["branch"] == "superlite/x-proto"
    wt = (repo / ".claude" / "worktrees" / "x-proto").as_posix()
    assert cp.stdout.splitlines()[-1] == f"session: enter EnterWorktree path={wt}"


def test_adopt_records_hub_feature_json(repo):
    wt = sandbox(repo)
    cp = proto(wt, "start", "--topic", "x", "--adopt")
    assert cp.returncode == 0, cp.stderr
    st = feature(repo)
    assert st["adopted"] is True and st["branch"] == "worktree-x-proto" and st["base"] == "feat"
    assert st["worktree"].endswith(".claude/worktrees/x-proto") and st["status"] == "building"
    assert not (wt / ".superlite" / "feature.json").exists()


def test_adopt_refuses_in_hub(repo):
    cp = proto(repo, "start", "--topic", "x", "--adopt")
    assert cp.returncode == 2 and not (repo / ".superlite" / "feature.json").exists()


def test_discard_adopted_keeps_worktree_and_branch(repo):
    wt = sandbox(repo)
    proto(wt, "start", "--topic", "x", "--adopt")
    cp = proto(repo, "discard")
    assert cp.returncode == 0 and "proto: leave the sandbox with ExitWorktree remove" in cp.stdout
    assert wt.is_dir() and "worktree-x-proto" in git(repo, "branch", "--list")
    assert feature(repo)["status"] == "discarded"


def test_discard_from_inside_adopted_worktree(repo):
    wt = sandbox(repo)
    proto(wt, "start", "--topic", "x", "--adopt")
    assert "building" in proto(wt, "status").stdout
    cp = proto(wt, "discard")
    assert "nothing to discard" not in cp.stdout and "ExitWorktree remove" in cp.stdout
    assert feature(repo)["status"] == "discarded"
    assert not (wt / ".superlite" / "feature.json").exists()


def test_off_claude_code_unchanged(repo):
    cp = proto(repo, "start", "--topic", "x", cc=False)
    assert cp.returncode == 0 and "create EnterWorktree" not in cp.stdout and "EnterWorktree" not in cp.stdout
    assert feature(repo)["branch"] == "superlite/x-proto" and "adopted" not in feature(repo)
    assert proto(repo, "discard", cc=False).returncode == 0
    assert not (repo / ".claude" / "worktrees" / "x-proto").exists()
    assert "superlite/x-proto" not in git(repo, "branch", "--list")


def session(repo, name="sess", mode="human"):
    """A session worktree with its own run: human mode, an uncommitted feature plan."""
    s = repo / ".claude" / "worktrees" / name
    git(repo, "worktree", "add", str(s), "-b", f"worktree-{name}", "HEAD")
    (s / ".superlite").mkdir()
    (s / ".superlite" / "orchestrator.json").write_text(json.dumps({"mode": mode, "approved": True}))
    (s / "docs" / "plans").mkdir(parents=True)
    (s / "docs" / "plans" / "2026-11-10-x.feature.md").write_text("# x\n\n## Acceptance checks\n\n- the button shows\n")
    return s


def test_adopt_keeps_feature_state_in_originating_session_run(repo):
    s = session(repo)
    cp = proto(s, "start", "--topic", "x")
    assert cp.stdout.strip().startswith("proto: create EnterWorktree name=x-proto"), cp.stdout + cp.stderr
    wt = sandbox(repo)
    cp = proto(wt, "start", "--topic", "x", "--adopt")
    assert cp.returncode == 0, cp.stderr
    st = feature(s)
    assert st["status"] == "building" and st["adopted"] is True and st["branch"] == "worktree-x-proto"
    assert st["plan"] == (s / "docs" / "plans" / "2026-11-10-x.feature.md").resolve().as_posix()
    assert not (repo / ".superlite" / "feature.json").exists()
    assert not (wt / ".superlite" / "feature.json").exists()
    assert not (s / ".superlite" / "proto-pending.json").exists()
    cp = proto(wt, "tryout")
    assert cp.returncode == 0 and "  - the button shows" in cp.stdout, cp.stdout + cp.stderr
    assert feature(s)["status"] == "tryout"
    cp = proto(wt, "tryout", "--approve")  # the session run is human mode and holds no reply
    assert cp.returncode == 3 and "BLOCKED" in cp.stderr
    assert feature(s)["status"] == "tryout"


def test_start_inside_adopted_prototype_reopens_fix_round(repo):
    s = session(repo, mode="ai")
    proto(s, "start", "--topic", "x")
    wt = sandbox(repo)
    proto(wt, "start", "--topic", "x", "--adopt")
    cp = proto(wt, "start", "--topic", "x")
    assert cp.returncode == 0 and "create EnterWorktree" not in cp.stdout, cp.stdout + cp.stderr
    assert "(fix round 2)" in cp.stdout
    assert feature(s)["rounds"] == 2 and feature(s)["status"] == "building"
