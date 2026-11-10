"""session reconcile / report-only pre-check / status inventory (CLI wiring; reconcile logic is T1's)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import session  # noqa: E402

DRIFT = {"class": "pruned", "slug": "a", "path": "/x/a", "detail": "worktree missing, branch superlite/sessions/a present"}
WT = {"path": "/x/a", "branch": "superlite/sessions/a", "head": "a" * 40, "detached": False, "kind": "session",
      "owner": "superlite", "session": "a", "last_seen": 1}
WT2 = {"path": "/y", "branch": None, "head": "b" * 40, "detached": True, "kind": "unknown", "owner": "user",
       "session": None, "last_seen": 1}
REAL = os.environ.get("SUPERLITE_REAL") == "1"


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def make_fake(drifts=(), wts=(), boom=None):
    f = types.ModuleType("session_reconcile")

    def drift(root, reg):
        if boom:
            raise boom
        return list(drifts)

    def apply(root, reg):
        return {**reg, "worktrees": list(wts)}, list(drifts)

    f.drift, f.apply = drift, apply
    f.inventory = lambda root, reg: list(wts)
    f.drift_line = lambda d: f"session: drift {d['class']} slug={d['slug'] or '-'} path={d['path'] or '-'}: {d['detail']}"
    return f


@pytest.fixture
def fake(monkeypatch):
    """The fake reconcile module by default; the real one when SUPERLITE_REAL=1."""
    def install(**kw):
        if REAL:
            pytest.skip("canned drift needs the fake")
        monkeypatch.setitem(sys.modules, "session_reconcile", make_fake(**kw))
    return install


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = tmp_path / "app"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "t")
    (r / ".gitignore").write_text(".superlite/\n")
    (r / "app.py").write_text("x\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    monkeypatch.setenv("SUPERLITE_HOME", str(tmp_path / "_home"))
    return r


def run(repo, *args, capfd):
    capfd.readouterr()
    rc = session.main([*args, "--root", str(repo)])
    cap = capfd.readouterr()
    return rc, cap.out, cap.err


def reg_bytes(repo):
    p = repo / ".superlite" / "sessions.json"
    return p.read_bytes() if p.exists() else b""


def test_reconcile_ok(repo, fake, capfd):
    fake(wts=[WT, WT2])
    rc, out, _ = run(repo, "reconcile", capfd=capfd)
    assert rc == 0
    assert out == "session: reconcile ok (0 drift, 2 worktrees)\n"


def test_reconcile_report_only(repo, fake, capfd):
    fake(drifts=[DRIFT], wts=[WT])
    run(repo, "ensure", "--slug", "aa", capfd=capfd)
    before = reg_bytes(repo)
    rc, out, _ = run(repo, "reconcile", capfd=capfd)
    assert rc == 0
    lines = out.splitlines()
    assert lines[0].startswith("session: drift pruned slug=a path=")
    assert lines[-1] == "session: reconcile 1 drift (report only; rerun with --apply)"
    assert reg_bytes(repo) == before


def test_reconcile_apply(repo, fake, capfd):
    fake(drifts=[DRIFT], wts=[WT, WT2])
    rc, out, _ = run(repo, "reconcile", "--apply", capfd=capfd)
    assert rc == 0
    lines = out.splitlines()
    assert lines[0].startswith("session: drift pruned")
    assert lines[-1] == "session: reconcile applied 1 fix, 2 worktrees recorded"
    reg = json.loads(reg_bytes(repo))
    assert [w["path"] for w in reg["worktrees"]] == ["/x/a", "/y"]


def test_reconcile_root_flag(repo, tmp_path, fake, capfd, monkeypatch):
    fake(wts=[WT])
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    rc, out, _ = run(repo, "reconcile", "--apply", capfd=capfd)
    assert rc == 0 and "1 worktrees recorded" in out
    assert (repo / ".superlite" / "sessions.json").exists() and not (elsewhere / ".superlite").exists()


def test_precheck_prints_drift_before_ensure(repo, fake, capfd):
    fake(drifts=[DRIFT])
    rc, out, _ = run(repo, "ensure", "--slug", "aa", capfd=capfd)
    lines = out.splitlines()
    assert rc == 0
    assert lines[0].startswith("session: drift pruned slug=a")
    assert any(ln.startswith("SESSION worktree=") for ln in lines[1:])


@pytest.mark.parametrize("cmd", ["ensure", "use", "status"])
def test_precheck_never_blocks(repo, fake, capfd, monkeypatch, cmd):
    fake()
    args = [cmd] + (["--slug", "aa"] if cmd != "status" else [])
    run(repo, "ensure", "--slug", "aa", capfd=capfd)
    expected = run(repo, *args, capfd=capfd)
    monkeypatch.setitem(sys.modules, "session_reconcile", make_fake(boom=RuntimeError("kaput")))
    rc, out, err = run(repo, *args, capfd=capfd)
    assert rc == expected[0]
    assert out == expected[1]
    assert err.count("\n") == 1 and "kaput" in err


def test_precheck_survives_missing_module(repo, capfd, monkeypatch):
    monkeypatch.setitem(sys.modules, "session_reconcile", None)  # import raises ImportError
    rc, out, err = run(repo, "ensure", "--slug", "aa", capfd=capfd)
    assert rc == 0 and "SESSION worktree=" in out and "session: drift" not in out
    assert err.count("\n") == 1


def test_existing_output_unchanged(repo, fake, capfd):
    fake()
    outs = {}
    for name, args in (("ensure", ["ensure", "--slug", "aa"]), ("use", ["use", "--slug", "aa"]), ("status", ["status"])):
        rc, out, err = run(repo, *args, capfd=capfd)
        assert rc == 0 and err == ""
        outs[name] = out
    assert outs["ensure"].startswith("session: active aa") and "SESSION worktree=" in outs["ensure"]
    assert all("session: drift" not in o and "session: worktree" not in o for o in outs.values())
    assert outs["status"].splitlines()[0].startswith("session: active=aa")
    assert len(outs["status"].splitlines()) == 2


def test_status_lists_worktrees(repo, fake, capfd):
    fake(wts=[WT, WT2])
    run(repo, "ensure", "--slug", "aa", capfd=capfd)
    rc, out, _ = run(repo, "status", capfd=capfd)
    lines = out.splitlines()
    assert rc == 0 and lines[0].startswith("session: active=aa")
    assert lines[-2] == "session: worktree session superlite /x/a superlite/sessions/a"
    assert lines[-1] == f"session: worktree unknown user /y detached {'b' * 7}"


def test_status_json_stays_json(repo, fake, capfd):
    fake(drifts=[DRIFT], wts=[WT])
    run(repo, "ensure", "--slug", "aa", capfd=capfd)
    rc, out, _ = run(repo, "status", "--json", capfd=capfd)
    assert rc == 0 and json.loads(out)["v"] == 2


def test_real_module_reconcile_and_apply(repo, capfd):
    sys.modules.pop("session_reconcile", None)
    git(repo, "worktree", "add", "-q", "-b", "side", str(repo.parent / "side"))
    run(repo, "ensure", "--slug", "aa", capfd=capfd)
    rc, out, _ = run(repo, "reconcile", capfd=capfd)
    assert rc == 0 and out.startswith("session: reconcile ok")
    before = reg_bytes(repo)
    rc, out, _ = run(repo, "reconcile", "--apply", capfd=capfd)
    assert rc == 0 and "reconcile applied 0 fixes" in out
    reg = json.loads(reg_bytes(repo))
    assert [w["branch"] for w in reg["worktrees"]] == ["side"] and reg["sessions"][0]["slug"] == "aa"
    assert reg_bytes(repo) != b"" and before != b""
    rc, out, err = run(repo, "status", capfd=capfd)
    assert rc == 0 and "session: worktree " in out and err == ""
