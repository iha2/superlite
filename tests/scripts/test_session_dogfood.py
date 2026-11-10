"""End to end on a hub shaped like superlite's own: session rows, harness worktree, sandboxes, a worktree outside.

Real modules, real git, the real CLI through `superlite-cli` (no fakes).
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
sys.path.insert(0, str(ROOT / "skills" / "swarm" / "scripts"))

import session_reconcile  # noqa: E402
import session_registry as sr  # noqa: E402


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def sl(hub, *args):
    """superlite-cli session ... --root hub -> (returncode, stdout, stderr)."""
    cp = subprocess.run([base.bash(), str(CLI), "session", *args, "--root", str(hub)], capture_output=True, text=True,
                        encoding="utf-8", timeout=180, env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    return cp.returncode, cp.stdout, cp.stderr


def ok(hub, *args):
    rc, out, err = sl(hub, *args)
    assert rc == 0, f"session {args}: rc={rc}\n{out}\n{err}"
    return out


def registry(hub) -> dict:
    return json.loads((hub / ".superlite" / "sessions.json").read_text(encoding="utf-8"))


def snapshot(hub) -> dict:
    return {"worktrees": git(hub, "worktree", "list", "--porcelain"),
            "refs": git(hub, "for-each-ref", "--format=%(refname) %(objectname)"),
            "stash": git(hub, "stash", "list")}


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
def busy(hub, tmp_path):
    """Two hub sessions (a parked, b active), a worktree session s1, and five hand-made worktrees."""
    ok(hub, "create", "--slug", "a")
    ok(hub, "create", "--slug", "s1", "--worktree")
    ok(hub, "create", "--slug", "b")
    s1 = Path(registry(hub)["sessions"][1]["worktree"])
    git(hub, "worktree", "add", "-q", "--detach", str(hub / ".superlite" / "worktrees" / "detached1"))
    git(hub, "worktree", "add", "-q", "-b", "p-proto", str(hub / ".superlite" / "sandboxes" / "p-proto"))
    git(hub, "worktree", "add", "-q", "-b", "n-proto", str(s1 / ".superlite" / "sandboxes" / "n-proto"))
    git(hub, "worktree", "add", "-q", "-b", "worktree-h", str(hub / ".claude" / "worktrees" / "h"))
    git(hub, "worktree", "add", "-q", "-b", "outside-br", str(tmp_path / "outside"))
    return hub


def worktree_lines(out: str) -> list[list[str]]:
    return [ln.split() for ln in out.splitlines() if ln.startswith("session: worktree ")]


def test_report_lists_six_kinds(busy):
    out = ok(busy, "reconcile")
    rows = worktree_lines(ok(busy, "status"))
    assert sorted(r[2] for r in rows) == ["harness", "proto", "proto", "session", "unknown", "unknown"]
    by_tail = {Path(r[4]).name: r[2] for r in rows}
    assert by_tail == {"sessions-s1": "session", "detached1": "unknown", "p-proto": "proto", "n-proto": "proto",
                       "h": "harness", "outside": "unknown"}
    assert next(r for r in rows if r[4].endswith("detached1"))[5] == "detached"
    assert "session: reconcile ok (0 drift, 6 worktrees)" in out


def test_report_changes_nothing(busy):
    before = (busy / ".superlite" / "sessions.json").read_bytes()
    ok(busy, "reconcile")
    assert (busy / ".superlite" / "sessions.json").read_bytes() == before
    assert "worktrees" not in registry(busy)


def test_apply_records_inventory(busy):
    out = ok(busy, "reconcile", "--apply")
    assert out.strip().endswith("session: reconcile applied 0 fixes, 6 worktrees recorded")
    wts = registry(busy)["worktrees"]
    assert len(wts) == 6
    assert {Path(w["path"]).name: (w["kind"], w["owner"]) for w in wts} == {
        "sessions-s1": ("session", "superlite"), "detached1": ("unknown", "user"), "p-proto": ("proto", "superlite"),
        "n-proto": ("proto", "superlite"), "h": ("harness", "harness"), "outside": ("unknown", "user")}
    assert [w for w in wts if w["detached"]][0]["branch"] is None
    assert {r["slug"] for r in registry(busy)["sessions"]} == {"a", "s1", "b"}  # rows untouched


def test_second_apply_noop(busy):
    ok(busy, "reconcile", "--apply")
    first = registry(busy)
    out = ok(busy, "reconcile", "--apply")
    assert "applied 0 fixes, 6 worktrees recorded" in out and "session: drift" not in out
    again = registry(busy)
    strip = lambda reg: {**reg, "worktrees": [{k: v for k, v in w.items() if k != "last_seen"} for w in reg["worktrees"]]}  # noqa: E731
    assert strip(again) == strip(first)


def test_git_snapshot_identical(busy):
    before = snapshot(busy)
    ok(busy, "reconcile")
    assert snapshot(busy) == before
    ok(busy, "reconcile", "--apply")
    assert snapshot(busy) == before


def test_report_names_real_drift_and_apply_fixes_registry_only(busy):
    """A deleted session worktree is a pruned row; apply marks the row and leaves git alone."""
    s1 = Path(next(r for r in registry(busy)["sessions"] if r["slug"] == "s1")["worktree"])
    import shutil
    shutil.rmtree(s1, ignore_errors=True)
    out = ok(busy, "reconcile")
    assert "session: drift pruned slug=s1" in out and "1 drift (report only" in out
    git_before = {k: v for k, v in snapshot(busy).items() if k != "worktrees"}
    ok(busy, "reconcile", "--apply")
    assert {k: v for k, v in snapshot(busy).items() if k != "worktrees"} == git_before
    assert next(r for r in registry(busy)["sessions"] if r["slug"] == "s1")["status"] == "pruned"


def test_close_use_discard_round_trip(hub):
    ok(hub, "create", "--slug", "dogfood-wip")
    (hub / "app.py").write_text("edited\n")
    stash = git(hub, "stash", "list")
    ref = "refs/superlite/wip/dogfood-wip"

    out = ok(hub, "close", "--slug", "dogfood-wip")
    assert "closed dogfood-wip; archived to" in out
    assert [r["slug"] for r in registry(hub)["sessions"]] == [] and registry(hub)["active"] is None
    assert git(hub, "rev-parse", "--verify", ref).strip()
    assert (hub / ".superlite" / "session-store" / "dogfood-wip" / "session.json").is_file()
    assert (hub / "app.py").read_text() == "one\n"
    assert git(hub, "stash", "list") == stash

    out = ok(hub, "use", "--slug", "dogfood-wip")
    assert "SESSION worktree=" in out and "slug=dogfood-wip" in out
    assert (hub / "app.py").read_text() == "edited\n"
    assert git(hub, "for-each-ref", ref) == ""
    assert [r["slug"] for r in registry(hub)["sessions"]] == ["dogfood-wip"]
    assert registry(hub)["active"] == "dogfood-wip"

    ok(hub, "discard", "--slug", "dogfood-wip")
    assert registry(hub)["sessions"] == []
    assert git(hub, "branch", "--list", "superlite/sessions/dogfood-wip") == ""
    assert git(hub, "for-each-ref", ref) == ""


def test_inventory_api_matches_cli(busy):
    reg = sr.read(busy)
    inv = session_reconcile.inventory(busy, reg)
    assert len(inv) == 6 and not any(sr.same(w["path"], busy) for w in inv)
