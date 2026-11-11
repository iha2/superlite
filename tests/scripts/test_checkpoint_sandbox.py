"""checkpoint and review_packet see the open prototype sandbox (a git-ignored folder) when a task owns it."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import checkpoint as cp  # noqa: E402
import review_packet  # noqa: E402

SB = ".superlite/sandboxes/host/x"


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = tmp_path.resolve() / "app"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "t")
    (r / ".gitignore").write_text(".superlite/\n")
    (r / "a.txt").write_text("a\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    (r / SB / "src").mkdir(parents=True)
    (r / SB / "src" / "m.py").write_text("x = 1\n")
    (r / SB / "node_modules").mkdir()
    (r / SB / "node_modules" / "skip.js").write_text("1\n")
    (r / ".superlite" / "stable.json").write_text("{}\n")
    (r / ".superlite" / "feature.json").write_text(json.dumps({"status": "building", "sandbox": str(r / SB)}))
    monkeypatch.chdir(r)
    monkeypatch.setattr(cp, "PLAN_IGNORE", [])
    return r


def test_checkpoint_saves_owned_sandbox_files(repo):
    files = cp.changed_files(cp.claims([f"{SB}/"]))
    assert files == [f"{SB}/src/m.py"]
    sha = cp.save_checkpoint("T1", "proto", files, "plan.md")
    tree = git(repo, "ls-tree", "-r", "--name-only", sha).split()
    assert f"{SB}/src/m.py" in tree
    assert not any(t.startswith(".superlite/stable") for t in tree)


def test_sandbox_files_never_unowned(repo):
    assert cp.changed_files(cp.claims(["src/other.py"])) == []
    assert cp.changed_files() == []


def test_run_bookkeeping_still_ignored(repo):
    assert cp.hard_ignored(".superlite/stable.json")
    assert cp.hard_ignored(".superlite/sandboxes/host/x/__pycache__/m.pyc")
    assert not cp.hard_ignored(f"{SB}/src/m.py")
    files = cp.changed_files(cp.claims([".superlite/", f"{SB}/"]))
    assert ".superlite/stable.json" not in files


def test_packet_lists_sandbox_files(repo):
    lines = review_packet.sandbox_section(repo, repo / ".superlite")
    text = "\n".join(lines)
    assert "## Sandbox files" in text and f"- {SB}/src/m.py" in text
    assert "skip.js" not in text


def test_packet_no_section_without_sandbox(repo):
    (repo / ".superlite" / "feature.json").unlink()
    assert review_packet.sandbox_section(repo, repo / ".superlite") == []


def test_sandbox_pyc_never_saved(repo):
    (repo / SB / "src" / "m.pyc").write_bytes(b"\0")
    assert cp.changed_files(cp.claims([f"{SB}/"])) == [f"{SB}/src/m.py"]
