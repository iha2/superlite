"""session ensure archives leftover run files into .superlite/runs/."""
from __future__ import annotations

import os
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SESSION = ROOT / "skills" / "swarm" / "scripts" / "session.py"


def run(*args, cwd=None, env=None):
    e = dict(os.environ, PYTHONIOENCODING="utf-8")
    e.pop("SUPERLITE_NO_AUTO_SESSION", None)
    e.update(env or {})
    cp = subprocess.run([sys.executable, str(SESSION), *map(str, args)], capture_output=True, cwd=cwd, env=e,
                        timeout=120)
    cp.out = cp.stdout.decode("utf-8")
    cp.err = cp.stderr.decode("utf-8")
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


def runs(r):
    d = r / ".superlite" / "runs"
    return sorted(p for p in d.iterdir()) if d.is_dir() else []


def test_open_here_reentry_does_not_snapshot_active_run(repo):
    assert run("ensure", "--slug", "demo", cwd=repo).returncode == 0
    sl = repo / ".superlite"
    (sl / "progress.md").write_text("p\n")
    (sl / "orchestrator.json").write_text('{"mode":"human","approved":true}\n')
    marker = (sl / "current-run.json").read_text()
    cp = run("ensure", "--slug", "demo", cwd=repo)
    lines = cp.out.strip().splitlines()
    assert cp.returncode == 0, cp.err
    assert lines[0] == "session: demo already open here"
    assert lines[-1].startswith("SESSION ")
    assert (sl / "progress.md").read_text() == "p\n"
    assert (sl / "orchestrator.json").read_text() == '{"mode":"human","approved":true}\n'
    assert (sl / "current-run.json").read_text() == marker
    assert runs(repo) == []


@pytest.mark.parametrize("with_branch", [False, True])
def test_ensure_preserves_active_run(repo, with_branch):
    assert run("ensure", "--slug", "demo", cwd=repo).returncode == 0
    sl = repo / ".superlite"
    progress = sl / "progress.md"
    progress.write_text("current run\n")
    orchestrator = sl / "orchestrator.json"
    orchestrator.write_text('{"mode":"human","approved":false}\n')
    args = ["ensure", "--slug", "demo"]
    if with_branch:
        args.extend(["--branch", git(repo, "branch", "--show-current").strip()])

    cp = run(*args, cwd=repo)

    assert cp.returncode == 0, cp.err
    assert "already open here" in cp.out
    assert progress.read_text() == "current run\n"
    assert orchestrator.read_text() == '{"mode":"human","approved":false}\n'
    assert runs(repo) == []


def test_unregistered_leftovers_archived(repo):
    sl = repo / ".superlite"
    sl.mkdir()
    (sl / "progress.md").write_text("stray\n")
    cp = run("ensure", "--slug", "new", cwd=repo)
    assert cp.returncode == 0, cp.err
    assert cp.out.strip().splitlines()[-1].startswith("SESSION ")
    (d,) = runs(repo)
    assert d.name.endswith("-new-unfinished")
    assert (d / "progress.md").read_text() == "stray\n"
    assert (sl / "progress.md").read_text() == "stray\n"
    marker = json.loads((sl / "current-run.json").read_text())
    assert marker["topic"] == "new" and marker["slug"] == "new"
    assert marker["id"] == d.name.removesuffix("-unfinished")
    assert f"run: copied leftovers to .superlite/runs/{d.name}" in cp.out
    assert git(repo, "branch", "--show-current").strip() != "main"


def test_different_topic_copies_unfinished_state_and_keeps_it(repo):
    assert run("ensure", "--slug", "old", cwd=repo).returncode == 0
    sl = repo / ".superlite"
    (sl / "progress.md").write_text("old run\n")
    marker = json.loads((sl / "current-run.json").read_text())
    branch = git(repo, "branch", "--show-current").strip()
    cp = run("ensure", "--slug", "new", "--branch", branch, cwd=repo)
    assert cp.returncode == 0, cp.err
    archives = runs(repo)
    assert len(archives) == 1 and archives[0].name == marker["id"] + "-unfinished"
    assert (archives[0] / "progress.md").read_text() == "old run\n"
    assert (sl / "progress.md").read_text() == "old run\n"
    updated = json.loads((sl / "current-run.json").read_text())
    assert updated["topic"] == "new" and updated["slug"] == "old"
    assert updated["id"] != marker["id"]


def test_legacy_orchestrator_gets_marker_without_snapshot(repo):
    sl = repo / ".superlite"
    sl.mkdir()
    (sl / "orchestrator.json").write_text('{"mode":"human","approved":true}\n')
    cp = run("ensure", "--slug", "legacy", cwd=repo)
    assert cp.returncode == 0, cp.err
    assert runs(repo) == []
    assert (sl / "orchestrator.json").read_text() == '{"mode":"human","approved":true}\n'
    marker = json.loads((sl / "current-run.json").read_text())
    assert marker["topic"] == "legacy" and marker["slug"] == "legacy"


def test_branch_registration_copies_and_keeps_leftovers(repo):
    sl = repo / ".superlite"
    sl.mkdir()
    (sl / "progress.md").write_text("stray\n")
    cp = run("ensure", "--slug", "branch-run", "--branch", "main", cwd=repo)
    assert cp.returncode == 0, cp.err
    assert (sl / "progress.md").read_text() == "stray\n"
    (snapshot,) = runs(repo)
    assert (snapshot / "progress.md").read_text() == "stray\n"
    assert "run: copied leftovers to" in cp.out


def test_config_survives(repo):
    sl = repo / ".superlite"
    sl.mkdir()
    (sl / "test-cmd.json").write_text("{}\n")
    (sl / "progress.md").write_text("stray\n")
    assert run("ensure", "--slug", "new", cwd=repo).returncode == 0
    assert (sl / "test-cmd.json").exists()
    (sl / "progress.md").write_text("again\n")
    assert run("ensure", "--slug", "new", cwd=repo).returncode == 0
    assert (sl / "test-cmd.json").exists()
    assert len(runs(repo)) == 1
    (snapshot,) = runs(repo)
    assert (snapshot / "progress.md").read_text() == "stray\n"
    assert (sl / "progress.md").read_text() == "again\n"


def test_no_auto_session_untouched(repo):
    sl = repo / ".superlite"
    sl.mkdir()
    (sl / "progress.md").write_text("stray\n")
    cp = run("ensure", "--slug", "new", cwd=repo, env={"SUPERLITE_NO_AUTO_SESSION": "1"})
    assert cp.returncode == 0
    assert (sl / "progress.md").exists()
    assert runs(repo) == []


def test_clean_checkout_no_line(repo):
    cp = run("ensure", "--slug", "demo", cwd=repo)
    assert "run:" not in cp.out
    cp = run("ensure", "--slug", "demo", cwd=repo)
    assert "run:" not in cp.out and "already open here" in cp.out
    assert runs(repo) == []


def test_failed_enter_restores_leftovers(repo, monkeypatch):
    sys.path.insert(0, str(SESSION.parent))
    import session_ensure as E
    import session_registry as R
    sl = repo / ".superlite"
    sl.mkdir()
    (sl / "progress.md").write_text("stray\n")
    (sl / "test-cmd.json").write_text("{}\n")

    def boom(*a, **k):
        raise RuntimeError("enter failed")

    monkeypatch.setattr(E, "enter_session", boom)
    with R.registry_lock(repo):
        reg = R.read(repo)
        with pytest.raises(E.JournalError):
            E.start_in_host(repo, reg, repo, "new", "main")
    assert (sl / "progress.md").read_text() == "stray\n"
    assert (sl / "test-cmd.json").exists()
    assert not list((sl / "runs").glob("*-unfinished")) if (sl / "runs").exists() else True


def test_nonzero_enter_restores_leftovers(repo, monkeypatch, capsys):
    sys.path.insert(0, str(SESSION.parent))
    import session_ensure as E
    import session_registry as R
    sl = repo / ".superlite"
    sl.mkdir()
    (sl / "progress.md").write_text("stray\n")
    (sl / "test-cmd.json").write_text("{}\n")
    monkeypatch.setattr(E, "enter_session", lambda *a, **k: 2)
    with R.registry_lock(repo):
        reg = R.read(repo)
        assert E.start_in_host(repo, reg, repo, "new", "main") == 2
    assert (sl / "progress.md").read_text() == "stray\n"
    assert capsys.readouterr().out == ""
    assert not list((sl / "runs").glob("*-unfinished")) if (sl / "runs").exists() else True
