"""session ensure archives leftover run files into .superlite/runs/."""
from __future__ import annotations

import os
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


def test_open_here_preserves_unfinished(repo):
    assert run("ensure", "--slug", "demo", cwd=repo).returncode == 0
    sl = repo / ".superlite"
    (sl / "progress.md").write_text("p\n")
    cp = run("ensure", "--slug", "demo", cwd=repo)
    lines = cp.out.strip().splitlines()
    assert cp.returncode == 0, cp.err
    assert lines[0] == "session: demo already open here"
    assert lines[-1].startswith("SESSION ")
    assert (sl / "progress.md").read_text() == "p\n"
    assert runs(repo) == []


@pytest.mark.parametrize("in_flow", [False, True])
@pytest.mark.parametrize("with_branch", [False, True])
def test_ensure_preserves_active_run(repo, with_branch, in_flow):
    assert run("ensure", "--slug", "demo", cwd=repo).returncode == 0
    sl = repo / ".superlite"
    progress = sl / "progress.md"
    progress.write_text("current run\n")
    orchestrator = sl / "orchestrator.json"
    orchestrator.write_text('{"mode":"human","approved":false}\n')
    args = ["ensure", "--slug", "demo"]
    if in_flow:
        args.append("--in-flow")
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
    assert d.name.endswith("-run-unfinished")
    assert (d / "progress.md").read_text() == "stray\n"
    assert not (sl / "progress.md").exists()
    assert git(repo, "branch", "--show-current").strip() != "main"


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


def test_failed_preserved_state_move_restores_everything(repo, monkeypatch):
    import shutil
    import session_ensure as E
    import session_registry as R

    sl = repo / ".superlite"
    sl.mkdir()
    (sl / "progress.md").write_text("stray\n")
    (sl / "test-cmd.json").write_text("{}\n")
    (sl / "agent-specs.json").write_text("{}\n")

    def partial_move(src, dest, moved=None):
        source = R.run_entries(src)[0]
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / source.name
        shutil.move(str(source), str(target))
        moved.append((source, target))
        raise OSError("preserved move failed")

    monkeypatch.setattr(E, "move_run_state", partial_move)
    with R.registry_lock(repo):
        reg = R.read(repo)
        with pytest.raises(E.JournalError):
            E.start_in_host(repo, reg, repo, "new", "main")
    assert (sl / "progress.md").read_text() == "stray\n"
    assert (sl / "test-cmd.json").is_file() and (sl / "agent-specs.json").is_file()
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
