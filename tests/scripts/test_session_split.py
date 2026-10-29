"""session.py split: move an open run into a worktree and cut a new one for the next session."""
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
    (r / "docs" / "plans" / "2026-10-28-export.md").write_text("# Export\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    sl = r / ".superlite"
    sl.mkdir()
    (sl / "orchestrator.json").write_text(json.dumps({"mode": "ai", "approved": True}) + "\n")
    (sl / "progress.md").write_text("- start abc\n")
    return r


def test_split_moves_open_run_and_cuts_new_worktree(repo):
    (repo / "app.py").write_text("print('wip')\n")  # uncommitted work stays with the open run
    cp = run(SESSION, "split", "--keep", "export", "--new", "auth", cwd=repo)
    assert cp.returncode == 0, cp.both
    keep = repo / ".superlite" / "worktrees" / "export"
    new = repo / ".superlite" / "worktrees" / "auth"
    assert keep.is_dir() and new.is_dir()
    assert (keep / ".superlite" / "orchestrator.json").is_file()
    assert (keep / ".superlite" / "progress.md").read_text(encoding="utf-8") == "- start abc\n"
    assert not (repo / ".superlite" / "orchestrator.json").exists()  # main checkout has no open run
    assert (keep / "app.py").read_text(encoding="utf-8") == "print('wip')\n"
    assert (repo / "app.py").read_text(encoding="utf-8") == "print('hi')\n"  # main is clean base
    assert git(repo, "branch", "--show-current").strip() == "main"
    assert "superlite/export" in git(repo, "branch", "--list") or "export" in git(keep, "branch", "--show-current")
    assert git(new, "branch", "--show-current").strip() == "superlite/auth"
    assert not (new / ".superlite" / "orchestrator.json").exists()  # fresh for the new session
    reg = json.loads((repo / ".superlite" / "sessions.json").read_text(encoding="utf-8"))
    assert {s["topic"] for s in reg["sessions"]} == {"export", "auth"}
    assert "worktrees/export" in cp.out and "worktrees/auth" in cp.out


def test_split_reuses_current_feature_branch_for_keep(repo):
    git(repo, "checkout", "-q", "-b", "superlite/export")
    (repo / "app.py").write_text("print('on-branch')\n")
    git(repo, "add", "app.py")
    git(repo, "commit", "-q", "-m", "wip")
    cp = run(SESSION, "split", "--new", "billing", cwd=repo)  # --keep derived from branch
    assert cp.returncode == 0, cp.both
    keep = repo / ".superlite" / "worktrees" / "export"
    assert git(keep, "branch", "--show-current").strip() == "superlite/export"
    assert (keep / "app.py").read_text(encoding="utf-8") == "print('on-branch')\n"
    assert git(repo, "branch", "--show-current").strip() == "main"


def test_split_refuses_without_an_open_run(repo):
    (repo / ".superlite" / "orchestrator.json").unlink()
    cp = run(SESSION, "split", "--keep", "a", "--new", "b", cwd=repo)
    assert cp.returncode == 2 and "no open run" in cp.err


def test_split_refuses_bad_topics(repo):
    assert run(SESSION, "split", "--keep", "Bad", "--new", "ok", cwd=repo).returncode == 2
    assert run(SESSION, "split", "--keep", "ok", "--new", "Also Bad", cwd=repo).returncode == 2


def test_start_md_asks_to_separate_into_worktrees():
    text = (ROOT / "commands" / "start.md").read_text(encoding="utf-8")
    assert "separate" in text.lower() and "worktree" in text.lower()
    assert "sl session split" in text
    assert "Start something new" in text
