"""HUD run field, main-checkout fallback, and project state from the session's run."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "swarm" / "scripts"
HUD = SCRIPTS / "session_hud.py"
HOOK = ROOT / "hooks" / "session-start"
BASH = shutil.which("bash")
sys.path.insert(0, str(SCRIPTS))
import suggest_flow  # noqa: E402


def git(cwd, *a):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t"})


@pytest.fixture
def hub(tmp_path):
    h = tmp_path / "hub"
    h.mkdir()
    git(h, "init", "-q", "-b", "main")
    (h / "a.txt").write_text("x")
    git(h, "add", ".")
    git(h, "commit", "-qm", "i")
    (h / ".superlite").mkdir()
    return h


def reg(hub, sessions, active=None):
    (hub / ".superlite" / "sessions.json").write_text(json.dumps({"sessions": sessions, "active": active}))


def hud(cwd):
    env = {k: v for k, v in os.environ.items() if k != "SUPERLITE_NO_SESSION_HUD"}
    return subprocess.run([sys.executable, str(HUD), str(cwd)], capture_output=True, text=True, env=env).stdout.strip()


def row(hub, slug="demo", **kw):
    return {"slug": slug, "branch": "main", "worktree": str(hub), "status": "active", **kw}


def test_hud_appends_run_finished_only(hub):
    reg(hub, [row(hub, run="finished")], "demo")
    assert hud(hub) == "superlite session: demo | branch main | worktree . | active | stash:no | run:finished"


def test_hud_missing_run_is_open(hub):
    reg(hub, [row(hub)], "demo")
    assert hud(hub).endswith("| stash:no | run:open")


def test_main_checkout_without_active_prints_none_here(hub):
    other = hub / ".superlite" / "worktrees" / "sessions" / "p1"
    reg(hub, [row(hub, "p1", worktree=str(other), status="parked"), row(hub, "p2", worktree=str(other) + "2",
                                                                       status="parked")])
    assert hud(hub) == "superlite session: none here (main checkout) | 2 sessions"


def _start(cwd, extra=None):
    env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(ROOT), "SUPERLITE_HOME": str(cwd / "home")}
    env.pop("SUPERLITE_NO_SESSION_HUD", None)
    r = subprocess.run([BASH, str(HOOK)], input="", capture_output=True, text=True, encoding="utf-8", env=env,
                       cwd=str(cwd))
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]


def test_session_start_finished_run_is_existing_code(hub):
    (hub / ".superlite" / "orchestrator.json").write_text("{}")
    (hub / "docs" / "plans").mkdir(parents=True)
    reg(hub, [row(hub, run="finished")], "demo")
    ctx = _start(hub)
    assert "Project state: existing code." in ctx and "| run:finished" in ctx


def test_session_start_open_run_in_progress(hub):
    (hub / ".superlite" / "orchestrator.json").write_text("{}")
    reg(hub, [row(hub, run="open")], "demo")
    assert "Project state: run in progress." in _start(hub)


def test_session_start_no_row_falls_back_to_orchestrator(hub):
    (hub / ".superlite" / "orchestrator.json").write_text("{}")
    (hub / "docs" / "plans").mkdir(parents=True)
    assert "Project state: run in progress." in _start(hub)


def test_project_state_follows_run(hub):
    (hub / ".superlite" / "orchestrator.json").write_text("{}")
    (hub / "docs" / "plans").mkdir(parents=True)
    reg(hub, [row(hub, run="finished")], "demo")
    assert suggest_flow.project_state(str(hub)) == suggest_flow.EXISTING
    reg(hub, [row(hub, run="open")], "demo")
    assert suggest_flow.project_state(str(hub)) == suggest_flow.RUNNING
    reg(hub, [row(hub)], "demo")
    assert suggest_flow.project_state(str(hub)) == suggest_flow.RUNNING


def test_row_without_orchestrator_is_existing_code(hub):
    reg(hub, [row(hub)], "demo")
    assert suggest_flow.project_state(str(hub)) == suggest_flow.EXISTING
    assert "Project state: existing code." in _start(hub)


def test_hooks_parse_under_bin_bash():
    assert subprocess.run(["/bin/bash", "-n", str(HOOK)]).returncode == 0


def test_parked_host_row_is_none_here(hub):
    reg(hub, [row(hub, status="parked", run="finished", has_stash=True)], None)
    assert hud(hub) == "superlite session: none here (main checkout) | 1 sessions"


def test_active_host_row_names_checkout(hub):
    reg(hub, [row(hub, status="parked", run="finished")], "demo")
    assert hud(hub).startswith("superlite session: demo |")


def test_parallel_row_still_matches_by_path(hub):
    wt = hub / ".superlite" / "worktrees" / "sessions" / "p1"
    git(hub, "worktree", "add", "-q", "-b", "p1", str(wt))
    reg(hub, [row(hub, "p1", worktree=str(wt), status="parked", branch="p1")], None)
    assert hud(wt).startswith("superlite session: p1 |")
