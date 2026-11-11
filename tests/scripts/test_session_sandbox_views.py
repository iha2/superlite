"""HUD and status show open prototype sandboxes; switching keeps each session's sandbox."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = str(Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts")
sys.path.insert(0, SCRIPTS)
import session_hud  # noqa: E402
import session_registry as R  # noqa: E402
import session_sandboxes  # noqa: E402
import session_switch  # noqa: E402
import state  # noqa: E402

OPEN = ("building", "tryout", "approved")
ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "t@t"}


def _fake_open_entries(row):
    return [e for e in row.get("sandboxes") or [] if e.get("status") in OPEN]


@pytest.fixture(autouse=True)
def entries(monkeypatch):
    """Fake open_entries by default; the real one when SUPERLITE_REAL=1."""
    if os.environ.get("SUPERLITE_REAL") != "1":
        monkeypatch.setattr(session_sandboxes, "open_entries", _fake_open_entries)


def git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True, env=ENV).stdout.strip()


@pytest.fixture
def hub(tmp_path):
    h = tmp_path / "hub"
    h.mkdir()
    git(h, "init", "-q", "-b", "main")
    (h / "a.txt").write_text("a")
    git(h, "add", ".")
    git(h, "commit", "-qm", "c0")
    return h


def put_reg(hub, rows, active="a"):
    (hub / state.DIR_NAME).mkdir(exist_ok=True)
    (hub / state.DIR_NAME / "sessions.json").write_text(json.dumps(
        {"base": "main", "active": active, "sessions": rows}))


def row(hub, **kw):
    return {"slug": "a", "branch": "main", "worktree": str(hub), "status": "active", **kw}


def entry(topic="export", status="building", run_id="20261111-140000-feat"):
    return {"topic": topic, "path": f".superlite/sandboxes/host/{topic}", "run_id": run_id, "status": status}


def test_hud_shows_open_proto(hub):
    put_reg(hub, [row(hub, sandboxes=[entry()])])
    line = session_hud.line_for(hub)
    assert "| proto:export building" in line


def test_hud_no_sandbox_unchanged(hub):
    put_reg(hub, [row(hub)])
    base = session_hud.line_for(hub)
    assert "proto:" not in base and base.endswith("| run:open")
    put_reg(hub, [row(hub, sandboxes=[entry(status="discarded")])])
    assert session_hud.line_for(hub) == base


def test_hud_survives_broken_entries(hub, monkeypatch):
    def boom(r):
        raise ValueError("bad")
    monkeypatch.setattr(session_sandboxes, "open_entries", boom)
    put_reg(hub, [row(hub, sandboxes="junk")])
    assert "proto:" not in session_hud.line_for(hub)


def test_status_lists_sandboxes_with_run_id(hub, capsys, monkeypatch):
    put_reg(hub, [row(hub, sandboxes=[entry(), entry("old", "discarded")])])
    a = type("A", (), {"json": False, "root": None, "cwd": str(hub), "start": str(hub)})()
    monkeypatch.setattr(session_switch, "start_dir", lambda _a: hub)
    assert session_switch.cmd_status(a) == 0
    out = capsys.readouterr().out
    assert "sandbox export building .superlite/sandboxes/host/export run 20261111-140000-feat" in out
    assert "sandbox old discarded" not in out


def test_switch_keeps_each_sessions_sandbox(tmp_path):
    root = tmp_path / "live"
    sl = root / state.DIR_NAME
    for t in ("ta", "tb"):
        (sl / "sandboxes" / "host" / t).mkdir(parents=True)
        (sl / "sandboxes" / "host" / t / "f.py").write_text(t)
    store_a, store_b = tmp_path / "store_a", tmp_path / "store_b"
    (store_b).mkdir()
    (store_b / "feature.json").write_text(json.dumps({"topic": "tb", "status": "building"}))
    (sl / "feature.json").write_text(json.dumps({"topic": "ta", "status": "building"}))
    R.park_run_state(sl, store_a)
    R.restore_run_state(store_b, sl)
    assert (sl / "sandboxes" / "host" / "ta" / "f.py").read_text() == "ta"
    assert (sl / "sandboxes" / "host" / "tb" / "f.py").read_text() == "tb"
    assert json.loads((sl / "feature.json").read_text())["topic"] == "tb"
    assert json.loads((store_a / "feature.json").read_text())["topic"] == "ta"
    rows = [{"slug": "a", "sandboxes": [entry("ta")]}, {"slug": "b", "sandboxes": [entry("tb")]}]
    assert [e["topic"] for e in session_sandboxes.open_entries(rows[1])] == ["tb"]
