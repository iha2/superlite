"""Archive manifest for closed sessions: path, write, read, slugs."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "swarm" / "scripts"

sys.path.insert(0, str(SCRIPTS))

import session_archive  # noqa: E402

NOW = 1794249523869


def test_path_for(tmp_path):
    assert session_archive.path_for(tmp_path, "a") == tmp_path / ".superlite" / "session-store" / "a" / "session.json"


def test_write_fields(tmp_path):
    row = {"slug": "a", "branch": "superlite/sessions/a"}
    path = session_archive.write(tmp_path, row, "refs/superlite/wip/a", NOW)
    assert path == session_archive.path_for(tmp_path, "a")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"slug": "a", "branch": "superlite/sessions/a",
                    "closed_at": NOW, "wip_ref": "refs/superlite/wip/a"}
    assert b"\r\n" not in path.read_bytes()


def test_read_round_trip(tmp_path):
    row = {"slug": "a", "branch": "superlite/sessions/a"}
    session_archive.write(tmp_path, row, None, NOW)
    assert session_archive.read(tmp_path, "a") == {"slug": "a", "branch": "superlite/sessions/a",
                                                   "closed_at": NOW, "wip_ref": None}


def test_read_missing_is_none(tmp_path):
    assert session_archive.read(tmp_path, "nope") is None


def test_corrupt_is_none(tmp_path):
    path = session_archive.path_for(tmp_path, "a")
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert session_archive.read(tmp_path, "a") is None


def test_slugs_sorted_and_filtered(tmp_path):
    session_archive.write(tmp_path, {"slug": "b"}, None, NOW)
    session_archive.write(tmp_path, {"slug": "a"}, None, NOW)
    (tmp_path / ".superlite" / "session-store" / "empty").mkdir(parents=True)
    assert session_archive.slugs(tmp_path) == ["a", "b"]


def test_slugs_no_store_is_empty(tmp_path):
    assert session_archive.slugs(tmp_path) == []


def test_write_is_atomic(tmp_path, monkeypatch):
    def boom(src, dst):
        raise OSError("simulated crash during replace")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        session_archive.write(tmp_path, {"slug": "a"}, None, NOW)
    target = session_archive.path_for(tmp_path, "a")
    assert not target.exists()
    leftovers = list(target.parent.iterdir()) if target.parent.exists() else []
    assert leftovers == []
