"""Make git fail at each changing step of use, split, close and move-out in turn: nothing may be left half done."""
from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import base  # noqa: E402
import session  # noqa: E402

READONLY_STASH = {"list", "show"}
CHANGING_BRANCH_FLAGS = {"-d", "-D", "-m", "-M", "-c", "-f", "--delete", "--move", "--force"}


def is_changing(args: tuple) -> bool:
    if not args:
        return False
    cmd, sub = args[0], (args[1] if len(args) > 1 else "")
    if cmd == "worktree":
        return sub in {"add", "remove", "move", "prune"}
    if cmd == "stash":
        return sub not in READONLY_STASH
    if cmd == "branch":
        return sub in CHANGING_BRANCH_FLAGS or bool(sub and not sub.startswith("-"))
    return cmd in {"checkout", "switch", "reset", "merge", "clean", "update-ref", "add", "commit", "rm", "mv",
                   "restore", "apply", "cherry-pick", "rebase"}


class Fault:
    """Wraps base.git: counts changing calls; call number `at` fails (0 = never fail, only count)."""

    def __init__(self, at: int = 0):
        self.at, self.count, self.hit = at, 0, None
        self.real = base.git

    def __call__(self, *args, cwd, check=True):
        if is_changing(args):
            self.count += 1
            if self.count == self.at:
                self.hit = (args, check)
                if check:
                    raise base.GitError(args, "injected fault")
                return subprocess.CompletedProcess(["git", *args], 1, "", "injected fault")
        return self.real(*args, cwd=cwd, check=check)


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def sess(root, *args):
    buf = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    err = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        rc = session.main([*map(str, args), "--root", str(root)])
    buf.flush()
    return rc, buf.buffer.getvalue().decode("utf-8") + err.getvalue()


def make_repo(tmp_path):
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


def tree_files(top: Path, skip: tuple = ()) -> dict:
    out = {}
    for f in sorted(top.rglob("*")):
        rel = f.relative_to(top).as_posix()
        if f.is_file() and not rel.startswith((".git/", ".superlite/worktrees/")) and rel not in {".git", ".superlite/sessions.lock", *skip}:  # the lock file is an artifact, not state
            out[rel] = f.read_bytes()
    return out


def snapshot(r: Path, skip: tuple = ()) -> dict:
    wts = [Path(line.split(" ", 1)[1]) for line in git(r, "worktree", "list", "--porcelain").splitlines()
           if line.startswith("worktree ")]
    reg = r / ".superlite" / "sessions.json"
    return {
        "branches": git(r, "for-each-ref", "refs/heads", "--format=%(refname) %(objectname)"),
        "worktrees": git(r, "worktree", "list", "--porcelain"),
        "heads": {w.as_posix(): git(w, "rev-parse", "--abbrev-ref", "HEAD") for w in wts if w.is_dir()},
        "stashes": git(r, "stash", "list", "--format=%gd %H %gs"),
        "files": {w.as_posix(): tree_files(w, skip) for w in wts if w.is_dir()},
        "registry": json.loads(reg.read_text(encoding="utf-8")) if reg.is_file() else None,
    }


def setup_open(tmp_path):
    """Session a open in the main checkout with a tracked edit, an untracked file and run files."""
    r = make_repo(tmp_path)
    assert sess(r, "ensure", "--slug", "a")[0] == 0
    (r / "app.py").write_text("print('wip')\n")
    (r / "notes.txt").write_text("untracked\n")
    (r / ".superlite" / "orchestrator.json").write_text('{"mode":"ai"}\n')
    (r / ".superlite" / "checkpoints.json").write_text('{"cp":1}\n')
    return r


def setup_two(tmp_path):
    """a parked (with its edits stashed), b open in the main checkout with its own edit."""
    r = setup_open(tmp_path)
    assert session.mark_run(r, "finished") == "a"
    assert sess(r, "ensure", "--slug", "b", "--untracked", "stash")[0] == 0
    (r / "app.py").write_text("print('b edit')\n")
    (r / "extra.txt").write_text("b untracked\n")
    (r / ".superlite" / "orchestrator.json").write_text('{"mode":"b"}\n')
    return r


def setup_run_on_main(tmp_path):
    """An open run (no session yet) on main with a tracked edit and an untracked file: what split moves out."""
    r = make_repo(tmp_path)
    (r / "app.py").write_text("print('wip')\n")
    (r / "notes.txt").write_text("untracked\n")
    (r / ".superlite").mkdir(exist_ok=True)
    (r / ".superlite" / "orchestrator.json").write_text('{"mode":"ai"}\n')
    (r / ".superlite" / "checkpoints.json").write_text('{"cp":1}\n')
    return r


CASES = {
    "use": (setup_two, ["use", "--slug", "a", "--untracked", "stash"]),
    "split": (setup_run_on_main, ["split", "--keep", "export", "--new", "auth"]),
    "close": (setup_open, ["close", "--slug", "a", "--untracked", "stash"]),
    "move-out": (setup_open, ["ensure", "--slug", "b", "--move", "yes", "--untracked", "stash"]),
}


def run_with_fault(monkeypatch, tmp_path, name, at, skip=()):
    setup, argv = CASES[name]
    root = setup(tmp_path)
    before = snapshot(root, skip)
    fault = Fault(at)
    monkeypatch.setattr(base, "git", fault)
    rc, out = sess(root, *argv)
    monkeypatch.setattr(base, "git", fault.real)
    return root, before, fault, rc, out


def check_each_step(monkeypatch, tmp_path_factory, name, skip=()):
    root, before, counter, rc, out = run_with_fault(monkeypatch, tmp_path_factory.mktemp("count"), name, 0)
    assert rc == 0, out
    steps = counter.count
    assert steps >= 2, f"{name}: expected several changing git steps, saw {steps}"
    for n in range(1, steps + 1):
        root, before, fault, rc, out = run_with_fault(monkeypatch, tmp_path_factory.mktemp(f"f{n}"), name, n, skip)
        assert fault.hit is not None, f"{name} step {n} not reached"
        if fault.hit[1] is False and rc == 0:
            continue  # the command tolerates this failure by design
        assert rc != 0, f"{name} step {n} {fault.hit[0]}: failure swallowed\n{out}"
        assert snapshot(root, skip) == before, f"{name} step {n} {fault.hit[0]}: state changed\n{out}"


@pytest.mark.slow
@pytest.mark.parametrize("name", ["use", "close", "move-out"])
def test_each_step_fails_cleanly(name, tmp_path_factory, monkeypatch):
    check_each_step(monkeypatch, tmp_path_factory, name)


@pytest.mark.slow
def test_split_each_step_fails_cleanly_apart_from_gitignore(tmp_path_factory, monkeypatch):
    check_each_step(monkeypatch, tmp_path_factory, "split", skip=(".gitignore",))


@pytest.mark.slow
def test_split_failure_leaves_gitignore_untouched(tmp_path_factory, monkeypatch):
    check_each_step(monkeypatch, tmp_path_factory, "split")
