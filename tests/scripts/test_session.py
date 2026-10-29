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


def test_restore_run_state_can_retry_after_partial_copy(tmp_path, monkeypatch):
    store = tmp_path / "store"
    live_sl = tmp_path / "live"
    store.mkdir()
    (store / "first.txt").write_text("first\n", encoding="utf-8")
    (store / "second.txt").write_text("second\n", encoding="utf-8")
    copy2 = session.shutil.copy2
    calls = 0

    def fail_second_copy(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated copy failure")
        return copy2(*args, **kwargs)

    monkeypatch.setattr(session.shutil, "copy2", fail_second_copy)
    with pytest.raises(OSError, match="simulated copy failure"):
        session.restore_run_state(store, live_sl)

    assert sorted(p.name for p in store.iterdir()) == ["first.txt", "second.txt"]
    monkeypatch.setattr(session.shutil, "copy2", copy2)
    session.restore_run_state(store, live_sl)
    assert (live_sl / "first.txt").read_text(encoding="utf-8") == "first\n"
    assert (live_sl / "second.txt").read_text(encoding="utf-8") == "second\n"
    assert not store.exists()


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


HOST_REL = Path(".superlite") / "worktrees" / "sessions" / "host"
HOOK = ROOT / "hooks" / "cost-report"


def reg_of(repo):
    return json.loads((repo / ".superlite" / "sessions.json").read_text(encoding="utf-8"))


def row_of(repo, slug):
    return next(s for s in reg_of(repo)["sessions"] if s["slug"] == slug)


def test_commands_find_hub_registry_from_inside_the_host(repo):
    assert run(SESSION, "create", "--slug", "auth", cwd=repo).returncode == 0
    host = repo / HOST_REL
    cp = run(SESSION, "status", cwd=host)
    assert cp.returncode == 0 and "auth" in cp.out, cp.both
    assert run(SESSION, "create", "--slug", "export", cwd=host).returncode == 0
    assert not (host / HOST_REL).exists()
    assert run(SESSION, "use", "--slug", "auth", cwd=host).returncode == 0
    assert reg_of(repo)["active"] == "auth"


def test_host_slug_is_reserved(repo):
    assert run(SESSION, "create", "--slug", "host", "--worktree", cwd=repo).returncode == 2
    assert run(SESSION, "create", "--slug", "host", cwd=repo).returncode == 2


def test_stash_restore_keeps_staged_changes(repo):
    assert run(SESSION, "create", "--slug", "export", cwd=repo).returncode == 0
    host = repo / HOST_REL
    (host / "app.py").write_text("print('staged')\n")
    git(host, "add", "app.py")
    (host / "app.py").write_text("print('worktree')\n")
    assert run(SESSION, "create", "--slug", "auth", cwd=repo).returncode == 0
    assert run(SESSION, "use", "--slug", "export", cwd=repo).returncode == 0
    assert git(host, "show", ":app.py") == "print('staged')\n"
    assert (host / "app.py").read_text(encoding="utf-8") == "print('worktree')\n"


def test_stash_matching_is_exact_per_session(repo):
    host = repo / HOST_REL
    assert run(SESSION, "create", "--slug", "auth", cwd=repo).returncode == 0
    (host / "app.py").write_text("print('auth')\n")
    assert run(SESSION, "create", "--slug", "auth-api", cwd=repo).returncode == 0
    (host / "app.py").write_text("print('auth-api')\n")
    assert run(SESSION, "create", "--slug", "other", cwd=repo).returncode == 0
    assert run(SESSION, "use", "--slug", "auth", cwd=repo).returncode == 0
    assert (host / "app.py").read_text(encoding="utf-8") == "print('auth')\n"
    assert row_of(repo, "auth-api")["has_stash"] is True
    assert run(SESSION, "discard", "--slug", "auth", cwd=repo).returncode == 0
    assert run(SESSION, "use", "--slug", "auth-api", cwd=repo).returncode == 0
    assert (host / "app.py").read_text(encoding="utf-8") == "print('auth-api')\n"


def test_use_after_discard_restores_parked_wip(repo):
    host = repo / HOST_REL
    assert run(SESSION, "create", "--slug", "a", cwd=repo).returncode == 0
    (host / ".superlite" / "progress.md").write_text("- a\n")
    (host / "app.py").write_text("print('a-wip')\n")
    assert run(SESSION, "create", "--slug", "b", cwd=repo).returncode == 0
    assert run(SESSION, "discard", "--slug", "b", cwd=repo).returncode == 0
    assert run(SESSION, "create", "--slug", "a", cwd=repo).returncode == 2  # exists: use, don't overwrite
    cp = run(SESSION, "use", "--slug", "a", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert (host / "app.py").read_text(encoding="utf-8") == "print('a-wip')\n"
    assert (host / ".superlite" / "progress.md").read_text(encoding="utf-8") == "- a\n"
    assert row_of(repo, "a")["has_stash"] is False
    assert git(host, "stash", "list").strip() == ""


def test_discard_active_serial_cleans_wip_and_branch(repo):
    host = repo / HOST_REL
    assert run(SESSION, "create", "--slug", "a", cwd=repo).returncode == 0
    (host / "app.py").write_text("print('junk')\n")
    (host / "new.txt").write_text("junk\n")
    cp = run(SESSION, "discard", "--slug", "a", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert (host / "app.py").read_text(encoding="utf-8") == "print('hi')\n"
    assert not (host / "new.txt").exists()
    assert "superlite/sessions/a" not in git(repo, "branch", "--list")
    assert run(SESSION, "create", "--slug", "b", cwd=repo).returncode == 0
    assert (host / "app.py").read_text(encoding="utf-8") == "print('hi')\n"


def test_park_keeps_sandboxes_in_place(repo):
    host = repo / HOST_REL
    assert run(SESSION, "create", "--slug", "a", cwd=repo).returncode == 0
    box = host / ".superlite" / "sandboxes" / "x-proto"
    box.mkdir(parents=True)
    (box / "keep.txt").write_text("k\n")
    (host / ".superlite" / "feature.json").write_text("{}\n")
    assert run(SESSION, "create", "--slug", "b", cwd=repo).returncode == 0
    assert (box / "keep.txt").is_file()
    assert not (host / ".superlite" / "feature.json").exists()
    assert (repo / ".superlite" / "session-store" / "a" / "feature.json").is_file()


def test_continue_drops_stash_without_reapplying(repo):
    host = repo / HOST_REL
    assert run(SESSION, "create", "--slug", "a", cwd=repo).returncode == 0
    (host / "app.py").write_text("print('a-wip')\n")
    assert run(SESSION, "create", "--slug", "b", cwd=repo).returncode == 0
    tmp = repo.parent / "tmpwt"
    git(repo, "worktree", "add", "-q", str(tmp), "superlite/sessions/a")
    (tmp / "app.py").write_text("print('upstream')\n")
    git(tmp, "commit", "-qam", "upstream")
    git(repo, "worktree", "remove", str(tmp))
    cp = run(SESSION, "use", "--slug", "a", cwd=repo)
    assert cp.returncode == 2 and "--continue" in cp.err, cp.both
    assert run(SESSION, "use", "--slug", "a", "--continue", cwd=repo).returncode == 2  # unmerged left
    (host / "app.py").write_text("print('resolved')\n")
    git(host, "add", "app.py")
    cp = run(SESSION, "use", "--slug", "a", "--continue", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert (host / "app.py").read_text(encoding="utf-8") == "print('resolved')\n"
    assert row_of(repo, "a")["has_stash"] is False
    assert git(host, "stash", "list").strip() == ""


def test_concurrent_parallel_creates_all_register(repo):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    procs = [subprocess.Popen([sys.executable, str(SESSION), "create", "--slug", f"p{i}", "--worktree"],
                              cwd=repo, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
             for i in range(5)]
    assert all(p.wait(timeout=120) == 0 for p in procs)
    assert {s["slug"] for s in reg_of(repo)["sessions"]} == {f"p{i}" for i in range(5)}


def test_hud_uses_path_components_and_works_from_worktree(repo):
    assert run(SESSION, "create", "--slug", "auth", cwd=repo).returncode == 0
    assert run(SESSION, "create", "--slug", "host-extra", "--worktree", cwd=repo).returncode == 0
    wt = repo / ".superlite" / "worktrees" / "sessions" / "host-extra"
    cp = run(HUD, str(wt), cwd=wt)
    assert cp.out.startswith("superlite session: host-extra |"), cp.both
    cp = run(HUD, str(repo / HOST_REL), cwd=repo)
    assert cp.out.startswith("superlite session: auth |"), cp.both


def test_cost_report_emits_hud_without_cost_or_run_dir(repo):
    assert run(SESSION, "create", "--slug", "solo", "--worktree", cwd=repo).returncode == 0
    wt = repo / ".superlite" / "worktrees" / "sessions" / "solo"
    import shutil
    shutil.rmtree(wt / ".superlite")
    env = dict(os.environ, SUPERLITE_NO_COST_REPORT="1", SUPERLITE_PYTHON=sys.executable,
               CLAUDE_PLUGIN_ROOT=str(ROOT))
    for k in ("CURSOR_AGENT", "CURSOR_TRACE_ID", "CURSOR_PLUGIN_ROOT", "CLAUDE_PROJECT_DIR", "CURSOR_PROJECT_DIR"):
        env.pop(k, None)
    raw = json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "hi", "cwd": str(wt)})
    out = subprocess.run(["bash", str(HOOK)], input=raw, capture_output=True, text=True, env=env).stdout
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert ctx.startswith("superlite session: solo |")


def test_session_start_hook_includes_hud():
    text = (ROOT / "hooks" / "session-start").read_text(encoding="utf-8")
    assert "session_hud.py" in text
