"""session create / use (park+stash+redirect) / discard / HUD."""
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
HUD = SCRIPTS / "session_hud.py"

sys.path.insert(0, str(SCRIPTS))


def run(script: Path, *args, cwd=None):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    cp = subprocess.run([sys.executable, str(script), *map(str, args)], capture_output=True, cwd=cwd,
                        env=env, timeout=120)
    cp.out = cp.stdout.decode("utf-8")
    cp.err = cp.stderr.decode("utf-8")
    cp.both = cp.out + cp.err
    return cp


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "app"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "t")
    (r / ".gitignore").write_text(".superlite/\n")
    (r / "app.py").write_text("print('hi')\n")
    (r / "docs" / "plans").mkdir(parents=True)
    (r / "docs" / "plans" / "p.md").write_text("# p\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    return r


def test_create_makes_host_worktree_and_branch(repo):
    cp = run(SESSION, "create", "--slug", "auth", cwd=repo)
    assert cp.returncode == 0, cp.both
    host = repo / ".superlite" / "worktrees" / "sessions" / "host"
    assert host.is_dir()
    assert git(host, "branch", "--show-current").strip() == "superlite/sessions/auth"
    reg = json.loads((repo / ".superlite" / "sessions.json").read_text(encoding="utf-8"))
    assert reg["active"] == "auth" and reg["v"] == 2
    assert any(s["slug"] == "auth" and s["status"] == "active" for s in reg["sessions"])


def test_use_parks_and_restores_dirty_work(repo):
    assert run(SESSION, "create", "--slug", "export", cwd=repo).returncode == 0
    host = repo / ".superlite" / "worktrees" / "sessions" / "host"
    (host / ".superlite" / "orchestrator.json").write_text(json.dumps({"mode": "ai"}) + "\n")
    (host / ".superlite" / "progress.md").write_text("- start export\n")
    (host / "app.py").write_text("print('export-wip')\n")

    cp = run(SESSION, "create", "--slug", "auth", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert git(host, "branch", "--show-current").strip() == "superlite/sessions/auth"
    assert (host / "app.py").read_text(encoding="utf-8") == "print('hi')\n"  # clean auth tree
    store = repo / ".superlite" / "session-store" / "export"
    assert (store / "orchestrator.json").is_file()
    assert (store / "progress.md").read_text(encoding="utf-8") == "- start export\n"
    reg = json.loads((repo / ".superlite" / "sessions.json").read_text(encoding="utf-8"))
    export = next(s for s in reg["sessions"] if s["slug"] == "export")
    assert export["status"] == "parked" and export["has_stash"] is True

    cp = run(SESSION, "use", "--slug", "export", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert git(host, "branch", "--show-current").strip() == "superlite/sessions/export"
    assert (host / "app.py").read_text(encoding="utf-8") == "print('export-wip')\n"
    assert (host / ".superlite" / "progress.md").read_text(encoding="utf-8") == "- start export\n"


def test_use_redirects_when_branch_live_in_other_worktree(repo):
    assert run(SESSION, "create", "--slug", "auth", "--worktree", cwd=repo).returncode == 0
    wt = repo / ".superlite" / "worktrees" / "sessions" / "auth"
    assert wt.is_dir()
    # From hub, create/use same slug must REDIRECT, not check out again
    cp = run(SESSION, "use", "--slug", "auth", cwd=repo)
    assert cp.returncode == 3, cp.both
    assert "REDIRECT worktree=" in cp.out and "slug=auth" in cp.out
    cp2 = run(SESSION, "create", "--slug", "auth", cwd=repo)
    assert cp2.returncode == 3 and "REDIRECT" in cp2.out


def test_discard_removes_parallel_worktree(repo):
    assert run(SESSION, "create", "--slug", "auth", "--worktree", cwd=repo).returncode == 0
    wt = repo / ".superlite" / "worktrees" / "sessions" / "auth"
    assert wt.is_dir()
    cp = run(SESSION, "discard", "--slug", "auth", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert not wt.exists()
    reg = json.loads((repo / ".superlite" / "sessions.json").read_text(encoding="utf-8"))
    assert not any(s["slug"] == "auth" for s in reg["sessions"])
    assert "superlite/sessions/auth" not in git(repo, "branch", "--list")


def test_session_hud_line(repo):
    assert run(SESSION, "create", "--slug", "auth", cwd=repo).returncode == 0
    cp = run(HUD, str(repo / ".superlite"), cwd=repo)
    assert cp.returncode == 0 and cp.out.startswith("superlite session: auth |")
    assert "branch superlite/sessions/auth" in cp.out and "active" in cp.out
    env = dict(os.environ, SUPERLITE_NO_SESSION_HUD="1", PYTHONIOENCODING="utf-8")
    silent = subprocess.run([sys.executable, str(HUD), str(repo / ".superlite")], capture_output=True,
                            cwd=repo, env=env, text=True)
    assert silent.stdout.strip() == ""
