"""End to end: Claude creates the session / prototype worktree, superlite adopts it (real CLI, real git, CLAUDECODE=1).

EnterWorktree name=<n> is simulated with `git worktree add .claude/worktrees/<n> -b worktree-<n> HEAD`;
ExitWorktree remove with `git worktree remove` + `git branch -D worktree-<n>`.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import base  # noqa: E402  (base.bash(): Git Bash on Windows, never the WSL stub)

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "skills" / "swarm" / "scripts" / "superlite-cli"
CREATE = re.compile(r"^(session|proto): create EnterWorktree name=(\S+) base=(\S+)$", re.M)


def git(cwd, *args, check=True):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=check).stdout


def env_for(tmp_path):
    e = dict(os.environ, PYTHONIOENCODING="utf-8", SUPERLITE_HOME=str(tmp_path / "home"), CLAUDECODE="1")
    e.pop("SUPERLITE_NO_AUTO_SESSION", None)
    return e


def sl(where, env, *args):
    return subprocess.run([base.bash(), str(CLI), *args], cwd=where, capture_output=True, text=True, encoding="utf-8",
                          timeout=180, env=env)


def session(where, env, hub, *args, rc=0):
    tail = [] if args[0] == "branch-name" else ["--root", str(where)]
    cp = sl(where, env, "session", *args, *tail)
    assert cp.returncode == rc, f"session {args}: rc={cp.returncode} (want {rc})\n{cp.stdout}\n{cp.stderr}"
    return cp.stdout


def registry(hub) -> dict:
    return json.loads((hub / ".superlite" / "sessions.json").read_text(encoding="utf-8"))


def row(hub, slug):
    return next((s for s in registry(hub)["sessions"] if s["slug"] == slug), None)


def drift(out):
    return [ln for ln in out.splitlines() if ln.startswith("session: drift")]


def branches(hub):
    return set(git(hub, "for-each-ref", "--format=%(refname:short)", "refs/heads").split())


def refs_snapshot(hub):
    """Every ref except the ones the flow may legitimately create (session / worktree branches, wip refs)."""
    out = git(hub, "for-each-ref", "--format=%(refname) %(objectname)")
    keep = [ln for ln in out.splitlines()
            if not re.match(r"refs/heads/(superlite/|worktree-)|refs/superlite/", ln)]
    return keep, git(hub, "stash", "list")


def enter_worktree(hub, name):
    """What the harness does for EnterWorktree name=<name> (worktree.baseRef: head)."""
    wt = hub / ".claude" / "worktrees" / name
    git(hub, "worktree", "add", "-q", str(wt), "-b", f"worktree-{name}", "HEAD")
    return wt


def exit_worktree_remove(hub, wt, name):
    git(hub, "worktree", "remove", "--force", str(wt))
    git(hub, "branch", "-D", f"worktree-{name}")


@pytest.fixture
def hub(tmp_path):
    h = tmp_path.resolve() / "hub"
    h.mkdir()
    git(h, "init", "-q", "-b", "main")
    git(h, "config", "user.email", "t@example.com")
    git(h, "config", "user.name", "t")
    (h / ".gitignore").write_text(".superlite/\n.claude/\n")
    (h / "app.py").write_text("one\n")
    (h / "docs" / "plans").mkdir(parents=True)
    (h / "docs" / "plans" / "2026-01-01-f.feature.md").write_text("# f\n")
    git(h, "add", "-A")
    git(h, "commit", "-q", "-m", "init")
    git(h, "switch", "-qc", "feat")
    (h / "app.py").write_text("two\n")
    git(h, "commit", "-qam", "feat work")
    return h


def busy_hub(hub, env):
    """Session `a` is open in the hub (branch feat is its base); returns once b may be asked for."""
    session(hub, env, hub, "ensure", "--slug", "a")
    assert row(hub, "a")


def ask_b(hub, env, *extra):
    """`ensure --slug b` from the busy hub: Claude creates the worktree, so no split and no move gate."""
    cp = sl(hub, env, "session", "ensure", "--slug", "b", *extra, "--root", str(hub))
    assert "ASK move" not in cp.stdout, cp.stdout
    return cp


def create_session_worktree(hub, env, pref):
    session(hub, env, hub, "branch-name", pref)
    busy_hub(hub, env)
    cp = ask_b(hub, env)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    m = CREATE.search(cp.stdout)
    assert m and m.group(1) == "session" and m.group(2) == "b", cp.stdout
    return cp


def test_session_claude_create_lifecycle(hub, tmp_path):
    env = env_for(tmp_path)
    session(hub, env, hub, "branch-name", "superlite")
    busy_hub(hub, env)
    cp = ask_b(hub, env)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert CREATE.search(cp.stdout), cp.stdout
    hub_branch = git(hub, "branch", "--show-current").strip()
    wts, br = git(hub, "worktree", "list", "--porcelain"), branches(hub)
    assert row(hub, "b") is None and not (hub / ".claude" / "worktrees" / "b").exists()  # create line changes nothing
    refs = refs_snapshot(hub)

    wt = enter_worktree(hub, "b")
    out = session(wt, env, hub, "ensure", "--slug", "b")
    assert "slug=b branch=superlite/sessions/b" in out
    r = row(hub, "b")
    assert (r["branch"], r["harness"], r["owned"], r["adopted_branch"]) == ("superlite/sessions/b", True, False, True)
    assert git(wt, "branch", "--show-current").strip() == "superlite/sessions/b"
    assert "worktree-b" in branches(hub)

    session(wt, env, hub, "reconcile", "--apply")
    e = next(w for w in registry(hub)["worktrees"] if Path(w["path"]).as_posix() == wt.as_posix())
    assert (e["kind"], e["owner"], e["session"]) == ("harness", "harness", "b")

    out = session(wt, env, hub, "close", "--slug", "b")
    assert "leave it with ExitWorktree" in out
    assert row(hub, "b") is None and wt.is_dir()
    assert {"superlite/sessions/b", "worktree-b"} <= branches(hub)

    exit_worktree_remove(hub, wt, "b")
    assert drift(session(hub, env, hub, "reconcile")) == []
    assert "superlite/sessions/b" in branches(hub)  # superlite never deleted it
    assert refs_snapshot(hub) == refs  # unrelated refs and stashes untouched
    assert git(hub, "branch", "--show-current").strip() == hub_branch  # the hub never switched
    assert git(hub, "status", "--porcelain").strip() == ""
    assert wts == git(hub, "worktree", "list", "--porcelain") and br != branches(hub)  # no split, no move: only b's branches


def test_worktree_pref_keeps_harness_branch(hub, tmp_path):
    env = env_for(tmp_path)
    create_session_worktree(hub, env, "worktree")
    wt = enter_worktree(hub, "b")
    out = session(wt, env, hub, "ensure", "--slug", "b")
    assert "slug=b branch=worktree-b" in out
    assert git(wt, "branch", "--show-current").strip() == "worktree-b"
    r = row(hub, "b")
    assert (r["branch"], r["harness"], r["owned"]) == ("worktree-b", True, False)
    assert "superlite/sessions/b" not in branches(hub)
    assert "leave it with ExitWorktree" in session(wt, env, hub, "close", "--slug", "b")
    assert wt.is_dir() and "worktree-b" in branches(hub)


def test_reconcile_clean_after_exitworktree(hub, tmp_path):
    env = env_for(tmp_path)
    create_session_worktree(hub, env, "superlite")
    wt = enter_worktree(hub, "b")
    session(wt, env, hub, "ensure", "--slug", "b")
    session(wt, env, hub, "reconcile", "--apply")
    assert drift(session(hub, env, hub, "reconcile")) == []
    exit_worktree_remove(hub, wt, "b")  # ExitWorktree remove without closing the session first
    session(hub, env, hub, "close", "--slug", "b", rc=0) if row(hub, "b") else None
    out = session(hub, env, hub, "reconcile", "--apply")
    assert not [ln for ln in drift(out) if "dangling-active" in ln]
    assert drift(session(hub, env, hub, "reconcile")) == []
    assert not any(w["path"].endswith("/b") for w in registry(hub)["worktrees"])


def proto(where, env, *args):
    return sl(where, env, "proto", *args)


def test_proto_claude_create_lifecycle(hub, tmp_path):
    env = env_for(tmp_path)
    before_wts, before_br = git(hub, "worktree", "list", "--porcelain"), branches(hub)
    refs = refs_snapshot(hub)
    cp = proto(hub, env, "start", "--topic", "x")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert "proto: create EnterWorktree name=x-proto base=feat" in cp.stdout.splitlines()
    assert not (hub / ".superlite" / "feature.json").exists()
    assert git(hub, "worktree", "list", "--porcelain") == before_wts and branches(hub) == before_br

    wt = enter_worktree(hub, "x-proto")
    cp = proto(wt, env, "start", "--topic", "x", "--adopt")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    feat = json.loads((hub / ".superlite" / "feature.json").read_text(encoding="utf-8"))
    assert Path(feat["worktree"]).as_posix() == wt.as_posix()
    assert (feat["branch"], feat["base"], feat["adopted"], feat["topic"]) == ("worktree-x-proto", "feat", True, "x")
    assert not (wt / ".superlite" / "feature.json").exists()  # the hub's run folder, not the worktree's

    cp = proto(hub, env, "discard")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert "proto: leave the sandbox with ExitWorktree remove" in cp.stdout
    assert wt.is_dir() and "worktree-x-proto" in branches(hub)
    assert json.loads((hub / ".superlite" / "feature.json").read_text(encoding="utf-8"))["status"] == "discarded"

    exit_worktree_remove(hub, wt, "x-proto")
    assert drift(session(hub, env, hub, "reconcile")) == []
    assert refs_snapshot(hub) == refs
    assert git(hub, "branch", "--show-current").strip() == "feat"
    assert git(hub, "status", "--porcelain").strip() == ""
