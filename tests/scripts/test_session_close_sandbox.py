"""Session close discards an open sandbox first; reconcile reports sandbox drift (reads only)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import proto_discard  # noqa: E402
import session_archive  # noqa: E402
import session_ensure as E  # noqa: E402
import session_reconcile as rec  # noqa: E402
import session_sandboxes  # noqa: E402


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def _open_entries(row):
    return [e for e in row.get("sandboxes") or [] if e.get("status") in ("building", "tryout", "approved")]


@pytest.fixture
def calls(monkeypatch):
    log = []
    if os.environ.get("SUPERLITE_REAL") != "1":
        def fake_discard(sl, reason="discard"):
            log.append(("discard", reason))
            return {"status": "discarded", "discard_reason": reason}
        monkeypatch.setattr(proto_discard, "discard_open", fake_discard, raising=False)
        monkeypatch.setattr(session_sandboxes, "open_entries", _open_entries, raising=False)
    real = session_archive.write

    def spy(*a, **k):
        log.append(("archive",))
        return real(*a, **k)
    monkeypatch.setattr(session_archive, "write", spy)
    return log


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


def test_close_with_open_sandbox_discards_first(repo, calls):
    E.cmd_ensure(Namespace(slug="s1", root=str(repo), branch=None, move=None, untracked=None))
    rc = E.cmd_close(Namespace(slug="s1", root=str(repo), keep_branch=False, untracked=None))
    assert rc == 0
    assert calls[0] == ("discard", "session close")
    assert calls.index(("archive",)) > 0


def test_close_discard_failure_does_not_block(repo, calls, monkeypatch, capsys):
    def boom(sl, reason="discard"):
        raise OSError("locked")
    monkeypatch.setattr(proto_discard, "discard_open", boom, raising=False)
    E.cmd_ensure(Namespace(slug="s1", root=str(repo), branch=None, move=None, untracked=None))
    assert E.cmd_close(Namespace(slug="s1", root=str(repo), keep_branch=False, untracked=None)) == 0
    assert "could not discard" in capsys.readouterr().err


def _reg(sandboxes):
    return {"sessions": [{"slug": "s1", "branch": "main", "status": "active", "sandboxes": sandboxes}]}


def _sb_drift(repo, reg):
    return [d for d in rec.drift(repo, reg) if d["class"].startswith("sandbox")]


def test_reconcile_orphan_folder(repo, calls):
    f = repo / ".superlite" / "sandboxes" / "s1" / "export"
    f.mkdir(parents=True)
    ds = _sb_drift(repo, _reg([]))
    assert [d["class"] for d in ds] == ["sandbox-orphan"]
    assert f.is_dir()


def test_reconcile_missing_folder(repo, calls):
    e = {"topic": "export", "path": ".superlite/sandboxes/s1/export", "status": "building"}
    ds = _sb_drift(repo, _reg([e]))
    assert [d["class"] for d in ds] == ["sandbox-missing"]
    assert rec.drift_line(ds[0]).startswith("session: drift sandbox-missing")


def test_reconcile_consistent_is_quiet(repo, calls):
    (repo / ".superlite" / "sandboxes" / "s1" / "export").mkdir(parents=True)
    e = {"topic": "export", "path": ".superlite/sandboxes/s1/export", "status": "building"}
    assert _sb_drift(repo, _reg([e])) == []
