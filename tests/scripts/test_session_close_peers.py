"""/superlite:close: close keeps the branch and parked work, discard deletes them, an open run's peers come back (feature checks 1, 3, 6, 7, 8)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "swarm" / "scripts"
SESSION = SCRIPTS / "session.py"
EX = SCRIPTS / "exclusive.py"
PEER = "superpowers@claude-plugins-official"


def py(script, *args, cwd, claude=False):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}  # superlite cuts the worktrees
    env.update(PYTHONIOENCODING="utf-8", **({"CLAUDE_CODE": "1"} if claude else {}))
    env.pop("SUPERLITE_NO_AUTO_SESSION", None)
    cp = subprocess.run([sys.executable, str(script), *map(str, args)], cwd=cwd, env=env,
                        capture_output=True, text=True, timeout=120)
    cp.both = cp.stdout + cp.stderr
    return cp


def git(cwd, *args, check=True):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=check).stdout


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "app"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "t")
    (r / ".gitignore").write_text(".superlite/\n.claude/\n")
    (r / "app.py").write_text("print('hi')\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    return r


def suspended_host(repo):
    assert py(SESSION, "ensure", "--slug", "a", cwd=repo).returncode == 0
    assert py(EX, "on", cwd=repo, claude=True).returncode == 0
    settings = repo / ".claude" / "settings.local.json"
    assert json.loads(settings.read_text())["enabledPlugins"][PEER] is False
    return settings


def peer_disabled(settings: Path) -> bool:
    if not settings.is_file():
        return False
    return json.loads(settings.read_text()).get("enabledPlugins", {}).get(PEER) is False


def test_close_host_with_open_run_restores_peers(repo):
    settings = suspended_host(repo)
    cp = py(SESSION, "close", "--slug", "a", "--keep-branch", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert not peer_disabled(settings)
    assert py(EX, "status", cwd=repo).stdout.startswith("peers: clear")


@pytest.fixture
def owned_b(repo):
    assert py(SESSION, "ensure", "--slug", "a", cwd=repo).returncode == 0
    cp = py(SESSION, "ensure", "--slug", "b", "--move", "yes", cwd=repo)
    assert cp.returncode == 0, cp.both
    wt = Path(next(s for s in json.loads((repo / ".superlite" / "sessions.json").read_text())["sessions"]
                   if s["slug"] == "b")["worktree"])
    (wt / "feature.py").write_text("x = 1\n")
    git(wt, "add", "feature.py")
    git(wt, "commit", "-q", "-m", "unpushed work")
    (wt / "app.py").write_text("print('edited')\n")
    return repo, wt


def test_close_keeps_branch_and_wip(owned_b):
    repo, wt = owned_b
    cp = py(SESSION, "close", "--slug", "b", "--keep-branch", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert not wt.exists()
    assert git(repo, "branch", "--list", "superlite/sessions/b").strip()
    assert "edited" in git(repo, "show", "-p", "refs/superlite/wip/b")
    assert git(repo, "rev-list", "--count", "main..superlite/sessions/b").strip() == "1"


def test_delete_after_close_removes_branch_and_wip(owned_b):
    repo, _ = owned_b
    assert py(SESSION, "close", "--slug", "b", "--keep-branch", cwd=repo).returncode == 0
    cp = py(SESSION, "discard", "--slug", "b", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert not git(repo, "branch", "--list", "superlite/sessions/b").strip()
    assert git(repo, "rev-parse", "-q", "--verify", "refs/superlite/wip/b", check=False).strip() == ""


def test_close_owned_with_open_run_restores_its_peers(repo):
    assert py(SESSION, "create", "--slug", "b", "--worktree", cwd=repo).returncode == 0  # the only open run
    wt = repo / ".claude" / "worktrees" / "sessions-b"
    assert py(EX, "on", cwd=wt, claude=True).returncode == 0
    cp = py(SESSION, "close", "--slug", "b", "--keep-branch", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert "peers: clear" in cp.stdout
    assert not wt.exists()


def test_close_keeps_peers_off_while_another_run_is_open(repo):
    assert py(SESSION, "ensure", "--slug", "a", cwd=repo).returncode == 0
    assert py(SESSION, "create", "--slug", "b", "--worktree", cwd=repo).returncode == 0
    wt = repo / ".claude" / "worktrees" / "sessions-b"
    assert py(EX, "on", cwd=wt, claude=True).returncode == 0
    cp = py(SESSION, "close", "--slug", "b", "--keep-branch", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert "1 other run open: a" in cp.stdout  # same rule as finish: the last open run restores them
