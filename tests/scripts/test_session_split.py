"""session.py split: parallel move of an open run + cut a new worktree."""
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

import session  # noqa: E402


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
    (repo / "app.py").write_text("print('wip')\n")
    cp = run(SESSION, "split", "--keep", "export", "--new", "auth", cwd=repo)
    assert cp.returncode == 0, cp.both
    keep = repo / ".superlite" / "worktrees" / "sessions" / "export"
    new = repo / ".superlite" / "worktrees" / "sessions" / "auth"
    assert keep.is_dir() and new.is_dir()
    assert (keep / ".superlite" / "orchestrator.json").is_file()
    assert (keep / ".superlite" / "progress.md").read_text(encoding="utf-8") == "- start abc\n"
    assert not (repo / ".superlite" / "orchestrator.json").exists()
    assert (keep / "app.py").read_text(encoding="utf-8") == "print('wip')\n"
    assert (repo / "app.py").read_text(encoding="utf-8") == "print('hi')\n"
    assert git(repo, "branch", "--show-current").strip() == "main"
    assert git(keep, "branch", "--show-current").strip() == "superlite/sessions/export"
    assert git(new, "branch", "--show-current").strip() == "superlite/sessions/auth"
    assert not (new / ".superlite" / "orchestrator.json").exists()
    reg = json.loads((repo / ".superlite" / "sessions.json").read_text(encoding="utf-8"))
    assert {s["slug"] for s in reg["sessions"]} == {"export", "auth"}
    assert "worktrees/sessions/export" in cp.out and "worktrees/sessions/auth" in cp.out


def test_split_reuses_current_feature_branch_for_keep(repo):
    git(repo, "checkout", "-q", "-b", "superlite/sessions/export")
    (repo / "app.py").write_text("print('on-branch')\n")
    git(repo, "add", "app.py")
    git(repo, "commit", "-q", "-m", "wip")
    cp = run(SESSION, "split", "--new", "billing", cwd=repo)
    assert cp.returncode == 0, cp.both
    keep = repo / ".superlite" / "worktrees" / "sessions" / "export"
    assert git(keep, "branch", "--show-current").strip() == "superlite/sessions/export"
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


def test_sessions_doc_has_mermaid_and_is_indexed():
    doc = (ROOT / "docs" / "sessions.md").read_text(encoding="utf-8")
    assert "```mermaid" in doc and doc.count("```mermaid") >= 3
    assert "sl session split" in doc and "sessions.json" in doc
    index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    assert "sessions.md" in index


def test_split_rolls_back_when_a_late_step_fails(repo):
    (repo / "app.py").write_text("print('wip')\n")
    (repo / "app.py.new").write_text("untracked\n")
    sessions = repo / ".superlite" / "worktrees"
    sessions.mkdir(parents=True)
    (sessions / "sessions").write_text("not a dir\n")  # blocks worktree creation after the run moved
    cp = run(SESSION, "split", "--keep", "export", "--new", "auth", cwd=repo)
    assert cp.returncode == 1 and "rolling back" in cp.err, cp.both
    assert (repo / ".superlite" / "orchestrator.json").is_file()
    assert (repo / ".superlite" / "progress.md").read_text(encoding="utf-8") == "- start abc\n"
    assert (repo / "app.py").read_text(encoding="utf-8") == "print('wip')\n"
    assert (repo / "app.py.new").is_file()
    assert git(repo, "branch", "--show-current").strip() == "main"
    assert git(repo, "stash", "list").strip() == ""
    assert "superlite/sessions/export" not in git(repo, "branch", "--list")


def test_split_restores_run_state_after_partial_move(repo, monkeypatch):
    original_move = session.shutil.move
    calls = 0

    def fail_second_move(src, dst, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated move failure")
        return original_move(src, dst, *args, **kwargs)

    monkeypatch.setattr(session.shutil, "move", fail_second_move)
    result = session.main(["split", "--keep", "export", "--new", "auth", "--root", str(repo)])

    assert result == 1
    assert (repo / ".superlite" / "orchestrator.json").is_file()
    assert (repo / ".superlite" / "progress.md").read_text(encoding="utf-8") == "- start abc\n"


def test_split_does_not_restore_run_state_when_first_move_fails(repo, monkeypatch):
    original_move_run_state = session.move_run_state
    restore_calls = 0

    def fail_move(*args, **kwargs):
        raise OSError("simulated move failure")

    def track_move_run_state(*args, **kwargs):
        nonlocal restore_calls
        restore_calls += 1
        return original_move_run_state(*args, **kwargs)

    monkeypatch.setattr(session.shutil, "move", fail_move)
    monkeypatch.setattr(session, "move_run_state", track_move_run_state)
    result = session.main(["split", "--keep", "export", "--new", "auth", "--root", str(repo)])

    assert result == 1
    assert restore_calls == 1
    assert (repo / ".superlite" / "orchestrator.json").is_file()


def test_split_rejects_unknown_base_without_side_effects(repo):
    (repo / "app.py").write_text("print('wip')\n")
    cp = run(SESSION, "split", "--keep", "export", "--new", "auth", "--base", "nope", cwd=repo)
    assert cp.returncode == 2 and "unknown --base" in cp.err
    assert (repo / ".superlite" / "orchestrator.json").is_file()
    assert (repo / "app.py").read_text(encoding="utf-8") == "print('wip')\n"


def test_split_keeps_sandboxes_and_rebinds_checkpoints(repo):
    box = repo / ".superlite" / "sandboxes" / "x-proto"
    box.mkdir(parents=True)
    (box / "keep.txt").write_text("k\n")
    (repo / ".superlite" / "checkpoints.json").write_text(json.dumps({"branch": "refs/heads/main"}) + "\n")
    cp = run(SESSION, "split", "--keep", "export", "--new", "auth", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert (box / "keep.txt").is_file()
    keep = repo / ".superlite" / "worktrees" / "sessions" / "export"
    st = json.loads((keep / ".superlite" / "checkpoints.json").read_text(encoding="utf-8"))
    assert st["branch"] == "refs/heads/superlite/sessions/export"


def test_split_preserves_staged_changes(repo):
    (repo / "app.py").write_text("print('staged')\n")
    git(repo, "add", "app.py")
    (repo / "app.py").write_text("print('worktree')\n")
    assert run(SESSION, "split", "--keep", "export", "--new", "auth", cwd=repo).returncode == 0
    keep = repo / ".superlite" / "worktrees" / "sessions" / "export"
    assert git(keep, "show", ":app.py") == "print('staged')\n"
    assert (keep / "app.py").read_text(encoding="utf-8") == "print('worktree')\n"


def test_split_from_linked_feature_worktree_keeps_run_in_place(repo):
    import shutil
    run_dir = repo / ".superlite"
    feat = run_dir / "worktrees" / "export"
    git(repo, "worktree", "add", "-q", "-b", "superlite/export", str(feat), "main")
    (feat / ".superlite").mkdir()
    for name in ("orchestrator.json", "progress.md"):
        shutil.move(str(run_dir / name), str(feat / ".superlite" / name))
    cp = run(SESSION, "split", "--new", "auth", cwd=feat)
    assert cp.returncode == 0, cp.both
    assert (feat / ".superlite" / "orchestrator.json").is_file()
    assert git(feat, "branch", "--show-current").strip() == "superlite/export"
    new = repo / ".superlite" / "worktrees" / "sessions" / "auth"
    assert git(new, "branch", "--show-current").strip() == "superlite/sessions/auth"
    reg = json.loads((repo / ".superlite" / "sessions.json").read_text(encoding="utf-8"))
    export = next(s for s in reg["sessions"] if s["slug"] == "export")
    assert export["branch"] == "superlite/export" and export["owned"] is False
    # a second split from the new worktree works too
    (new / ".superlite").mkdir(exist_ok=True)
    (new / ".superlite" / "orchestrator.json").write_text("{}\n")
    assert run(SESSION, "split", "--new", "billing", cwd=new).returncode == 0
    # discarding the in-place run never removes the user's worktree
    assert run(SESSION, "discard", "--slug", "export", "--keep-branch", cwd=repo).returncode == 0
    assert feat.is_dir()


def test_use_honors_recorded_branch(repo):
    git(repo, "checkout", "-q", "-b", "superlite/export")
    assert run(SESSION, "split", "--new", "auth", cwd=repo).returncode == 0
    cp = run(SESSION, "use", "--slug", "export", cwd=repo)
    assert cp.returncode == 3 and "branch=superlite/export" in cp.out, cp.both
    cp = run(SESSION, "create", "--slug", "export", cwd=repo)
    assert cp.returncode == 3 and "branch=superlite/export" in cp.out, cp.both
