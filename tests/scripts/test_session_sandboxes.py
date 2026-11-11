"""session_sandboxes: the per-session list of prototype sandboxes on the session row."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import base  # noqa: E402
import session_registry  # noqa: E402
import session_sandboxes as ss  # noqa: E402

PATH = ".superlite/sandboxes/host/export"
RUN = "20261111-140000-feat"


def repo(tmp_path, registered=True):
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=r, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "i"], cwd=r, check=True)
    if registered:
        session_registry.write(r, {"sessions": [{"slug": "host"}], "active": "host"})
    return r


def row(r):
    return session_registry.find_row(session_registry.read(r), "host")


def test_record_creates_entry(tmp_path):
    r = repo(tmp_path)
    assert ss.record(r, "export", path=PATH, run_id=RUN, status="building") is True
    e = row(r)["sandboxes"][0]
    assert {k: e[k] for k in ("topic", "path", "run_id", "status", "ended")} == {
        "topic": "export", "path": PATH, "run_id": RUN, "status": "building", "ended": None}
    assert e["started"]


def test_record_upserts_by_topic(tmp_path):
    r = repo(tmp_path)
    ss.record(r, "export", path=PATH, run_id=RUN, status="building")
    ss.record(r, "other", path="p2", status="building")
    started = row(r)["sandboxes"][0]["started"]
    assert ss.record(r, "export", status="discarded") is True
    entries = row(r)["sandboxes"]
    assert len(entries) == 2
    e = entries[0]
    assert (e["status"], e["path"], e["run_id"], e["started"]) == ("discarded", PATH, RUN, started)
    assert e["ended"] and entries[1]["ended"] is None


def test_record_without_session_false(tmp_path):
    r = repo(tmp_path, registered=False)
    assert ss.record(r, "export", status="building") is False
    d = tmp_path / "plain"
    d.mkdir()
    assert ss.record(d, "export", status="building") is False


def test_record_under_lock(tmp_path, monkeypatch):
    r = repo(tmp_path)
    seen = []
    real = base.locked_update
    monkeypatch.setattr(base, "locked_update", lambda p, fn: seen.append(Path(p).name) or real(p, fn))
    assert ss.record(r, "export", status="building") is True
    assert seen == [session_registry.FILE]
    assert not list((r / ".superlite").glob("*.tmp"))


def test_record_under_held_registry_lock(tmp_path):
    r = repo(tmp_path)
    with session_registry.registry_lock(r):
        assert ss.record(r, "export", status="building") is True


def test_record_corrupt_registry_false(tmp_path):
    r = repo(tmp_path)
    p = session_registry._path(r)
    p.write_text("{not json", encoding="utf-8")
    assert ss.record(r, "export", status="building") is False
    assert p.read_text(encoding="utf-8") == "{not json"


def test_open_entries():
    r = {"sandboxes": [{"topic": "a", "status": "building"}, {"topic": "b", "status": "discarded"}, {"topic": "c", "status": "closed"}]}
    assert ss.open_entries(r) == [{"topic": "a", "status": "building"}]
    assert ss.open_entries({}) == []
    assert ss.open_entries({"sandboxes": "x"}) == []
