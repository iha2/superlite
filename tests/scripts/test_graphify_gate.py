import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).resolve().parents[2]
          / "skills/swarm/scripts/graphify_gate.py")


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def make_repo(tmp_path, n_files, lines=10):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "t")
    git(repo, "config", "user.email", "t@example.com")
    for i in range(n_files):
        (repo / f"f{i}.py").write_text("x = 1\n" * lines)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "init")
    return repo


def fake_bin(tmp_path):
    d = tmp_path / "fakebin"
    d.mkdir()
    if os.name == "nt":
        (d / "graphify.cmd").write_text("@echo off\r\nexit /b 0\r\n")
    else:
        p = d / "graphify"
        p.write_text("#!/bin/sh\nexit 0\n")
        p.chmod(0o755)
    return d


def run_gate(repo, tmp_path, with_fake, env_extra=None, extra_args=()):
    empty = tmp_path / "emptybin"
    empty.mkdir(exist_ok=True)
    git_dir = str(Path(shutil.which("git")).parent)
    parts = ([str(fake_bin(tmp_path))] if with_fake else []) + [str(empty), git_dir]
    env = {k: v for k, v in os.environ.items() if k != "SUPERLITE_ALLOW_INSTALL"}
    env["PATH"] = os.pathsep.join(parts)
    env.update(env_extra or {})
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(repo), *extra_args],
        capture_output=True, text=True, env=env, check=True,
    ).stdout
    return json.loads(out)


def test_small_repo_skips(tmp_path):
    repo = make_repo(tmp_path, 10, lines=50)
    r = run_gate(repo, tmp_path, with_fake=True)
    assert r["use"] is False
    assert r["reason"] == "small repo"


def test_large_repo_installed_uses(tmp_path):
    repo = make_repo(tmp_path, 60)
    r = run_gate(repo, tmp_path, with_fake=True)
    assert r["use"] is True
    assert r["installed"] is True


def test_large_repo_missing_not_allowed(tmp_path):
    repo = make_repo(tmp_path, 60)
    r = run_gate(repo, tmp_path, with_fake=False)
    assert r["use"] is False
    assert r["installed"] is False
    assert r["needs_install"] is True
    assert r["allowed"] is False


def test_env_allows_install(tmp_path):
    repo = make_repo(tmp_path, 60)
    r = run_gate(repo, tmp_path, with_fake=False,
                 env_extra={"SUPERLITE_ALLOW_INSTALL": "graphify"})
    assert r["allowed"] is True
    assert r["needs_install"] is True


def test_counts_tracked_files_only(tmp_path):
    repo = make_repo(tmp_path, 10)
    (repo / ".gitignore").write_text("ignored/\n")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-q", "-m", "ignore")
    (repo / "ignored").mkdir()
    for i in range(80):
        (repo / "ignored" / f"g{i}.py").write_text("y = 2\n")
        (repo / f"untracked{i}.py").write_text("z = 3\n")
    r = run_gate(repo, tmp_path, with_fake=True)
    assert r["use"] is False
    assert r["reason"] == "small repo"
