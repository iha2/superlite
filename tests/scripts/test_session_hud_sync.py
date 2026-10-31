"""HUD main:+N, sync field, offer line, moved-out line (hand-written sessions.json fixtures)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HUD = ROOT / "skills" / "swarm" / "scripts" / "session_hud.py"
ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "t@t"}


def git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True, env=ENV).stdout.strip()


def commit(h, n):
    (h / f"{n}.txt").write_text(n)
    git(h, "add", ".")
    git(h, "commit", "-qm", n)
    return git(h, "rev-parse", "HEAD")


@pytest.fixture
def hub(tmp_path):
    h = tmp_path / "hub"
    h.mkdir()
    git(h, "init", "-q", "-b", "main")
    return h


def hud(cwd):
    r = subprocess.run([sys.executable, str(HUD), str(cwd)], capture_output=True, text=True, env=ENV)
    return r.stdout.splitlines()


def wt(hub, slug):
    p = hub.parent / slug
    git(hub, "worktree", "add", "-q", "-b", f"s/{slug}", str(p))
    return p


def row(slug, p, **kw):
    return {"slug": slug, "branch": f"s/{slug}", "worktree": str(p), "status": "parallel", **kw}


def test_ahead_and_offer(hub):
    commit(hub, "c0")
    p = wt(hub, "b")
    sha = git(hub, "rev-parse", "HEAD")
    commit(hub, "c1")
    commit(hub, "c2")
    (hub / ".superlite").mkdir()
    (hub / ".superlite" / "sessions.json").write_text(json.dumps(
        {"base": "main", "active": None, "sessions": [row("b", p, base_sha=sha)]}))
    out = hud(p)
    assert len(out) == 2
    assert out[0].endswith("| run:open | main:+2")
    assert out[1].startswith("superlite sync: local main moved ahead by 2 commits")
    assert "sl session sync --slug b --later" in out[1]


def test_offered_suppresses_offer_and_flags(hub):
    commit(hub, "c0")
    p = wt(hub, "b")
    sha = git(hub, "rev-parse", "HEAD")
    cur = commit(hub, "c1")
    (hub / ".superlite").mkdir()
    (hub / ".superlite" / "sessions.json").write_text(json.dumps(
        {"base": "main", "active": None, "sessions": [row("b", p, base_sha=sha, sync_offered=cur, sync="needs-sync")]}))
    out = hud(p)
    assert len(out) == 1
    assert out[0].endswith("| run:open | main:+1 | needs-sync")


def test_no_base_sha_or_equal_shows_nothing(hub):
    commit(hub, "c0")
    p = wt(hub, "b")
    q = wt(hub, "c")
    sha = git(hub, "rev-parse", "HEAD")
    (hub / ".superlite").mkdir()
    (hub / ".superlite" / "sessions.json").write_text(json.dumps(
        {"base": "main", "active": None, "sessions": [row("b", p), row("c", q, base_sha=sha, sync="conflict")]}))
    assert hud(p) == [hud(p)[0]] and "main:+" not in hud(p)[0]
    assert hud(q)[0].endswith("| run:open | conflict")


def test_moved_out_line(hub):
    commit(hub, "c0")
    p = wt(hub, "a")
    (hub / ".superlite").mkdir()
    (hub / ".superlite" / "sessions.json").write_text(json.dumps(
        {"base": "main", "active": None, "sessions": [row("a", p, moved_from=str(hub))]}))
    assert hud(hub) == [f"superlite session: a moved to {p} — move the agent root there"]
