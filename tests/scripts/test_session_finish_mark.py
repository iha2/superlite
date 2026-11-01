"""sl finish marks the checkout's session finished; orchestrator set warns when there is none (fails open)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "swarm" / "scripts"
WARN = "session: none for this checkout; run sl session ensure --slug <topic> first"


def run(script, *args, cwd, env=None):
    e = dict(os.environ, PYTHONIOENCODING="utf-8")
    e.pop("SUPERLITE_NO_AUTO_SESSION", None)
    e.update(env or {})
    cp = subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, args)], capture_output=True, text=True,
                        cwd=cwd, env=e, timeout=120)
    return cp


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path):
    r = tmp_path.resolve() / "app"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "t")
    (r / ".gitignore").write_text(".superlite/\n")
    (r / "a.txt").write_text("a\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    return r


def finish(cwd):
    (cwd / ".superlite").mkdir(exist_ok=True)
    (cwd / ".superlite" / "progress.md").write_text("")
    return run("finish.py", cwd=cwd)


def reg_of(r):
    return json.loads((r / ".superlite" / "sessions.json").read_text(encoding="utf-8"))


def test_finish_marks_host_session_finished(repo):
    assert run("session.py", "ensure", "--slug", "demo", cwd=repo).returncode == 0
    cp = finish(repo)
    assert cp.returncode == 0, cp.stderr
    assert "session: demo finished" in cp.stdout.splitlines()
    row = reg_of(repo)["sessions"][0]
    assert row["run"] == "finished" and isinstance(row["finished_at"], int)


def test_finish_marks_parallel_worktree_only(repo):
    run("session.py", "ensure", "--slug", "demo", cwd=repo)
    run("session.py", "ensure", "--slug", "next", "--move", "yes", cwd=repo)
    wt = repo / ".superlite" / "worktrees" / "sessions" / "next"
    assert "session: next finished" in finish(wt).stdout
    rows = {s["slug"]: s for s in reg_of(repo)["sessions"]}
    assert rows["next"]["run"] == "finished" and rows["demo"]["run"] == "open"


def test_finish_fails_open_without_registry_git_or_valid_json(repo, tmp_path):
    cp = finish(repo)
    assert cp.returncode == 0 and "session:" not in cp.stdout
    plain = tmp_path / "plain"
    plain.mkdir()
    cp = finish(plain)
    assert cp.returncode == 0 and "session:" not in cp.stdout and cp.stderr == ""
    (repo / ".superlite" / "sessions.json").write_text("{not json")
    cp = finish(repo)
    assert cp.returncode == 0 and "session:" not in cp.stdout


def orch(cwd, env=None):
    return run("orchestrator.py", "set", "human", cwd=cwd, env=env)


def test_orchestrator_set_warns_on_stderr_without_session(repo):
    cp = orch(repo)
    assert cp.returncode == 0
    assert WARN in cp.stderr and WARN not in cp.stdout
    assert json.loads((repo / ".superlite" / "orchestrator.json").read_text())["mode"] == "human"


def test_orchestrator_set_silent_with_session_opt_out_or_no_git(repo, tmp_path):
    run("session.py", "ensure", "--slug", "demo", cwd=repo)
    assert WARN not in orch(repo).stderr
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q")
    cp = orch(other, env={"SUPERLITE_NO_AUTO_SESSION": "1"})
    assert cp.returncode == 0 and WARN not in cp.stderr
    plain = tmp_path / "plain"
    plain.mkdir()
    cp = orch(plain)
    assert cp.returncode == 0 and cp.stderr == ""
    assert (plain / ".superlite" / "orchestrator.json").is_file()


def test_unreadable_registry_never_changes_exit_or_stdout(repo):
    (repo / ".superlite").mkdir(exist_ok=True)
    (repo / ".superlite" / "sessions.json").mkdir()  # reading it raises IsADirectoryError
    cp = finish(repo)
    assert cp.returncode == 0 and "session:" not in cp.stdout and "Traceback" not in cp.stderr
    cp = orch(repo)
    assert cp.returncode == 0 and "Traceback" not in cp.stderr and "session:" not in cp.stdout
    assert json.loads((repo / ".superlite" / "orchestrator.json").read_text())["mode"] == "human"


def test_orchestrator_set_stdout_and_state_same_with_and_without_warning(repo):
    a = orch(repo)
    b = orch(repo, env={"SUPERLITE_NO_AUTO_SESSION": "1"})
    assert a.returncode == b.returncode == 0
    assert a.stdout.splitlines()[1:] == b.stdout.splitlines()[1:]
    assert WARN in a.stderr and WARN not in b.stderr
