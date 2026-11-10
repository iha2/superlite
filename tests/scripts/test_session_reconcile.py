"""session_reconcile: inventory, drift and apply against real git repos (reads git, never changes it)."""
from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import session_reconcile as rec  # noqa: E402


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def hub(tmp_path):
    r = tmp_path.resolve() / "app"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "t")
    (r / "a.txt").write_text("a\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    (r / ".git" / "info").mkdir(exist_ok=True)
    (r / ".git" / "info" / "exclude").write_text(".superlite/\n.claude/\n")
    return r


def add_wt(hub, rel, branch=None, detach=False):
    p = hub / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if detach:
        git(hub, "worktree", "add", "-q", "--detach", str(p))
    else:
        git(hub, "worktree", "add", "-q", "-b", branch, str(p))
    return p


def row(slug, wt=None, **kw):
    r = {"slug": slug, "branch": f"superlite/sessions/{slug}", "status": "parked"}
    if wt is not None:
        r["worktree"] = str(wt)
    r.update(kw)
    return r


def mkreg(*rows, active=None):
    return {"v": 2, "base": "main", "active": active, "sessions": list(rows), "host_worktree": None}


def snapshot(hub, with_file=True):
    snap = [git(hub, "worktree", "list", "--porcelain"), git(hub, "for-each-ref"), git(hub, "stash", "list")]
    f = hub / ".superlite" / "sessions.json"
    if with_file:
        snap.append(f.read_bytes() if f.is_file() else b"")
    return snap


def classes(ds):
    return sorted((d["class"], d["slug"]) for d in ds)


def test_inventory_kinds_and_owners(hub):
    a = add_wt(hub, ".superlite/worktrees/a", "superlite/sessions/a")
    add_wt(hub, ".superlite/sandboxes/x-proto", "superlite/x-proto")
    add_wt(hub, ".claude/worktrees/y", "worktree-y")
    add_wt(hub, "../elsewhere", detach=True)
    inv = rec.inventory(hub, mkreg(row("a", a)))
    by = {Path(e["path"]).name: e for e in inv}
    assert len(inv) == 4
    assert [(by[n]["kind"], by[n]["owner"]) for n in ("a", "x-proto", "y", "elsewhere")] == [
        ("session", "superlite"), ("proto", "superlite"), ("harness", "harness"), ("unknown", "user")]
    assert by["a"]["session"] == "a" and by["y"]["session"] is None
    assert by["y"]["branch"] == "worktree-y" and by["y"]["detached"] is False
    assert all(e["last_seen"] for e in inv)


def test_inventory_harness_hints_win_over_row(hub):
    y = add_wt(hub, ".claude/worktrees/x", "worktree-x")
    r = {"slug": "x", "branch": "worktree-x", "worktree": str(y), "status": "parallel", "owned": False,
         "adopted_branch": True, "harness": True}
    [e] = rec.inventory(hub, mkreg(r))
    assert (e["kind"], e["owner"], e["session"]) == ("harness", "harness", "x")
    assert rec.drift(hub, mkreg(r)) == []  # a registered harness worktree is not "unregistered"


def test_inventory_skips_hub(hub):
    assert rec.inventory(hub, mkreg()) == []


def test_inventory_detached(hub):
    p = add_wt(hub, "../elsewhere", detach=True)
    [e] = rec.inventory(hub, mkreg())
    assert e["branch"] is None and e["detached"] is True
    assert e["head"] == git(p, "rev-parse", "HEAD").strip()


def test_nested_worktree_listed(hub):
    outer = add_wt(hub, ".superlite/worktrees/o", "superlite/sessions/o")
    add_wt(outer, "inner", "superlite/sessions/i")
    inv = rec.inventory(hub, mkreg(row("o", outer)))
    assert sorted(Path(e["path"]).name for e in inv) == ["inner", "o"]


def test_drift_pruned(hub):
    a = add_wt(hub, ".superlite/worktrees/a", "superlite/sessions/a")
    shutil.rmtree(a)
    [d] = rec.drift(hub, mkreg(row("a", a)))
    assert d["class"] == "pruned" and d["slug"] == "a" and Path(d["path"]) == a
    assert "superlite/sessions/a" in d["detail"]


def test_drift_gone(hub):
    [d] = rec.drift(hub, mkreg(row("c")))
    assert d == {"class": "gone", "slug": "c", "path": None, "detail": "branch and worktree missing"}


def test_drift_moved(hub):
    other = add_wt(hub, ".superlite/worktrees/other", "superlite/sessions/b")
    [d] = rec.drift(hub, mkreg(row("b", hub / ".superlite" / "worktrees" / "b")))
    assert d["class"] == "moved" and d["slug"] == "b"
    assert d["detail"].startswith("branch at ") and other.name in d["detail"]


def test_drift_unregistered(hub):
    p = add_wt(hub, ".superlite/worktrees/z", "superlite/sessions/z")
    [d] = rec.drift(hub, mkreg())
    assert d["class"] == "unregistered" and d["slug"] == "-" and Path(d["path"]) == p


def test_drift_clean_registered_and_other_kinds(hub):
    a = add_wt(hub, ".superlite/worktrees/a", "superlite/sessions/a")
    add_wt(hub, ".claude/worktrees/y", "worktree-y")
    assert rec.drift(hub, mkreg(row("a", a))) == []


def test_lost_wip(hub):
    ds = rec.drift(hub, mkreg(row("a", stash_oid="deadbeef" * 5)))
    assert ("lost-wip", "a") in classes(ds)
    git(hub, "branch", "superlite/sessions/a")
    assert classes(rec.drift(hub, mkreg(row("a", stash_oid="deadbeef" * 5)))) == [("lost-wip", "a")]
    git(hub, "update-ref", "refs/superlite/wip/a", "HEAD")
    assert rec.drift(hub, mkreg(row("a", stash_oid="deadbeef" * 5))) == []


def test_lost_wip_not_when_stash_present(hub):
    (hub / "a.txt").write_text("changed\n")
    git(hub, "stash", "push", "-q", "-m", "superlite-session:a")
    oid = git(hub, "rev-parse", "stash@{0}").strip()
    git(hub, "branch", "superlite/sessions/a")
    assert rec.drift(hub, mkreg(row("a", stash_oid=oid))) == []


def test_dangling_active(hub):
    [d] = rec.drift(hub, mkreg(active="ghost"))
    assert d["class"] == "dangling-active" and d["slug"] == "ghost" and d["path"] is None


def test_drift_line_format():
    assert rec.drift_line({"class": "gone", "slug": "c", "path": None, "detail": "branch and worktree missing"}) \
        == "session: drift gone slug=c path=-: branch and worktree missing"
    assert rec.drift_line({"class": "unregistered", "slug": None, "path": "/p", "detail": "d"}) \
        == "session: drift unregistered slug=- path=/p: d"


def test_reconcile_report_changes_nothing(hub):
    a = add_wt(hub, ".superlite/worktrees/a", "superlite/sessions/a")
    shutil.rmtree(a)
    reg = mkreg(row("a", a), row("c"), active="ghost")
    (hub / ".superlite").mkdir(exist_ok=True)
    (hub / ".superlite" / "sessions.json").write_text(json.dumps(reg))
    before = snapshot(hub)
    rec.drift(hub, reg)
    assert snapshot(hub) == before


def test_apply_git_untouched(hub):
    a = add_wt(hub, ".superlite/worktrees/a", "superlite/sessions/a")
    shutil.rmtree(a)
    add_wt(hub, ".superlite/worktrees/z", "superlite/sessions/z")
    reg = mkreg(row("a", a), row("c"), active="ghost")
    before = snapshot(hub, with_file=False)
    new, ds = rec.apply(hub, reg)
    assert snapshot(hub, with_file=False) == before
    assert not (hub / ".superlite" / "sessions.json").exists()
    assert len(ds) == 4


def test_apply_fixes_rows(hub):
    a = add_wt(hub, ".superlite/worktrees/a", "superlite/sessions/a")
    shutil.rmtree(a)
    other = add_wt(hub, ".superlite/worktrees/other", "superlite/sessions/b")
    reg = mkreg(row("a", a), row("b", hub / "nowhere"), row("c"), active="ghost")
    orig = copy.deepcopy(reg)
    new, ds = rec.apply(hub, reg)
    assert reg == orig  # input not mutated
    assert [s["slug"] for s in new["sessions"]] == ["a", "b"]
    assert new["sessions"][0]["status"] == "pruned"
    assert Path(new["sessions"][1]["worktree"]) == other
    assert new["active"] is None
    assert [e["kind"] for e in new["worktrees"]] == ["session"]


def test_apply_gone_keeps_row_with_stash(hub):
    (hub / "a.txt").write_text("changed\n")
    git(hub, "stash", "push", "-q", "-m", "superlite-session:c")
    oid = git(hub, "rev-parse", "stash@{0}").strip()
    new, _ = rec.apply(hub, mkreg(row("c", stash_oid=oid)))
    assert [s["slug"] for s in new["sessions"]] == ["c"]


def test_apply_idempotent(hub):
    a = add_wt(hub, ".superlite/worktrees/a", "superlite/sessions/a")
    shutil.rmtree(a)
    add_wt(hub, ".superlite/worktrees/other", "superlite/sessions/b")
    reg = mkreg(row("a", a), row("b", hub / "nowhere"), row("c"), active="ghost")
    new, _ = rec.apply(hub, reg)
    again, ds = rec.apply(hub, new)
    assert ds == []

    def strip(r):
        r = copy.deepcopy(r)
        for e in r["worktrees"]:
            e.pop("last_seen")
        return r
    assert strip(again) == strip(new)


def test_apply_keeps_parked_rows(hub):
    a = add_wt(hub, ".superlite/worktrees/a", "superlite/sessions/a")
    git(hub, "branch", "superlite/sessions/h")
    reg = mkreg(row("a", a, status="parked"), row("h", status="parked"))
    new, ds = rec.apply(hub, reg)
    assert ds == [] and [s["slug"] for s in new["sessions"]] == ["a", "h"]
    assert new["sessions"][0]["status"] == "parked"
