"""End to end: the new worktree home, the enter line and reconcile kinds, through the real CLI in temp git repos."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "skills" / "swarm" / "scripts" / "superlite-cli"

PLAN = """# P

### T1 first · Complexity 2 · Est. context 10k
**Owns:** a.py
**Consumes:** none
"""


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def env_for(tmp_path, claude):
    e = dict(os.environ, PYTHONIOENCODING="utf-8", SUPERLITE_HOME=str(tmp_path / "_home"))
    e.pop("CLAUDECODE", None)
    if claude:
        e["CLAUDECODE"] = "1"
    return e


def run(cwd, env, *args):
    return subprocess.run(["bash", str(CLI), *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          timeout=180, env=env)


def session(hub, env, *args):
    cp = run(hub, env, "session", *args, "--root", str(hub))
    assert cp.returncode == 0, f"session {args}: rc={cp.returncode}\n{cp.stdout}\n{cp.stderr}"
    return cp.stdout


def registry(hub) -> dict:
    return json.loads((hub / ".superlite" / "sessions.json").read_text(encoding="utf-8"))


def entry(hub, path):
    return next((w for w in registry(hub).get("worktrees", [])
                 if Path(w["path"]).as_posix().rstrip("/").endswith(Path(path).relative_to(hub).as_posix())), None)


def snapshot(hub):
    return git(hub, "worktree", "list", "--porcelain"), git(hub, "branch", "-a", "-v"), git(hub, "stash", "list")


@pytest.fixture
def hub(tmp_path):
    h = tmp_path.resolve() / "hub"
    h.mkdir()
    git(h, "init", "-q", "-b", "main")
    git(h, "config", "user.email", "t@example.com")
    git(h, "config", "user.name", "t")
    (h / ".gitignore").write_text(".superlite/\n")
    (h / "a.py").write_text("V = 0\n")
    (h / "docs").mkdir()
    (h / "docs" / "plan.md").write_text(PLAN)
    git(h, "add", "-A")
    git(h, "commit", "-q", "-m", "init")
    return h


def off_base(hub):
    """Move the hub off its base so Claude Code's create line is skipped and superlite cuts the worktree."""
    git(hub, "checkout", "-q", "-b", "side")
    git(hub, "commit", "-q", "--allow-empty", "-m", "side")


def test_new_home_lifecycle(hub, tmp_path):
    env = env_for(tmp_path, claude=True)
    off_base(hub)
    session(hub, env, "create", "--slug", "a", "--worktree", "--base", "main", "--branch-name", "superlite")
    wt = hub / ".claude" / "worktrees" / "sessions-a"
    assert wt.is_dir()
    assert git(hub, "status", "--porcelain").strip() == ""
    assert git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == "superlite/sessions/a"
    session(hub, env, "reconcile", "--apply")
    e = entry(hub, wt)
    assert e and e["kind"] == "session" and e["owner"] == "superlite" and e["session"] == "a"
    session(hub, env, "close", "--slug", "a")
    assert not wt.exists()
    assert not any(s["slug"] == "a" for s in registry(hub)["sessions"])


def test_enter_line_end_to_end(hub, tmp_path):
    wt = hub / ".claude" / "worktrees" / "sessions-a"
    off_base(hub)
    out = session(hub, env_for(tmp_path, claude=True), "create", "--slug", "a", "--worktree", "--base", "main", "--branch-name", "superlite")
    lines = [ln for ln in out.splitlines() if ln.startswith("session: enter EnterWorktree path=")]
    assert len(lines) == 1
    assert Path(lines[0].split("path=", 1)[1]).as_posix().endswith(".claude/worktrees/sessions-a")
    assert out.strip().splitlines()[-1] == lines[0]  # after the command's own output
    assert wt.is_dir()


def test_no_enter_line_off_claude_code(hub, tmp_path):
    out = session(hub, env_for(tmp_path, claude=False), "create", "--slug", "a", "--worktree")
    assert "EnterWorktree" not in out


def test_old_home_still_closes(hub, tmp_path):
    env = env_for(tmp_path, claude=False)
    session(hub, env, "create", "--slug", "a", "--worktree")
    new = hub / ".claude" / "worktrees" / "sessions-a"
    old = hub / ".superlite" / "worktrees" / "sessions" / "a"
    old.parent.mkdir(parents=True, exist_ok=True)
    git(hub, "worktree", "move", str(new), str(old))
    reg = registry(hub)
    for s in reg["sessions"]:
        if s["slug"] == "a":
            s["worktree"] = old.as_posix()
    (hub / ".superlite" / "sessions.json").write_text(json.dumps(reg), encoding="utf-8")
    session(hub, env, "close", "--slug", "a")
    assert not old.exists()
    assert not any(s["slug"] == "a" for s in registry(hub)["sessions"])


def test_reconcile_kinds_in_new_home(hub, tmp_path):
    env = env_for(tmp_path, claude=False)
    session(hub, env, "create", "--slug", "a", "--worktree")
    agent = hub / ".claude" / "worktrees" / "agent-1"
    git(hub, "worktree", "add", "-q", str(agent), "-b", "worktree-agent-1")
    before = snapshot(hub)
    session(hub, env, "reconcile", "--apply")
    assert snapshot(hub) == before
    s = entry(hub, hub / ".claude" / "worktrees" / "sessions-a")
    h = entry(hub, agent)
    assert s and (s["kind"], s["owner"]) == ("session", "superlite")
    assert h and (h["kind"], h["owner"]) == ("harness", "harness")
    assert git(hub, "status", "--porcelain").strip() == ""
