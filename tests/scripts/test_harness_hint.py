"""The EnterWorktree enter line under Claude Code (D5): harness helpers and session ensure/create/use output."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import harness  # noqa: E402

ENTER = re.compile(r"^session: enter EnterWorktree path=(\S+)$", re.M)


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "app"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "t")
    git(r, "config", "commit.gpgsign", "false")
    (r / ".gitignore").write_text(".superlite/\n")
    (r / "app.py").write_text("x\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    return r


def wt_of(out):
    """The worktree path the command printed: SESSION/REDIRECT, or `create --worktree`'s created-parallel line."""
    m = re.search(r"^(?:(?:SESSION|REDIRECT) worktree=(.+) slug=|session: created parallel \S+ -> (.+) on \S+$)", out, re.M)
    return m.group(1) or m.group(2)


def sess(repo, *args, claude, tmp_path, cwd=None):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    env.update(PYTHONIOENCODING="utf-8", SUPERLITE_HOME=str(tmp_path / "_home"), SUPERLITE_NO_AUTO_SESSION="")
    if claude:
        env["CLAUDECODE"] = "1"
    cp = subprocess.run([sys.executable, str(SCRIPTS / "session.py"), *args], capture_output=True, text=True,
                        encoding="utf-8", cwd=cwd or repo, env=env, timeout=180)
    return cp


def test_under_claude_code():
    assert harness.under_claude_code({"CLAUDECODE": "1"}) is True
    assert harness.under_claude_code({}) is False
    assert harness.under_claude_code({"CLAUDECODE": "0"}) is False


def test_enter_line_differs():
    p = Path("/r/.claude/worktrees/sessions-a")
    assert harness.enter_line(p, Path("/r")) == "session: enter EnterWorktree path=/r/.claude/worktrees/sessions-a"


def test_enter_line_same_is_none():
    p = Path("/r/.claude/worktrees/sessions-a")
    assert harness.enter_line(p, p) is None


def test_create_prints_enter_line(repo, tmp_path):
    git(repo, "checkout", "-q", "-b", "side")  # off the base: superlite cuts the worktree
    git(repo, "commit", "-q", "--allow-empty", "-m", "side")
    cp = sess(repo, "create", "--slug", "a", "--worktree", "--base", "main", "--branch-name", "superlite", claude=True, tmp_path=tmp_path)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    lines = cp.stdout.splitlines()
    wt = wt_of(cp.stdout)
    assert len(lines) > 1 and lines[-1] == f"session: enter EnterWorktree path={wt}"
    assert wt != Path(repo).as_posix()


def test_use_redirect_prints_enter_line(repo, tmp_path):
    assert sess(repo, "create", "--slug", "a", "--worktree", claude=False, tmp_path=tmp_path).returncode == 0
    cp = sess(repo, "use", "--slug", "a", claude=True, tmp_path=tmp_path)
    assert cp.returncode == 3, cp.stdout + cp.stderr
    wt = re.search(r"^REDIRECT worktree=(.+) slug=", cp.stdout, re.M).group(1)
    assert ENTER.search(cp.stdout).group(1) == wt
    assert cp.stdout.splitlines()[-1].startswith("session: enter ")


def test_no_enter_line_when_already_there(repo, tmp_path):
    assert sess(repo, "create", "--slug", "a", "--worktree", claude=False, tmp_path=tmp_path).returncode == 0
    cp = sess(repo, "use", "--slug", "a", claude=False, tmp_path=tmp_path)
    wt = re.search(r"^REDIRECT worktree=(.+) slug=", cp.stdout, re.M).group(1)
    cp2 = sess(repo, "use", "--slug", "a", claude=True, tmp_path=tmp_path, cwd=Path(wt))
    assert not ENTER.search(cp2.stdout)


def test_output_unchanged_off_claude_code(repo, tmp_path):
    cp = sess(repo, "create", "--slug", "a", "--worktree", claude=False, tmp_path=tmp_path)
    assert cp.returncode == 0
    assert "session: enter" not in cp.stdout + cp.stderr
    cp = sess(repo, "use", "--slug", "a", claude=False, tmp_path=tmp_path)
    assert cp.returncode == 3 and "session: enter" not in cp.stdout + cp.stderr
