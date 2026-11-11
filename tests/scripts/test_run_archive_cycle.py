"""Seam: ensure -> ledger -> finish -> ensure -> resume -> close/discard, through superlite-cli."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

CLI = Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts" / "superlite-cli"
_GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"
BASH = _GIT_BASH if os.name == "nt" and os.path.exists(_GIT_BASH) else shutil.which("bash")


def cli(repo, *args, home):
    env = dict(os.environ, SUPERLITE_HOME=str(home), GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    env.pop("SUPERLITE_NO_AUTO_SESSION", None)
    cp = subprocess.run([BASH, str(CLI), *map(str, args)], cwd=repo, env=env, capture_output=True, timeout=180)
    cp.out, cp.err = cp.stdout.decode("utf-8"), cp.stderr.decode("utf-8")
    return cp


def git(cwd, *a):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd,
                          capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path):
    r = tmp_path.resolve() / "app"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    (r / ".gitignore").write_text(".superlite/\n")
    (r / "app.py").write_text("print('hi')\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    return r


@pytest.fixture
def home(tmp_path):
    return tmp_path / "home"


def archives(hub):
    d = hub / ".superlite" / "runs"
    return sorted(p.name for p in d.iterdir()) if d.is_dir() else []


def ledger(repo, text):
    sl = repo / ".superlite"
    sl.mkdir(exist_ok=True)
    (sl / "progress.md").write_text(text)
    (sl / "incidents.jsonl").write_text("")


def live(repo):
    return [p.name for p in (repo / ".superlite").glob("progress.md")]


def test_cycle(repo, home):
    ok = lambda cp: (cp.returncode == 0) or pytest.fail(cp.out + cp.err)
    ok(cli(repo, "session", "ensure", "--slug", "first", home=home))
    ledger(repo, "Ruling: a - b - c\n")
    ok(cli(repo, "finish", "--ledger", ".superlite/progress.md", "--log", ".superlite/incidents.jsonl", home=home))
    (a,) = archives(repo)
    assert a.endswith("-first") and live(repo) == []

    ok(cli(repo, "session", "ensure", "--slug", "second", home=home))
    ledger(repo, "second work\n")
    cp = cli(repo, "session", "ensure", "--slug", "second", home=home)
    ok(cp)
    assert "already open here" in cp.out
    names = archives(repo)
    assert names == [a]
    assert live(repo) == ["progress.md"]

    ok(cli(repo, "session", "close", "--slug", "first", home=home))
    ok(cli(repo, "session", "discard", "--slug", "second", home=home))
    assert archives(repo) == names


def test_worktree_session_archive_survives_close(repo, home):
    cp = cli(repo, "session", "create", "--slug", "wt", "--worktree", home=home)
    assert cp.returncode == 0, cp.out + cp.err
    wt = Path(git(repo, "worktree", "list", "--porcelain").split("worktree ")[2].splitlines()[0])
    ledger(wt, "Ruling: a - b - c\n")
    cp = cli(wt, "finish", "--ledger", ".superlite/progress.md", "--log", ".superlite/incidents.jsonl", home=home)
    assert cp.returncode == 0, cp.out + cp.err
    (a,) = archives(repo)
    assert a.endswith("-wt") and (repo / ".superlite" / "runs" / a / "progress.md").is_file()
    cp = cli(repo, "session", "close", "--slug", "wt", home=home)
    assert cp.returncode == 0, cp.out + cp.err
    assert archives(repo) == [a]
