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
    assert not wt.exists()


def test_host_peers_wait_for_the_last_close(repo):
    """Host a suspended, parallel b never suspended: closing a defers, closing b (no marker) restores the hub."""
    settings = suspended_host(repo)
    assert py(SESSION, "create", "--slug", "b", "--worktree", cwd=repo).returncode == 0
    first = py(SESSION, "close", "--slug", "a", "--keep-branch", cwd=repo)
    assert first.returncode == 0, first.both
    assert "1 other run open: b" in first.stdout and peer_disabled(settings)
    assert (repo / ".superlite" / "peers.json").is_file()  # stationary: not parked with a's run files
    last = py(SESSION, "close", "--slug", "b", "--keep-branch", cwd=repo)
    assert last.returncode == 0, last.both
    assert "peers: clear" in last.stdout and not peer_disabled(settings)


def test_move_leaves_the_marker_with_the_hub(repo):
    """ensure --move used to carry peers.json into the new worktree and strand the hub's settings."""
    settings = suspended_host(repo)
    assert py(SESSION, "ensure", "--slug", "b", "--move", "yes", cwd=repo).returncode == 0
    assert (repo / ".superlite" / "peers.json").is_file()
    for slug in ("a", "b"):
        cp = py(SESSION, "close", "--slug", slug, "--keep-branch", cwd=repo)
        assert cp.returncode == 0, cp.both
    assert not peer_disabled(settings)


def test_close_adopted_worktree_waits_for_the_last_run(repo):
    """A harness worktree stays after close and leaves the registry: its marker is found by the last close."""
    wt = repo / ".claude" / "worktrees" / "h"
    git(repo, "worktree", "add", "-q", "-b", "worktree-h", str(wt))
    assert py(SESSION, "ensure", "--slug", "h", cwd=wt, claude=True).returncode == 0
    assert py(SESSION, "create", "--slug", "other", "--worktree", cwd=repo).returncode == 0  # keeps a run open
    assert py(EX, "on", cwd=wt, claude=True).returncode == 0
    settings = wt / ".claude" / "settings.local.json"
    cp = py(SESSION, "close", "--slug", "h", "--keep-branch", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert wt.exists() and "harness worktree" in cp.stdout
    assert peer_disabled(settings) and (wt / ".superlite" / "peers.json").exists()  # other is still open
    last = py(SESSION, "close", "--slug", "other", "--keep-branch", cwd=repo)
    assert last.returncode == 0, last.both
    assert not peer_disabled(settings) and not (wt / ".superlite" / "peers.json").exists()


def test_reopen_brings_back_a_marker_parked_by_an_older_version(repo):
    settings = suspended_host(repo)
    marker = repo / ".superlite" / "peers.json"
    saved = marker.read_text()
    marker.unlink()  # an older close parked it with the run files
    assert py(SESSION, "close", "--slug", "a", "--keep-branch", cwd=repo).returncode == 0
    (repo / ".superlite" / "session-store" / "a" / "peers.json").write_text(saved)
    assert py(SESSION, "use", "--slug", "a", cwd=repo).returncode == 0
    assert marker.is_file() and peer_disabled(settings)
    cp = py(SESSION, "close", "--slug", "a", "--keep-branch", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert not peer_disabled(settings)


@pytest.mark.skipif(os.name == "nt", reason="Windows paths cannot hold a quote or newline")
def test_checkouts_keep_worktree_paths_git_would_quote(repo):
    sys.path.insert(0, str(SCRIPTS))
    import exclusive
    wt = repo / ".claude" / "worktrees" / 'h "q"\nx'
    git(repo, "worktree", "add", "-q", "-b", "worktree-q", str(wt))
    found = exclusive._checkouts(repo, {"sessions": []})
    assert wt.resolve() in [p.resolve() for p in found]
