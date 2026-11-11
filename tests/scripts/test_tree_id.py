"""tree_id.tree_hash: a 16-hex working-tree fingerprint over HEAD, the diff against HEAD, and untracked not-ignored files."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import tree_id  # noqa: E402


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@t")
    git(tmp_path, "config", "user.name", "t")
    git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / "a.txt").write_text("one\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("*.log\n", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def test_stable(repo):
    first = tree_id.tree_hash(str(repo))
    assert re.fullmatch(r"[0-9a-f]{16}", first)
    assert tree_id.tree_hash(str(repo)) == first


def test_edit_changes(repo):
    before = tree_id.tree_hash(str(repo))
    (repo / "a.txt").write_text("two\n", encoding="utf-8")
    assert tree_id.tree_hash(str(repo)) != before


def test_untracked_changes(repo):
    before = tree_id.tree_hash(str(repo))
    (repo / "ignored.log").write_text("noise\n", encoding="utf-8")
    assert tree_id.tree_hash(str(repo)) == before
    (repo / "new.txt").write_text("hello\n", encoding="utf-8")
    assert tree_id.tree_hash(str(repo)) != before


def test_not_repo_empty(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert tree_id.tree_hash(str(plain)) == ""
