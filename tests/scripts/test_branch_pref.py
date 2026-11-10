"""Branch-name preference for Claude-created worktrees (D5 revised)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import harness  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    monkeypatch.setenv("SUPERLITE_HOME", str(h))
    return h


def test_pref_unset(home):
    assert harness.branch_pref() is None


def test_set_keeps_other_keys(home):
    home.mkdir()
    (home / "preferences.json").write_text(json.dumps({"agent_specs": True, "open_in_harness": False}))
    harness.set_branch_pref("superlite")
    data = json.loads((home / "preferences.json").read_text())
    assert data == {"agent_specs": True, "open_in_harness": False, "worktree_branch": "superlite"}
    assert harness.branch_pref() == "superlite"


def test_session_branch_kinds():
    assert harness.session_branch("worktree", "a", "worktree-a") == "worktree-a"
    assert harness.session_branch("superlite", "a", "worktree-a") == "superlite/sessions/a"
    assert harness.session_branch("feat/{slug}", "a", "worktree-a") == "feat/a"


@pytest.mark.parametrize("pat", ["feat/fixed", "bad name/{slug}", "x..y/{slug}", "{slug}.lock", "-{slug}", "a/{slug}/", "a:{slug}"])
def test_pattern_requires_slug(home, pat):
    with pytest.raises(ValueError):
        harness.session_branch(pat, "a", "worktree-a")
    with pytest.raises(ValueError):
        harness.set_branch_pref(pat)
    assert harness.branch_pref() is None


def test_cli_status_and_set(home, tmp_path):
    env = {**os.environ, "SUPERLITE_HOME": str(home)}
    env.pop("CLAUDECODE", None)

    def run(*args):
        return subprocess.run([sys.executable, str(SCRIPTS / "session.py"), "branch-name", *args],
                              cwd=tmp_path, env=env, capture_output=True, text=True)

    assert run("status").stdout.strip() == "session: branch-name unset"
    assert run("superlite").stdout.strip() == "session: branch-name superlite (saved)"
    assert run().stdout.strip() == "session: branch-name superlite (preferences)"
    assert run("feat/{slug}").stdout.strip() == "session: branch-name feat/{slug} (saved)"
    bad = run("nope")
    assert bad.returncode == 2 and "{slug}" in bad.stderr


@pytest.mark.parametrize("cmd", ["ensure", "create"])
def test_branch_name_flag_listed(cmd, tmp_path):
    out = subprocess.run([sys.executable, str(SCRIPTS / "session.py"), cmd, "--help"],
                         cwd=tmp_path, capture_output=True, text=True).stdout
    assert "--branch-name" in out
