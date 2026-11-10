"""Registry _normalize keeps the worktree inventory written by session_reconcile."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import session_registry  # noqa: E402


def test_worktrees_survive_write_read(tmp_path):
    session_registry.write(tmp_path, {"worktrees": [{"path": "a"}, "junk", {"path": "b"}]})
    assert session_registry.read(tmp_path)["worktrees"] == [{"path": "a"}, {"path": "b"}]


def test_non_list_worktrees_dropped(tmp_path):
    session_registry.write(tmp_path, {"worktrees": {"path": "a"}})
    assert "worktrees" not in session_registry.read(tmp_path)


def test_absent_worktrees_has_no_key(tmp_path):
    session_registry.write(tmp_path, {})
    reg = session_registry.read(tmp_path)
    assert "worktrees" not in reg and reg["v"] == 2


def test_detached_row_keeps_branch_none(tmp_path):
    row = {"slug": "d", "branch": None, "detached": True, "head": "abc123"}
    session_registry.write(tmp_path, {"sessions": [row, {"slug": "n", "branch": None}]})
    rows = {s["slug"]: s for s in session_registry.read(tmp_path)["sessions"]}
    assert rows["d"]["branch"] is None and rows["d"]["head"] == "abc123"
    assert rows["n"]["branch"] == "superlite/sessions/n"  # a normal row still gets its default
