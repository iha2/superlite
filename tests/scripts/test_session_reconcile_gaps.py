"""Try-out fix round: stable re-apply, leaked registry base, Windows-written paths read from WSL."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import ident  # noqa: E402
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
    for rel, br in ((".superlite/sandboxes/x-proto", "superlite/x-proto"), (".superlite/worktrees/y", "superlite/y")):
        (r / rel).parent.mkdir(parents=True, exist_ok=True)
        git(r, "worktree", "add", "-q", "-b", br, str(r / rel))
    return r


def reg(base="main"):
    return {"v": 2, "base": base, "active": None, "sessions": [], "host_worktree": None}


def test_second_apply_is_byte_identical(hub):
    first, _ = rec.apply(hub, reg())
    second, ds = rec.apply(hub, first)
    assert ds == [] and second == first
    assert len(first["worktrees"]) == 2


def test_changed_entry_gets_a_new_last_seen(hub):
    first, _ = rec.apply(hub, reg())
    first["worktrees"][0]["head"] = "0" * 40
    first["worktrees"][0]["last_seen"] = 1
    second, _ = rec.apply(hub, first)
    assert second["worktrees"][0]["last_seen"] != 1
    assert second["worktrees"][1]["last_seen"] == first["worktrees"][1]["last_seen"]


@pytest.mark.parametrize("leaked", ["superlite/y", "no-such-branch"])
def test_leaked_registry_base_is_reported_then_reset(hub, leaked):
    before = git(hub, "for-each-ref")
    ds = rec.drift(hub, reg(leaked))
    assert [d["class"] for d in ds] == ["base"]
    assert rec.drift_line(ds[0]).startswith("session: drift base slug=- path=-: registry base ")
    new, _ = rec.apply(hub, reg(leaked))
    assert new["base"] == "main"
    assert rec.drift(hub, new) == []
    assert git(hub, "for-each-ref") == before


def test_a_real_trunk_base_is_not_drift(hub):
    git(hub, "branch", "develop")
    assert rec.drift(hub, reg("develop")) == []


@pytest.mark.skipif(os.name == "nt", reason="WSL path forms only arise off Windows")
def test_git_path_keeps_the_windows_tail():
    assert ident.git_path("/mnt/c/r/.git/worktrees/a/C:/Users/me/r/.superlite/worktrees/a") == "C:/Users/me/r/.superlite/worktrees/a"
    assert ident.git_path("C:/Users/me/r") == "C:/Users/me/r"
    assert ident.git_path("/home/me/r") == "/home/me/r"


@pytest.mark.skipif(os.name == "nt" or not os.path.isdir("/mnt/c"), reason="needs a WSL /mnt/c mount")
def test_native_maps_drive_paths_under_wsl():
    assert ident.native("C:/Users") == Path("/mnt/c/Users")
    assert ident.same_path(Path("C:/Users"), Path("/mnt/c/Users"))
    assert ident.native("/home/x") == Path("/home/x")


def test_native_returns_the_normalized_path_on_windows(monkeypatch):
    monkeypatch.setattr(ident, "os", SimpleNamespace(name="nt", path=os.path))
    assert ident.native("/mnt/c/Users/x") == Path("C:/Users/x")
    assert ident.native("D:\\y") == Path("D:\\y")


def test_one_apply_clears_active_of_a_dropped_row(hub):
    r = reg()
    r["active"] = "ghost"
    r["sessions"] = [{"slug": "ghost", "branch": "superlite/ghost", "worktree": str(hub / "nowhere"),
                      "store": str(hub / "s"), "status": "parallel", "owned": True, "run": "open"}]
    first, _ = rec.apply(hub, r)
    assert not any(s["slug"] == "ghost" for s in first["sessions"]) and first["active"] is None
    second, _ = rec.apply(hub, first)
    assert json.dumps(second, sort_keys=True) == json.dumps(first, sort_keys=True)
