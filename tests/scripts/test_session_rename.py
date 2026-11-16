"""session rename: slug, branch, store folder and active pointer move together; the tree is untouched."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"
SESSION = SCRIPTS / "session.py"


def run(*args, cwd=None):
    e = dict(os.environ, PYTHONIOENCODING="utf-8")
    e.pop("SUPERLITE_NO_AUTO_SESSION", None)
    cp = subprocess.run([sys.executable, str(SESSION), *map(str, args)], capture_output=True, cwd=cwd, env=e,
                        timeout=120)
    cp.out, cp.err = cp.stdout.decode("utf-8"), cp.stderr.decode("utf-8")
    return cp


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path):
    r = tmp_path.resolve() / "app"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "t")
    (r / ".gitignore").write_text(".superlite/\n")
    (r / "app.py").write_text("print('hi')\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    return r


def reg_of(r):
    return json.loads((r / ".superlite" / "sessions.json").read_text(encoding="utf-8"))


def started(repo, slug="login-fix", *extra):
    return run("ensure", "--slug", slug, *extra, "--root", repo, cwd=repo)


def test_rename_dirty_keeps_changes(repo):
    started(repo)
    (repo / "app.py").write_text("print('dirty')\n")
    (repo / "new.txt").write_text("x")
    cp = run("rename", "--slug", "login-fix", "--to", "login-fix-3", "--root", repo, cwd=repo)
    assert cp.returncode == 0, cp.err
    assert (repo / "app.py").read_text() == "print('dirty')\n"
    assert (repo / "new.txt").exists()
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "superlite/sessions/login-fix-3"
    reg = reg_of(repo)
    assert [s["slug"] for s in reg["sessions"]] == ["login-fix-3"]
    assert reg["sessions"][0]["branch"] == "superlite/sessions/login-fix-3"
    assert reg["active"] == "login-fix-3"


def test_rename_prints_line(repo):
    started(repo)
    cp = run("rename", "--slug", "login-fix", "--to", "login-fix-3", "--root", repo, cwd=repo)
    assert cp.out.strip() == "session: renamed login-fix to login-fix-3 (branch superlite/sessions/login-fix-3)"


def test_rename_taken_refused(repo):
    started(repo)
    f = repo / ".superlite" / "sessions.json"
    reg = json.loads(f.read_text(encoding="utf-8"))
    reg["sessions"].append({"slug": "login-fix-3", "branch": "superlite/sessions/other", "status": "parked"})
    f.write_text(json.dumps(reg), encoding="utf-8")
    cp = run("rename", "--slug", "login-fix", "--to", "login-fix-3", "--root", repo, cwd=repo)
    assert cp.returncode == 2
    assert "session: rename refused: login-fix-3 is taken" in cp.err


def test_rename_branch_taken_refused(repo):
    started(repo)
    git(repo, "branch", "superlite/sessions/login-fix-3")
    cp = run("rename", "--slug", "login-fix", "--to", "login-fix-3", "--root", repo, cwd=repo)
    assert cp.returncode == 2
    assert "session: rename refused: login-fix-3 is taken" in cp.err
    assert reg_of(repo)["sessions"][0]["slug"] == "login-fix"


def test_rename_unknown_refused(repo):
    started(repo)
    cp = run("rename", "--slug", "nope", "--to", "other", "--root", repo, cwd=repo)
    assert cp.returncode == 2
    assert "session: rename refused: no session nope" in cp.err


def test_rename_custom_branch(repo):
    git(repo, "checkout", "-q", "-b", "feat/login-fix")
    cp = started(repo, "login-fix", "--branch", "feat/login-fix")
    assert cp.returncode == 0, cp.err
    cp = run("rename", "--slug", "login-fix", "--to", "login-fix-3", "--root", repo, cwd=repo)
    assert cp.returncode == 0, cp.err
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "feat/login-fix-3"
    assert reg_of(repo)["sessions"][0]["branch"] == "feat/login-fix-3"


def test_rename_active_pointer(repo):
    started(repo)
    run("rename", "--slug", "login-fix", "--to", "login-fix-3", "--root", repo, cwd=repo)
    assert reg_of(repo)["active"] == "login-fix-3"


def test_rename_origin_branch_taken_refused(repo):
    assert started(repo).returncode == 0
    sha = git(repo, "rev-parse", "main").strip()
    git(repo, "update-ref", "refs/remotes/origin/superlite/sessions/fresh", sha)
    before = reg_of(repo)
    r = run("rename", "--slug", "login-fix", "--to", "fresh", "--root", repo, cwd=repo)
    assert r.returncode == 2, r.err
    assert reg_of(repo) == before


def test_rename_registry_write_failure_rolls_back(repo, monkeypatch, capsys):
    started(repo)
    sys.path.insert(0, str(SCRIPTS))
    import argparse
    import session_registry as R
    import session_rename
    store = R.store_path(repo, "login-fix")
    store.mkdir(parents=True, exist_ok=True)
    (store / "marker").write_text("x")
    before = reg_of(repo)

    def boom(root, reg):
        raise OSError("disk full")

    monkeypatch.setattr(R, "write", boom)
    rc = session_rename.cmd_rename(argparse.Namespace(slug="login-fix", to="login-fix-3", root=str(repo)))
    assert rc == 1
    assert "rolling back" in capsys.readouterr().err
    assert git(repo, "branch", "--list", "superlite/sessions/login-fix").strip().lstrip("* ")
    assert not git(repo, "branch", "--list", "superlite/sessions/login-fix-3").strip()
    assert (store / "marker").exists() and not R.store_path(repo, "login-fix-3").exists()
    assert reg_of(repo) == before
