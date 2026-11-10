"""End to end: adopt a harness worktree in place and walk the whole session lifecycle through the real CLI.

Real modules, real git, `superlite-cli session ...` (no fakes). `where` is the directory the command starts in:
the hub, or the adopted worktree itself.
"""
from __future__ import annotations

import json
import os
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
EXIT_LINE = "lives in a harness worktree; leave it with ExitWorktree"


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def sl(where, *args):
    cp = subprocess.run([base.bash(), str(CLI), "session", *args, "--root", str(where)], capture_output=True, text=True,
                        encoding="utf-8", timeout=180, env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    return cp.returncode, cp.stdout, cp.stderr


def ok(where, *args):
    rc, out, err = sl(where, *args)
    assert rc == 0, f"session {args}: rc={rc}\n{out}\n{err}"
    return out


def registry(hub) -> dict:
    return json.loads((hub / ".superlite" / "sessions.json").read_text(encoding="utf-8"))


def row(hub, slug):
    return next((s for s in registry(hub)["sessions"] if s["slug"] == slug), None)


def drift(out: str) -> list[str]:
    return [ln for ln in out.splitlines() if ln.startswith("session: drift")]


@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERLITE_HOME", str(tmp_path / "_home"))
    h = tmp_path / "hub"
    h.mkdir()
    git(h, "init", "-q", "-b", "main")
    git(h, "config", "user.email", "t@example.com")
    git(h, "config", "user.name", "t")
    (h / ".gitignore").write_text(".superlite/\n.claude/\n")
    (h / "app.py").write_text("one\n")
    git(h, "add", "-A")
    git(h, "commit", "-q", "-m", "init")
    return h


@pytest.fixture
def wt(hub):
    w = hub / ".claude" / "worktrees" / "x"
    git(hub, "worktree", "add", "-q", str(w), "-b", "worktree-x")
    (w / "f.txt").write_text("work\n")
    git(w, "add", "f.txt")
    git(w, "commit", "-q", "-m", "work")
    return w


def entry(hub, path):
    return next(w for w in registry(hub)["worktrees"] if Path(w["path"]).as_posix() == Path(path).as_posix())


def test_harness_lifecycle(hub, wt):
    before = (git(hub, "worktree", "list", "--porcelain"),)
    out = ok(wt, "ensure", "--slug", "x")
    assert "slug=x branch=worktree-x" in out
    r = row(hub, "x")
    assert r["owned"] is False and r["harness"] is True and r["adopted_branch"] is True
    assert "x" in ok(wt, "status") and "harness" in ok(wt, "status")
    ok(wt, "reconcile", "--apply")
    e = entry(hub, wt)
    assert e["kind"] == "harness" and e["owner"] == "harness" and e["session"] == "x"
    (wt / "f.txt").write_text("dirty\n")
    (wt / ".superlite").mkdir(exist_ok=True)
    (wt / ".superlite" / "progress.md").write_text("- x\n")
    out = ok(wt, "close", "--slug", "x")
    assert EXIT_LINE in out
    assert row(hub, "x") is None and (wt / "f.txt").read_text() == "dirty\n"
    assert (hub / ".superlite" / "session-store" / "x" / "progress.md").is_file()
    ok(wt, "ensure", "--slug", "x")  # back in place, no new worktree
    assert row(hub, "x")["worktree"] == wt.as_posix()
    assert (wt / ".superlite" / "progress.md").read_text() == "- x\n"
    assert EXIT_LINE in ok(wt, "discard", "--slug", "x")
    assert row(hub, "x") is None and wt.is_dir()
    assert git(hub, "branch", "--list", "worktree-x").strip()
    assert git(hub, "worktree", "list", "--porcelain") == before[0]  # no worktree made or removed


def test_use_in_place_after_close(hub, wt):
    ok(wt, "ensure", "--slug", "x")
    ok(wt, "close", "--slug", "x")
    rc, out, err = sl(wt, "use", "--slug", "x")
    assert rc == 0, out + err
    assert row(hub, "x")["worktree"] == wt.as_posix() and "REDIRECT" not in out


def test_detached_lifecycle(hub):
    w = hub / ".claude" / "worktrees" / "d"
    git(hub, "worktree", "add", "-q", "--detach", str(w))
    head = git(w, "rev-parse", "HEAD").strip()
    ok(w, "ensure", "--slug", "d")
    r = row(hub, "d")
    assert r["branch"] is None and r["detached"] is True and r["head"] == head and r["adopted_branch"] is True
    assert "d" in ok(w, "status")
    ok(w, "reconcile", "--apply")
    assert entry(hub, w)["session"] == "d" and entry(hub, w)["detached"] is True
    assert EXIT_LINE in ok(w, "close", "--slug", "d")
    assert row(hub, "d") is None
    ok(w, "ensure", "--slug", "d")
    assert row(hub, "d")["branch"] is None
    assert EXIT_LINE in ok(w, "discard", "--slug", "d")
    assert w.is_dir() and git(w, "rev-parse", "HEAD").strip() == head
    assert drift(ok(w, "reconcile")) == []


def test_reconcile_clean_after_discard(hub, wt):
    ok(wt, "ensure", "--slug", "x")
    ok(wt, "reconcile", "--apply")
    ok(wt, "discard", "--slug", "x")
    assert wt.is_dir() and git(hub, "branch", "--list", "worktree-x").strip()
    assert drift(ok(hub, "reconcile")) == []
    assert drift(ok(wt, "reconcile")) == []


def test_status_from_inside_adopted(hub, wt):
    ok(wt, "ensure", "--slug", "x")
    out = ok(wt, "status")
    line = next(ln for ln in out.splitlines() if " x " in ln and "branch worktree-x" in ln)
    assert wt.as_posix() in line and "harness" in line
    assert " x " in ok(hub, "status")
