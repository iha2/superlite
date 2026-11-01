"""Red-phase tests for the Copilot HIGH findings on PR #25 (close data loss, host close, no-repo ensure, orchestrator state, sync scope)."""
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
SYNC = SCRIPTS / "session_sync.py"
ORCH = SCRIPTS / "orchestrator.py"


def _exec(script, args, cwd):
    e = dict(os.environ, PYTHONIOENCODING="utf-8")
    e.pop("SUPERLITE_NO_AUTO_SESSION", None)
    e.pop("CLAUDE_PLUGIN_ROOT", None)
    e.pop("CLAUDE_CODE", None)
    cp = subprocess.run([sys.executable, str(script), *map(str, args)], capture_output=True, cwd=cwd, env=e, timeout=120)
    cp.out, cp.err = cp.stdout.decode("utf-8"), cp.stderr.decode("utf-8")
    cp.both = cp.out + cp.err
    return cp


def run(*args, cwd=None):
    return _exec(SESSION, args, cwd)


def sync(*args, cwd=None):
    return _exec(SYNC, args, cwd)


def orch(*args, cwd=None):
    return _exec(ORCH, args, cwd)


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def init_repo(r: Path) -> Path:
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "t")
    (r / ".gitignore").write_text(".superlite/\n")
    (r / "app.py").write_text("one\ntwo\nthree\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    return r


@pytest.fixture
def repo(tmp_path):
    return init_repo(tmp_path.resolve() / "app")


def reg_of(r):
    return json.loads((r / ".superlite" / "sessions.json").read_text(encoding="utf-8"))


def row_of(r, slug):
    return next(s for s in reg_of(r)["sessions"] if s["slug"] == slug)


def branch(cwd):
    return git(cwd, "branch", "--show-current").strip()


def wt_of(r, slug):
    return r / ".superlite" / "worktrees" / "sessions" / slug


@pytest.fixture
def owned_b(repo):
    """Parallel owned session b with a tracked edit, an untracked file and a run file in its worktree."""
    assert run("ensure", "--slug", "a", cwd=repo).returncode == 0
    cp = run("ensure", "--slug", "b", "--move", "yes", cwd=repo)
    assert cp.returncode == 0, cp.both
    wt = wt_of(repo, "b")
    (wt / "app.py").write_text("one\nTWO-edit\nthree\n")
    (wt / "new.txt").write_text("untracked-content\n")
    (wt / ".superlite").mkdir(exist_ok=True)
    (wt / ".superlite" / "run-file.txt").write_text("run\n")
    return repo


def test_h1a_close_owned_parallel_stashes_work(owned_b):
    """Copilot H1: closing an owned parallel session with --untracked stash must keep tracked, untracked and run files."""
    repo, wt = owned_b, wt_of(owned_b, "b")
    cp = run("close", "--slug", "b", "--keep-branch", "--untracked", "stash", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert not wt.exists()
    assert git(repo, "branch", "--list", "superlite/sessions/b").strip()
    row = row_of(repo, "b")
    assert row.get("has_stash") is True and row.get("stash_oid"), row
    shown = git(repo, "stash", "show", "-p", "--include-untracked", row["stash_oid"])
    assert "TWO-edit" in shown and "new.txt" in shown and "untracked-content" in shown
    assert (repo / ".superlite" / "session-store" / "b" / "run-file.txt").is_file()


def test_h1b_close_owned_parallel_untracked_keep_leaves_worktree(owned_b):
    """Copilot H1: closing an owned parallel session with --untracked keep must keep the worktree in place."""
    repo, wt = owned_b, wt_of(owned_b, "b")
    cp = run("close", "--slug", "b", "--keep-branch", "--untracked", "keep", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert wt.is_dir(), "worktree was removed although untracked files are kept"
    assert (wt / "new.txt").read_text() == "untracked-content\n"
    assert (wt / ".superlite" / "run-file.txt").is_file()
    assert "kept" in cp.out and "in place" in cp.out, cp.out
    assert git(repo, "branch", "--list", "superlite/sessions/b").strip()


def test_h1c_close_owned_parallel_asks_when_untracked_unset(owned_b):
    """Copilot H1: closing an owned parallel session with untracked files and no setting must ASK (exit 4) and change nothing."""
    repo, wt = owned_b, wt_of(owned_b, "b")
    before = (repo / ".superlite" / "sessions.json").read_bytes()
    cp = run("close", "--slug", "b", "--keep-branch", cwd=repo)
    assert cp.returncode == 4, cp.both
    assert "ASK untracked=" in cp.out, cp.out
    assert wt.is_dir()
    assert (wt / "new.txt").is_file() and (wt / ".superlite" / "run-file.txt").is_file()
    assert (wt / "app.py").read_text() == "one\nTWO-edit\nthree\n"
    assert (repo / ".superlite" / "sessions.json").read_bytes() == before


def test_h2_close_host_refuses_when_base_checked_out_elsewhere(repo, tmp_path):
    """Copilot H2: closing the host session must refuse, changing nothing, when the base branch is checked out in another worktree."""
    assert run("ensure", "--slug", "a", cwd=repo).returncode == 0
    (repo / "app.py").write_text("one\nTWO-edit\nthree\n")
    (repo / ".superlite" / "run-file.txt").write_text("run\n")
    other = tmp_path.resolve() / "other"
    git(repo, "worktree", "add", "-q", str(other), "main")
    stashes = git(repo, "stash", "list")
    before = (repo / ".superlite" / "sessions.json").read_bytes()
    cp = run("close", "--slug", "a", cwd=repo)
    assert cp.returncode == 2, cp.both
    assert str(other) in cp.err, cp.err
    assert branch(repo) == "superlite/sessions/a"
    assert (repo / "app.py").read_text() == "one\nTWO-edit\nthree\n"
    assert (repo / ".superlite" / "run-file.txt").is_file()
    assert reg_of(repo)["active"] == "a"
    assert git(repo, "stash", "list") == stashes
    assert (repo / ".superlite" / "sessions.json").read_bytes() == before


def test_h3a_ensure_outside_git_continues_without_session(tmp_path):
    """Copilot H3: ensure in a non-git folder must exit 0 with a no-repository notice and a SESSION line, not crash."""
    d = tmp_path.resolve() / "plain"
    d.mkdir()
    cp = run("ensure", "--slug", "demo", cwd=d)
    assert cp.returncode == 0, cp.both
    assert "session: no git repository here; continuing without a session" in cp.out, cp.both
    assert cp.out.strip().splitlines()[-1] == f"SESSION worktree={d} slug=demo branch=-"
    assert not (d / ".superlite" / "sessions.json").exists()


def test_h3b_ensure_in_repo_without_commits_continues_without_session(tmp_path):
    """Copilot H3: ensure in a git repo with no commit must exit 0 with a no-commits notice and a SESSION line, not crash."""
    d = tmp_path.resolve() / "fresh"
    d.mkdir()
    git(d, "init", "-q", "-b", "main")
    cp = run("ensure", "--slug", "demo", cwd=d)
    assert cp.returncode == 0, cp.both
    assert "session: repository has no commits yet; continuing without a session" in cp.out, cp.both
    assert cp.out.strip().splitlines()[-1] == f"SESSION worktree={d} slug=demo branch=-"
    assert not (d / ".superlite" / "sessions.json").exists()


def test_h4_orchestrator_set_in_parallel_worktree_writes_own_state(repo):
    """Copilot H4: orchestrator set run inside a parallel worktree must write that worktree's state, not the main checkout's."""
    assert run("ensure", "--slug", "a", cwd=repo).returncode == 0
    cp = run("ensure", "--slug", "b", "--move", "yes", cwd=repo)
    assert cp.returncode == 0, cp.both
    wt = wt_of(repo, "b")
    main_file = repo / ".superlite" / "orchestrator.json"
    main_file.write_text(json.dumps({"mode": "ai", "approved": True}), encoding="utf-8")
    main_bytes = main_file.read_bytes()
    assert not (wt / ".superlite" / "orchestrator.json").exists()
    cp = orch("set", "human", cwd=wt)
    assert cp.returncode == 0, cp.both
    own = wt / ".superlite" / "orchestrator.json"
    assert own.is_file(), "set did not write the parallel worktree's own orchestrator.json"
    assert json.loads(own.read_text(encoding="utf-8"))["mode"] == "human"
    assert main_file.read_bytes() == main_bytes
    sub = wt / "sub" / "dir"
    sub.mkdir(parents=True)
    st = orch("status", cwd=sub)
    assert st.returncode == 0, st.both
    assert "human" in st.out and "AI" not in st.out, st.out


@pytest.mark.parametrize("action", ["abort", "continue"])
def test_h5_sync_recovery_ignores_other_sessions_merge(repo, tmp_path, action):
    """Copilot H5: sync --abort/--continue for session p must not act on session a's merge in the main checkout."""
    assert run("ensure", "--slug", "a", cwd=repo).returncode == 0
    assert run("create", "--slug", "p", "--worktree", cwd=repo).returncode == 0
    cp = run("close", "--slug", "p", "--keep-branch", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert row_of(repo, "p").get("worktree") in (None, "")
    # a changes line two; main changes the same line (committed through a temporary worktree)
    (repo / "app.py").write_text("one\nA-side\nthree\n")
    git(repo, "commit", "-q", "-am", "a edit")
    tmpw = tmp_path.resolve() / "mainwt"
    git(repo, "worktree", "add", "-q", str(tmpw), "main")
    (tmpw / "app.py").write_text("one\nMAIN-side\nthree\n")
    git(tmpw, "commit", "-q", "-am", "main edit")
    git(repo, "worktree", "remove", "--force", str(tmpw))
    merge = subprocess.run(["git", "merge", "--no-edit", "main"], cwd=repo, capture_output=True, text=True)
    assert merge.returncode != 0
    gitdir = repo / ".git"
    assert (gitdir / "MERGE_HEAD").exists()
    head = git(repo, "rev-parse", "HEAD").strip()
    if action == "continue":
        (repo / "app.py").write_text("one\nresolved\nthree\n")
        git(repo, "add", "app.py")
    cp = sync("--slug", "p", f"--{action}", cwd=repo)
    assert cp.returncode == 2, cp.both
    assert (gitdir / "MERGE_HEAD").exists(), "session p's recovery touched session a's merge"
    assert git(repo, "rev-parse", "HEAD").strip() == head


def test_v_close_owned_parallel_staged_and_nested_untracked(repo):
    """Review: staged edits and nested untracked dirs survive owned-parallel close."""
    assert run("ensure", "--slug", "a", cwd=repo).returncode == 0
    assert run("ensure", "--slug", "b", "--move", "yes", cwd=repo).returncode == 0
    wt = wt_of(repo, "b")
    (wt / "app.py").write_text("one\nSTAGED\nthree\n")
    git(wt, "add", "app.py")
    (wt / "d1" / "d2").mkdir(parents=True)
    (wt / "d1" / "d2" / "deep.txt").write_text("deep\n")
    cp = run("close", "--slug", "b", "--untracked", "stash", cwd=repo)
    assert cp.returncode == 0, cp.both
    row = row_of(repo, "b")
    shown = git(repo, "stash", "show", "-p", "--include-untracked", row["stash_oid"])
    assert "STAGED" in shown and "deep.txt" in shown


def test_v_close_owned_parallel_worktree_deleted_by_hand(repo):
    """Review: close when the worktree dir was removed by hand must not crash."""
    import shutil
    assert run("ensure", "--slug", "a", cwd=repo).returncode == 0
    assert run("ensure", "--slug", "b", "--move", "yes", cwd=repo).returncode == 0
    shutil.rmtree(wt_of(repo, "b"))
    cp = run("close", "--slug", "b", cwd=repo)
    assert cp.returncode == 0, cp.both


def test_v_ensure_subfolder_of_no_commit_repo(tmp_path):
    """Review: ensure from a subfolder of a repo without commits continues without a session."""
    d = tmp_path.resolve() / "fresh"
    (d / "sub").mkdir(parents=True)
    git(d, "init", "-q", "-b", "main")
    cp = run("ensure", "--slug", "demo", cwd=d / "sub")
    assert cp.returncode == 0, cp.both
    assert "no commits yet" in cp.out


def test_v_sl_dir_inside_git_dir_and_no_superlite(repo):
    """Review: orchestrator status from inside .git or a repo with no .superlite uses the checkout root."""
    cp = orch("status", cwd=repo / ".git")
    assert cp.returncode == 0, cp.both
    assert not (repo / ".git" / ".superlite").exists()


def test_v_sync_recovery_when_row_worktree_path_gone(repo):
    """Review: sync --abort with a parked row (no worktree) exits 2 without crashing."""
    assert run("ensure", "--slug", "a", cwd=repo).returncode == 0
    assert run("create", "--slug", "p", "--worktree", cwd=repo).returncode == 0
    assert run("close", "--slug", "p", "--keep-branch", cwd=repo).returncode == 0
    cp = sync("--slug", "p", "--abort", cwd=repo)
    assert cp.returncode == 2, cp.both
