"""session ensure / close / run status / mark_run (feature checks 1-9)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "swarm" / "scripts"
SESSION = SCRIPTS / "session.py"

sys.path.insert(0, str(SCRIPTS))

import session  # noqa: E402


def run(*args, cwd=None, env=None):
    e = dict(os.environ, PYTHONIOENCODING="utf-8")
    e.pop("SUPERLITE_NO_AUTO_SESSION", None)
    e.update(env or {})
    cp = subprocess.run([sys.executable, str(SESSION), *map(str, args)], capture_output=True, cwd=cwd, env=e,
                        timeout=120)
    cp.out = cp.stdout.decode("utf-8")
    cp.err = cp.stderr.decode("utf-8")
    cp.both = cp.out + cp.err
    return cp


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path):
    r = (tmp_path / "app").resolve() if (tmp_path / "app").exists() else tmp_path.resolve() / "app"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "t")
    (r / ".gitignore").write_text(".superlite/\n")
    (r / "app.py").write_text("print('hi')\n")
    (r / "docs" / "plans").mkdir(parents=True)
    (r / "docs" / "plans" / "p.md").write_text("# p\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    return r


def reg_of(r):
    return json.loads((r / ".superlite" / "sessions.json").read_text(encoding="utf-8"))


def row_of(r, slug):
    reg = reg_of(r)
    row = next((s for s in reg["sessions"] if s["slug"] == slug), None)
    assert row is not None, f"no row {slug!r}; found {[s['slug'] for s in reg['sessions']]}"
    return row


def branch(cwd):
    return git(cwd, "branch", "--show-current").strip()


def last(cp):
    return cp.out.strip().splitlines()[-1]


def test_ensure_creates_in_place_on_main_checkout(repo):
    cp = run("ensure", "--slug", "demo", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert last(cp) == f"SESSION worktree={repo.as_posix()} slug=demo branch=superlite/sessions/demo"
    assert branch(repo) == "superlite/sessions/demo"
    reg = reg_of(repo)
    assert reg["host_worktree"] == repo.as_posix() and reg["active"] == "demo" and reg["base"] == "main"
    assert row_of(repo, "demo")["run"] == "open"
    st = run("status", cwd=repo)
    assert "active demo" in st.out and "run:open" in st.out


def test_ensure_is_idempotent(repo):
    run("ensure", "--slug", "demo", cwd=repo)
    before = (repo / ".superlite" / "sessions.json").read_text()
    cp = run("ensure", "--slug", "demo", cwd=repo)
    assert cp.returncode == 0 and "already open here" in cp.out
    assert last(cp).startswith("SESSION ") and "slug=demo" in last(cp)
    assert (repo / ".superlite" / "sessions.json").read_text() == before


def test_ensure_moves_open_host_session_out_while_active_is_open(repo):
    run("ensure", "--slug", "demo", cwd=repo)
    (repo / "app.py").write_text("print('wip')\n")
    cp = run("ensure", "--slug", "next", "--move", "yes", cwd=repo)
    assert cp.returncode == 0, cp.both
    wt = repo / ".claude" / "worktrees" / "sessions-next"
    assert branch(wt) == "superlite/sessions/next"
    assert last(cp) == f"SESSION worktree={wt.as_posix()} slug=next branch=superlite/sessions/next"
    old = repo / ".claude" / "worktrees" / "sessions-demo"
    assert branch(old) == "superlite/sessions/demo" and (old / "app.py").read_text() == "print('wip')\n"
    assert branch(repo) == "main" and git(repo, "status", "--porcelain").strip() == ""
    assert row_of(repo, "next")["owned"] is True and reg_of(repo)["active"] is None
    assert row_of(repo, "demo")["owned"] is True and row_of(repo, "demo")["status"] == "parallel"


def test_finished_active_is_parked_and_new_session_goes_in_place(repo):
    run("ensure", "--slug", "demo", cwd=repo)
    (repo / ".superlite" / "orchestrator.json").write_text('{"mode":"ai"}\n')
    assert session.mark_run(repo, "finished") == "demo"
    row = row_of(repo, "demo")
    assert row["run"] == "finished" and isinstance(row["finished_at"], int) and row["finished_at"] > 10**12
    cp = run("ensure", "--slug", "after", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert branch(repo) == "superlite/sessions/after"
    assert not (repo / ".claude" / "worktrees" / "sessions-after").exists()
    assert row_of(repo, "demo")["status"] == "parked"
    assert (repo / ".superlite" / "session-store" / "demo" / "orchestrator.json").is_file()
    assert not (repo / ".superlite" / "orchestrator.json").exists()
    assert reg_of(repo)["base"] == "main" and row_of(repo, "after")["run"] == "open"


def test_use_switches_by_branch_and_restores_run_files(repo):
    run("ensure", "--slug", "demo", cwd=repo)
    (repo / ".superlite" / "progress.md").write_text("- demo\n")
    session.mark_run(repo, "finished")
    run("ensure", "--slug", "after", cwd=repo)
    cp = run("use", "--slug", "demo", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert branch(repo) == "superlite/sessions/demo"
    assert (repo / ".superlite" / "progress.md").read_text() == "- demo\n"
    assert run("use", "--slug", "after", cwd=repo).returncode == 0
    assert branch(repo) == "superlite/sessions/after"


def test_close_host_parks_and_switches_back_keeping_branch(repo):
    run("ensure", "--slug", "demo", cwd=repo)
    (repo / ".superlite" / "progress.md").write_text("- x\n")
    (repo / "app.py").write_text("print('wip')\n")
    cp = run("close", "--slug", "demo", "--keep-branch", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert branch(repo) == "main"
    assert (repo / "app.py").read_text() == "print('hi')\n"
    assert all(r["slug"] != "demo" for r in reg_of(repo)["sessions"])  # closed: archived, row removed
    assert (repo / ".superlite" / "session-store" / "demo" / "session.json").is_file()
    assert (repo / ".superlite" / "session-store" / "demo" / "progress.md").is_file()
    assert git(repo, "branch", "--list", "superlite/sessions/demo").strip()
    assert reg_of(repo)["active"] is None
    # WIP survives: using the session restores it
    assert run("use", "--slug", "demo", cwd=repo).returncode == 0
    assert (repo / "app.py").read_text() == "print('wip')\n"


def test_close_parallel_removes_owned_worktree_keeps_branch(repo):
    run("ensure", "--slug", "demo", cwd=repo)
    run("ensure", "--slug", "next", "--move", "yes", cwd=repo)
    wt = repo / ".claude" / "worktrees" / "sessions-next"
    cp = run("close", "--slug", "next", "--keep-branch", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert not wt.exists()
    assert git(repo, "branch", "--list", "superlite/sessions/next").strip()
    assert all(r["slug"] != "next" for r in reg_of(repo)["sessions"])  # archived, row removed
    assert (repo / ".superlite" / "session-store" / "next" / "session.json").is_file()
    assert branch(repo) == "main"


def test_close_parallel_without_keep_branch_deletes_it(repo):
    run("ensure", "--slug", "demo", cwd=repo)
    run("ensure", "--slug", "next", "--move", "yes", cwd=repo)
    assert run("close", "--slug", "next", cwd=repo).returncode == 0
    assert not git(repo, "branch", "--list", "superlite/sessions/next").strip()
    assert all(s["slug"] != "next" for s in reg_of(repo)["sessions"])


def test_close_unknown_slug_fails(repo):
    run("ensure", "--slug", "demo", cwd=repo)
    assert run("close", "--slug", "nope", cwd=repo).returncode == 2


def test_unregistered_linked_worktree_is_registered_in_place(repo, tmp_path):
    other = tmp_path.resolve() / "linked"
    git(repo, "worktree", "add", "-q", "-b", "feat/y", str(other))
    cp = run("ensure", "--slug", "x", cwd=other)
    assert cp.returncode == 0, cp.both
    row = row_of(repo, "x")
    assert row["owned"] is False and row["branch"] == "feat/y" and row["run"] == "open"
    assert Path(row["worktree"]).resolve() == other
    assert last(cp) == f"SESSION worktree={other.as_posix()} slug=x branch=feat/y"
    assert not (repo / ".claude" / "worktrees" / "sessions-x").exists()
    assert "already open here" in run("ensure", "--slug", "x", cwd=other).out


def test_slug_collisions_get_suffix(repo):
    run("ensure", "--slug", "demo", cwd=repo)
    session.mark_run(repo, "finished")
    cp = run("ensure", "--slug", "demo", cwd=repo)
    assert last(cp).endswith("slug=demo-2 branch=superlite/sessions/demo-2"), cp.both
    session.mark_run(repo, "finished")
    assert "slug=demo-3" in last(run("ensure", "--slug", "demo", cwd=repo))


def test_opt_out_changes_nothing(repo):
    cp = run("ensure", "--slug", "demo", cwd=repo, env={"SUPERLITE_NO_AUTO_SESSION": "1"})
    assert cp.returncode == 0 and cp.out.strip() == "session: auto-start off"
    assert branch(repo) == "main" and not (repo / ".superlite" / "sessions.json").exists()


def test_status_shows_run_for_create_rows(repo):
    run("create", "--slug", "a", cwd=repo)
    assert "run:open" in run("status", cwd=repo).out


def test_mark_run_none_without_registry_or_row_and_never_raises(repo, tmp_path):
    assert session.mark_run(repo, "finished") is None
    assert session.mark_run(tmp_path / "missing", "finished") is None
    run("ensure", "--slug", "demo", cwd=repo)
    run("ensure", "--slug", "next", "--move", "yes", cwd=repo)
    wt = repo / ".claude" / "worktrees" / "sessions-next"
    assert session.mark_run(wt, "finished") == "next"
    assert row_of(repo, "next")["run"] == "finished" and row_of(repo, "demo")["run"] == "open"
    assert session.mark_run(wt, "open") == "next" and "finished_at" not in row_of(repo, "next")


def test_dirty_untracked_files_travel_with_first_ensure(repo):
    (repo / "scratch.txt").write_text("keep\n")
    (repo / "app.py").write_text("print('wip')\n")
    assert run("ensure", "--slug", "demo", cwd=repo).returncode == 0
    assert (repo / "scratch.txt").read_text() == "keep\n"
    assert (repo / "app.py").read_text() == "print('wip')\n"


def test_concurrent_ensure_same_slug_makes_one_session(repo):
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(4) as ex:
        cps = list(ex.map(lambda _: run("ensure", "--slug", "demo", cwd=repo), range(4)))
    assert all(c.returncode == 0 for c in cps), [c.both for c in cps]
    assert [s["slug"] for s in reg_of(repo)["sessions"]] == ["demo"]


def _assert_leftover_archived(repo):
    """Leftover run files are archived (not lost) under runs/<id>-run-unfinished/."""
    dirs = sorted((repo / ".superlite" / "runs").glob("*-unfinished"))
    assert len(dirs) == 1, dirs
    meta = json.loads((dirs[0] / "run.json").read_text(encoding="utf-8"))
    assert meta["status"] == "unfinished", meta
    found = [f for f in dirs[0].rglob("notes.txt")]
    live = (repo / ".superlite" / "notes.txt")
    assert found or live.exists(), "leftover lost"
    assert not live.exists() or live.read_text() == "keep\n"
    if found:
        assert found[0].read_text() == "keep\n"


def test_leftover_run_files_without_active_survive_ensure(repo):
    (repo / ".superlite").mkdir()
    (repo / ".superlite" / "notes.txt").write_text("keep\n")
    cp = run("ensure", "--slug", "demo", cwd=repo)
    assert cp.returncode == 0, cp.both
    _assert_leftover_archived(repo)


def test_failed_switch_keeps_dirty_work_and_leftovers(repo):
    git(repo, "switch", "-q", "-c", "other")
    (repo / "app.py").write_text("changed on other\n")
    git(repo, "commit", "-qam", "other")
    git(repo, "switch", "-q", "main")
    run("ensure", "--slug", "a", cwd=repo)
    run("close", "--slug", "a", cwd=repo)
    git(repo, "switch", "-q", "other")
    (repo / "app.py").write_text("dirty\n")
    (repo / ".superlite" / "notes.txt").write_text("keep\n")
    run("ensure", "--slug", "b", cwd=repo)
    _assert_leftover_archived(repo)
    assert "dirty" in (repo / "app.py").read_text() or git(repo, "stash", "list").strip()


def _finished_demo(repo, n_untracked):
    run("ensure", "--slug", "demo", cwd=repo)
    for i in range(n_untracked):
        (repo / "docs" / "plans" / ("other.md" if n_untracked == 1 else f"o{i}.md")).write_text("x\n")
    session.mark_run(repo, "finished")


def test_ensure_notes_one_untracked_file_and_use_restores_it(repo):
    _finished_demo(repo, 1)
    cp = run("ensure", "--slug", "after", "--untracked", "stash", cwd=repo)
    lines = cp.out.strip().splitlines()
    note = "session: stashed 1 untracked file(s) with demo: docs/plans/other.md"
    assert note in lines and lines.index(note) < len(lines) - 1 and lines[-1].startswith("SESSION ")
    assert not (repo / "docs" / "plans" / "other.md").exists()
    cp = run("use", "--slug", "demo", cwd=repo)
    assert cp.returncode == 0 and "stashed" not in cp.out
    assert (repo / "docs" / "plans" / "other.md").is_file()


def test_ensure_note_lists_five_then_more(repo):
    _finished_demo(repo, 7)
    out = run("ensure", "--slug", "after", "--untracked", "stash", cwd=repo).out
    line = next(ln for ln in out.splitlines() if ln.startswith("session: stashed"))
    assert line.startswith("session: stashed 7 untracked file(s) with demo: ") and line.endswith("(+2 more)")
    assert line.count(".md") == 5


def test_no_note_without_untracked_files(repo):
    _finished_demo(repo, 0)
    (repo / "app.py").write_text("print('tracked')\n")
    assert "stashed" not in run("ensure", "--slug", "after", cwd=repo).out


def test_close_notes_untracked_files_before_closed_line(repo):
    run("ensure", "--slug", "demo", cwd=repo)
    (repo / "docs" / "plans" / "other.md").write_text("x\n")
    lines = run("close", "--slug", "demo", "--keep-branch", "--untracked", "stash", cwd=repo).out.strip().splitlines()
    assert lines[-2] == "session: stashed 1 untracked file(s) with demo: docs/plans/other.md"
    assert lines[-1].startswith("session: closed demo")


def test_status_shows_removed_for_closed_parallel_worktree(repo):
    run("ensure", "--slug", "demo", cwd=repo)
    run("ensure", "--slug", "next", "--move", "yes", cwd=repo)
    run("close", "--slug", "next", "--keep-branch", cwd=repo)
    out = run("status", cwd=repo).out
    assert "next" not in out and "worktree None" not in out  # a closed session leaves the status list


def snapshot(repo):
    return (branch(repo), (repo / ".superlite" / "sessions.json").read_text(),
            sorted(p.name for p in (repo / "docs" / "plans").iterdir()))


def test_ask_is_default_and_changes_nothing(repo):
    _finished_demo(repo, 1)
    before = snapshot(repo)
    cp = run("ensure", "--slug", "after", cwd=repo)
    assert cp.returncode == 4, cp.both
    assert "ASK untracked=1 slug=demo: docs/plans/other.md" in cp.out
    assert "rerun with --untracked stash|keep, or set it with superlite-cli session untracked stash|keep" in cp.out
    assert snapshot(repo) == before
    assert run("close", "--slug", "demo", cwd=repo).returncode == 4
    assert snapshot(repo) == before


def test_stash_flag_then_use_brings_file_back_and_prints_line(repo):
    _finished_demo(repo, 1)
    assert run("ensure", "--slug", "after", "--untracked", "stash", cwd=repo).returncode == 0
    (repo / "docs" / "plans" / "o2.md").write_text("y\n")
    assert run("use", "--slug", "demo", cwd=repo).returncode == 4
    cp = run("use", "--slug", "demo", "--untracked", "stash", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert "session: stashed 1 untracked file(s) with after: docs/plans/o2.md" in cp.out
    assert (repo / "docs" / "plans" / "other.md").is_file() and not (repo / "docs" / "plans" / "o2.md").exists()


def test_keep_leaves_untracked_in_place_for_new_session(repo):
    _finished_demo(repo, 1)
    (repo / "app.py").write_text("print('tracked-wip')\n")
    cp = run("ensure", "--slug", "after", "--untracked", "keep", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert "session: kept 1 untracked file(s) in place: docs/plans/other.md" in cp.out
    assert branch(repo) == "superlite/sessions/after"
    assert (repo / "docs" / "plans" / "other.md").read_text() == "x\n"
    assert (repo / "app.py").read_text() == "print('hi')\n"
    assert run("use", "--slug", "demo", "--untracked", "keep", cwd=repo).returncode == 0
    assert (repo / "app.py").read_text() == "print('tracked-wip')\n"


def test_setting_skips_ask_and_flag_overrides_it(repo):
    assert run("untracked", cwd=repo).out.strip() == "session: untracked ask"
    assert run("untracked", "keep", cwd=repo).out.strip() == "session: untracked set to keep"
    assert run("untracked", cwd=repo).out.strip() == "session: untracked keep"
    _finished_demo(repo, 1)
    assert run("untracked", "stash", cwd=repo).returncode == 0
    assert reg_of(repo)["untracked"] == "stash" and reg_of(repo)["active"] == "demo"
    cp = run("ensure", "--slug", "after", cwd=repo)
    assert cp.returncode == 0 and "session: stashed 1 untracked" in cp.out
    run("use", "--slug", "demo", cwd=repo)
    run("untracked", "stash", cwd=repo)
    session.mark_run(repo, "finished")
    cp = run("ensure", "--slug", "third", "--untracked", "keep", cwd=repo)
    assert cp.returncode == 0 and "session: kept 1 untracked" in cp.out


def test_keep_with_conflicting_untracked_path_rolls_back(repo):
    run("ensure", "--slug", "demo", cwd=repo)
    session.mark_run(repo, "finished")
    git(repo, "switch", "-q", "main")
    (repo / "docs" / "plans" / "p.md").unlink()
    git(repo, "switch", "-q", "superlite/sessions/demo")
    # untracked file whose path exists in the base commit: switching would overwrite it
    git(repo, "rm", "-q", "--cached", "docs/plans/p.md")
    git(repo, "commit", "-q", "-m", "drop p on demo")
    (repo / "docs" / "plans" / "p.md").write_text("mine\n")
    before = snapshot(repo)
    cp = run("ensure", "--slug", "after", "--untracked", "keep", cwd=repo)
    assert cp.returncode == 2, cp.both
    assert "docs/plans/p.md" in cp.err
    assert snapshot(repo) == before
    assert (repo / "docs" / "plans" / "p.md").read_text() == "mine\n"


def test_no_untracked_means_no_ask(repo):
    _finished_demo(repo, 0)
    cp = run("ensure", "--slug", "after", cwd=repo)
    assert cp.returncode == 0 and "ASK" not in cp.out and "untracked file(s)" not in cp.out


def test_create_gates_untracked_when_parking(repo):
    run("create", "--slug", "demo", cwd=repo)
    (repo / "docs" / "plans" / "other.md").write_text("x\n")
    before = (reg_of(repo), branch(repo))
    cp = run("create", "--slug", "two", cwd=repo)
    assert cp.returncode == 4 and "ASK untracked=1 slug=demo: docs/plans/other.md" in cp.out
    assert cp.out.splitlines()[1].startswith("ask: ")
    assert (reg_of(repo), branch(repo)) == before
    assert run("create", "--slug", "wt", "--worktree", cwd=repo).returncode == 0
    cp = run("create", "--slug", "two", "--untracked", "stash", cwd=repo)
    assert cp.returncode == 0 and "session: stashed 1 untracked file(s) with demo: docs/plans/other.md" in cp.out
    assert not (repo / "docs" / "plans" / "other.md").exists()
    (repo / "docs" / "plans" / "o3.md").write_text("y\n")
    run("untracked", "keep", cwd=repo)
    cp = run("create", "--slug", "three", cwd=repo)
    assert cp.returncode == 0 and "session: kept 1 untracked file(s) in place: docs/plans/o3.md" in cp.out
    assert (repo / "docs" / "plans" / "o3.md").is_file()


def test_ensure_leaves_committed_gitignore_and_status_clean(repo):
    before = (repo / ".gitignore").read_text()
    assert run("ensure", "--slug", "demo", cwd=repo).returncode == 0
    assert (repo / ".gitignore").read_text() == before
    assert git(repo, "status", "--porcelain").strip() == ""
    from subprocess import run as sp
    assert sp(["git", "check-ignore", "-q", ".superlite/x"], cwd=repo).returncode == 0
    session.mark_run(repo, "finished")
    assert run("close", "--slug", "demo", "--keep-branch", cwd=repo).returncode == 0
    assert all(r["slug"] != "demo" for r in reg_of(repo)["sessions"])
    assert "demo" not in run("status", cwd=repo).out


def test_no_gitignore_uses_info_exclude(repo):
    git(repo, "rm", "-q", ".gitignore")
    git(repo, "commit", "-q", "-m", "no ignore")
    assert run("ensure", "--slug", "demo", cwd=repo).returncode == 0
    assert not (repo / ".gitignore").exists()
    assert ".superlite/" in (repo / ".git" / "info" / "exclude").read_text().splitlines()
    assert git(repo, "status", "--porcelain").strip() == ""
    run("ensure", "--slug", "next", "--move", "yes", cwd=repo)
    assert (repo / ".git" / "info" / "exclude").read_text().splitlines().count(".superlite/") == 1


def wt_of(r, slug):
    return r / ".claude" / "worktrees" / f"sessions-{slug}"


def commit_file(cwd, name, text, msg):
    (Path(cwd) / name).write_text(text)
    git(cwd, "add", name)
    git(cwd, "commit", "-q", "-m", msg)
    return git(cwd, "rev-parse", "HEAD").strip()


def is_ancestor(repo, sha, ref):
    return subprocess.run(["git", "merge-base", "--is-ancestor", sha, ref], cwd=repo,
                          capture_output=True).returncode == 0


def finish_active(repo):
    (repo / ".superlite").mkdir(exist_ok=True)
    (repo / ".superlite" / "orchestrator.json").write_text('{"mode":"ai"}\n')
    assert session.mark_run(repo, "finished")


@pytest.fixture
def owned_b(repo):
    """Parallel owned session b with a tracked edit, an untracked file and a run file in its worktree."""
    assert run("ensure", "--slug", "a", cwd=repo).returncode == 0
    cp = run("ensure", "--slug", "b", "--move", "yes", cwd=repo)
    assert cp.returncode == 0, cp.both
    wt = wt_of(repo, "b")
    (wt / "app.py").write_text("one\nTWO-edit\nthree\n")
    (wt / "new.txt").write_text("untracked-content\n")
    (wt / ".superlite").mkdir(exist_ok=True)
    (wt / ".superlite" / "run-file.txt").write_text("run\n")
    return repo


def test_close_owned_parallel_stashes_work(owned_b):
    """Closing an owned parallel session with --untracked stash keeps tracked, untracked and run files."""
    repo, wt = owned_b, wt_of(owned_b, "b")
    cp = run("close", "--slug", "b", "--keep-branch", "--untracked", "stash", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert not wt.exists()
    assert git(repo, "branch", "--list", "superlite/sessions/b").strip()
    assert all(r["slug"] != "b" for r in reg_of(repo)["sessions"])  # archived; WIP is on a private ref
    shown = git(repo, "show", "-p", "refs/superlite/wip/b")
    assert "TWO-edit" in shown and "new.txt" in shown and "untracked-content" in shown
    assert (repo / ".superlite" / "session-store" / "b" / "run-file.txt").is_file()


def test_close_owned_parallel_untracked_keep_leaves_worktree(owned_b):
    """Closing an owned parallel session with --untracked keep leaves the worktree in place."""
    repo, wt = owned_b, wt_of(owned_b, "b")
    cp = run("close", "--slug", "b", "--keep-branch", "--untracked", "keep", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert wt.is_dir(), "worktree was removed although untracked files are kept"
    assert (wt / "new.txt").read_text() == "untracked-content\n"
    assert (wt / ".superlite" / "run-file.txt").is_file()
    assert "kept" in cp.out and "in place" in cp.out, cp.out
    assert git(repo, "branch", "--list", "superlite/sessions/b").strip()


def test_close_owned_parallel_asks_when_untracked_unset(owned_b):
    """Closing an owned parallel session with untracked files and no setting asks (exit 4) and changes nothing."""
    repo, wt = owned_b, wt_of(owned_b, "b")
    before = (repo / ".superlite" / "sessions.json").read_bytes()
    cp = run("close", "--slug", "b", "--keep-branch", cwd=repo)
    assert cp.returncode == 4, cp.both
    assert "ASK untracked=" in cp.out, cp.out
    assert wt.is_dir()
    assert (wt / "new.txt").is_file() and (wt / ".superlite" / "run-file.txt").is_file()
    assert (wt / "app.py").read_text() == "one\nTWO-edit\nthree\n"
    assert (repo / ".superlite" / "sessions.json").read_bytes() == before


def test_close_owned_parallel_keeps_staged_and_nested_untracked(repo):
    """Staged edits and nested untracked dirs survive an owned parallel close."""
    assert run("ensure", "--slug", "a", cwd=repo).returncode == 0
    assert run("ensure", "--slug", "b", "--move", "yes", cwd=repo).returncode == 0
    wt = wt_of(repo, "b")
    (wt / "app.py").write_text("one\nSTAGED\nthree\n")
    git(wt, "add", "app.py")
    (wt / "d1" / "d2").mkdir(parents=True)
    (wt / "d1" / "d2" / "deep.txt").write_text("deep\n")
    cp = run("close", "--slug", "b", "--untracked", "stash", cwd=repo)
    assert cp.returncode == 0, cp.both
    ref = "refs/superlite/wip/b"
    assert "STAGED" in git(repo, "show", f"{ref}^2:app.py")  # the index commit keeps the staged edit
    assert git(repo, "show", f"{ref}:d1/d2/deep.txt") == "deep\n"


def test_close_owned_parallel_worktree_deleted_by_hand(repo):
    """Close does not crash when the worktree dir was removed by hand."""
    import shutil
    assert run("ensure", "--slug", "a", cwd=repo).returncode == 0
    assert run("ensure", "--slug", "b", "--move", "yes", cwd=repo).returncode == 0
    shutil.rmtree(wt_of(repo, "b"))
    cp = run("close", "--slug", "b", cwd=repo)
    assert cp.returncode == 0, cp.both


def test_close_host_refuses_when_base_checked_out_elsewhere(repo, tmp_path):
    """Closing the host session refuses, changing nothing, when the base branch is checked out in another worktree."""
    assert run("ensure", "--slug", "a", cwd=repo).returncode == 0
    (repo / "app.py").write_text("one\nTWO-edit\nthree\n")
    (repo / ".superlite" / "run-file.txt").write_text("run\n")
    other = tmp_path.resolve() / "other"
    git(repo, "worktree", "add", "-q", str(other), "main")
    stashes = git(repo, "stash", "list")
    before = (repo / ".superlite" / "sessions.json").read_bytes()
    cp = run("close", "--slug", "a", cwd=repo)
    assert cp.returncode == 2, cp.both
    assert other.as_posix() in cp.err, cp.err
    assert branch(repo) == "superlite/sessions/a"
    assert (repo / "app.py").read_text() == "one\nTWO-edit\nthree\n"
    assert (repo / ".superlite" / "run-file.txt").is_file()
    assert reg_of(repo)["active"] == "a"
    assert git(repo, "stash", "list") == stashes
    assert (repo / ".superlite" / "sessions.json").read_bytes() == before


def test_ensure_outside_git_continues_without_session(tmp_path):
    """Ensure in a non-git folder exits 0 with a no-repository notice and a SESSION line."""
    d = tmp_path.resolve() / "plain"
    d.mkdir()
    cp = run("ensure", "--slug", "demo", cwd=d)
    assert cp.returncode == 0, cp.both
    assert "session: no git repository here; continuing without a session" in cp.out, cp.both
    assert last(cp) == f"SESSION worktree={d.as_posix()} slug=demo branch=-"
    assert not (d / ".superlite" / "sessions.json").exists()


def test_ensure_in_repo_without_commits_continues_without_session(tmp_path):
    """Ensure in a git repo with no commit exits 0 with a no-commits notice and a SESSION line."""
    d = tmp_path.resolve() / "fresh"
    d.mkdir()
    git(d, "init", "-q", "-b", "main")
    cp = run("ensure", "--slug", "demo", cwd=d)
    assert cp.returncode == 0, cp.both
    assert "session: repository has no commits yet; continuing without a session" in cp.out, cp.both
    assert last(cp) == f"SESSION worktree={d.as_posix()} slug=demo branch=-"
    assert not (d / ".superlite" / "sessions.json").exists()


def test_ensure_from_subfolder_of_repo_without_commits(tmp_path):
    """Ensure from a subfolder of a repo without commits continues without a session."""
    d = tmp_path.resolve() / "fresh"
    (d / "sub").mkdir(parents=True)
    git(d, "init", "-q", "-b", "main")
    cp = run("ensure", "--slug", "demo", cwd=d / "sub")
    assert cp.returncode == 0, cp.both
    assert "no commits yet" in cp.out


def test_base_follows_main_checkout_branch_after_close(repo):
    """After close, a new ensure reads the main checkout's branch as base, not the stale registry base."""
    assert run("ensure", "--slug", "a", cwd=repo).returncode == 0
    cp = run("close", "--slug", "a", "--keep-branch", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert branch(repo) == "main"
    git(repo, "switch", "-q", "-c", "feature")
    feat = commit_file(repo, "feat.txt", "feature\n", "feature work")
    cp = run("ensure", "--slug", "b", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert is_ancestor(repo, feat, "superlite/sessions/b"), "b was cut from the stale base, not from feature"
    assert row_of(repo, "b")["base"] == "feature"  # the registry base is set once; rows keep their own


def test_finished_active_is_parked_and_new_session_keeps_recorded_base(repo):
    """A finished active session is parked and the new one still uses the recorded base."""
    assert run("ensure", "--slug", "a", cwd=repo).returncode == 0
    a_commit = commit_file(repo, "a.txt", "a\n", "a work")
    (repo / ".superlite" / "orchestrator.json").write_text('{"mode":"ai"}\n')
    assert session.mark_run(repo, "finished") == "a"
    cp = run("ensure", "--slug", "b", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert branch(repo) == "superlite/sessions/b"
    assert reg_of(repo)["base"] == "main"
    assert not is_ancestor(repo, a_commit, "superlite/sessions/b")
    assert row_of(repo, "a")["status"] == "parked"


def test_detached_host_with_deleted_base_still_starts(repo):
    """A closed session's base is gone and the host is detached: ensure and create fall back to an existing branch."""
    assert run("ensure", "--slug", "a", cwd=repo).returncode == 0
    assert run("close", "--slug", "a", cwd=repo).returncode == 0
    git(repo, "branch", "-m", "main", "trunk")
    git(repo, "checkout", "-q", "--detach")
    cp = run("ensure", "--slug", "z", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert row_of(repo, "z")["branch"] == "superlite/sessions/z"
    assert row_of(repo, "z")["base"] == "trunk"
    git(repo, "checkout", "-q", "--detach")
    assert run("close", "--slug", "z", cwd=repo).returncode == 0
    git(repo, "checkout", "-q", "--detach")
    cp = run("create", "--slug", "y", cwd=repo)
    assert cp.returncode == 0, cp.both


def test_ensure_branch_registers_current_branch_in_place(repo):
    """`ensure --branch <B>` on the checked-out branch keeps it and the edit, and names it in the row."""
    git(repo, "switch", "-q", "-c", "feature")
    commit_file(repo, "feat.txt", "feature\n", "feature work")
    (repo / "app.py").write_text("one\nEDITED\nthree\n")
    cp = run("ensure", "--slug", "rev", "--branch", "feature", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert branch(repo) == "feature"
    assert (repo / "app.py").read_text() == "one\nEDITED\nthree\n"
    assert row_of(repo, "rev")["branch"] == "feature"
    assert last(cp) == f"SESSION worktree={repo.as_posix()} slug=rev branch=feature"


def test_ensure_branch_parallel_worktree_checks_out_existing_branch(repo):
    """`ensure --branch <B> --move yes` puts the new worktree on existing B, not a fresh branch off base."""
    assert run("ensure", "--slug", "a", cwd=repo).returncode == 0
    tmp = repo.parent / "tmp-feature-wt"
    git(repo, "worktree", "add", "-q", "-b", "feature", str(tmp), "main")
    feat = commit_file(tmp, "feat.txt", "feature\n", "feature work")
    git(repo, "worktree", "remove", "--force", str(tmp))
    cp = run("ensure", "--slug", "rev", "--branch", "feature", "--move", "yes", cwd=repo)
    assert cp.returncode == 0, cp.both
    wt = row_of(repo, "rev")["worktree"]
    assert branch(wt) == "feature"
    assert git(wt, "rev-parse", "HEAD").strip() == feat
    assert (Path(wt) / "feat.txt").read_text() == "feature\n"
    assert last(cp) == f"SESSION worktree={wt} slug=rev branch=feature"


def test_adopted_branch_survives_close_and_discard(repo):
    """An adopted user branch is never deleted by close or discard, with or without --keep-branch."""
    git(repo, "branch", "other")
    assert run("ensure", "--slug", "r", "--branch", "other", cwd=repo).returncode == 0
    assert row_of(repo, "r").get("adopted_branch") is True
    assert run("close", "--slug", "r", cwd=repo).returncode == 0
    assert "other" in git(repo, "branch", "--format=%(refname:short)").split()
    assert run("ensure", "--slug", "r2", "--branch", "other", cwd=repo).returncode == 0
    assert run("discard", "--slug", "r2", cwd=repo).returncode == 0
    assert "other" in git(repo, "branch", "--format=%(refname:short)").split()


def exclude_lines(r):
    common = git(r, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    f = Path(common) / "info" / "exclude"
    return [ln.strip() for ln in f.read_text(encoding="utf-8").splitlines()] if f.is_file() else []


def superlite_status(cwd):
    return [ln for ln in git(cwd, "status", "--porcelain").splitlines() if ".superlite" in ln]


@pytest.fixture
def repo_noignore(repo):
    (repo / ".gitignore").write_text("*.pyc\n")
    git(repo, "commit", "-q", "-am", "ignore pyc only")
    return repo


def test_ensure_in_unregistered_linked_worktree_hides_superlite(repo_noignore):
    """Adopting an unregistered linked worktree hides .superlite/ in every checkout."""
    repo = repo_noignore
    x = repo.parent / "x"
    git(repo, "worktree", "add", "-q", str(x), "-b", "x")
    cp = run("ensure", "--slug", "x", cwd=x)
    assert cp.returncode == 0, cp.both
    assert "registered x where it is" in cp.out, cp.out
    (x / ".superlite").mkdir(exist_ok=True)
    (x / ".superlite" / "orchestrator.json").write_text("{}\n")
    assert ".superlite/" in exclude_lines(repo)
    assert superlite_status(repo) == []
    assert superlite_status(x) == []


def test_exclusion_does_not_depend_on_tracked_gitignore(repo):
    """info/exclude gets .superlite/ even when the tracked .gitignore already lists it."""
    cp = run("ensure", "--slug", "a", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert ".superlite/" in exclude_lines(repo), exclude_lines(repo)
    other = repo.parent / "nogi"
    git(repo, "worktree", "add", "-q", str(other), "-b", "nogi", "main")
    git(other, "rm", "-q", ".gitignore")
    git(other, "commit", "-q", "-m", "drop gitignore")
    git(repo, "worktree", "remove", "--force", str(other))
    git(repo, "switch", "-q", "nogi")
    assert not (repo / ".gitignore").exists()
    assert superlite_status(repo) == []


def advance_main(repo, n):
    for i in range(n):
        tip = git(repo, "rev-parse", "refs/heads/main").strip()
        new = git(repo, "commit-tree", "refs/heads/main^{tree}", "-p", tip, "-m", f"adv{i}").strip()
        git(repo, "update-ref", "refs/heads/main", new)


def test_status_shows_main_plus_n_when_behind(repo):
    """`session status` appends ` | main:+N` for a row N commits behind the local base."""
    assert run("ensure", "--slug", "a", cwd=repo).returncode == 0
    cp = run("status", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert "main:+" not in cp.out, cp.out
    advance_main(repo, 2)
    cp = run("status", cwd=repo)
    assert cp.returncode == 0, cp.both
    row_line = next(ln for ln in cp.out.splitlines() if " a | branch" in ln)
    assert " | main:+2" in row_line, row_line


def test_ensure_is_idempotent_after_a_collision(repo):
    """After demo collided into demo-2, the same ensure --slug demo reports demo-2 open, no demo-3."""
    assert run("ensure", "--slug", "demo", cwd=repo).returncode == 0
    finish_active(repo)
    cp = run("ensure", "--slug", "demo", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert "using demo-2" in cp.out, cp.out
    cp = run("ensure", "--slug", "demo", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert "demo-2 already open here" in cp.out, cp.out
    slugs = [s["slug"] for s in reg_of(repo)["sessions"]]
    assert "demo-3" not in slugs, slugs
    assert row_of(repo, "demo-2").get("topic") == "demo", row_of(repo, "demo-2")


def test_collision_on_41_char_slug_yields_valid_slug(repo):
    """A 41-char slug that collides still gets a generated slug of <= 41 chars that validates."""
    long_slug = "a" * 41
    assert session.SLUG.match(long_slug)
    assert run("ensure", "--slug", long_slug, cwd=repo).returncode == 0
    finish_active(repo)
    cp = run("ensure", "--slug", long_slug, cwd=repo)
    assert cp.returncode == 0, cp.both
    m = re.search(r"slug \S+ taken; using (\S+)", cp.out)
    assert m, cp.out
    new = m.group(1)
    assert new != long_slug and len(new) <= 41 and session.SLUG.match(new), new
    cp = run("close", "--slug", new, cwd=repo)
    assert cp.returncode == 0, cp.both


def test_ensure_branch_from_open_parallel_session_targets_requested_branch(repo):
    """From an open parallel session, `ensure --branch feature` hands back feature, not the session's own branch."""
    assert run("ensure", "--slug", "a", cwd=repo).returncode == 0
    assert run("ensure", "--slug", "b", "--move", "yes", cwd=repo).returncode == 0
    git(repo, "branch", "feature", "main")
    (repo / "app.py").write_text("print('main wip')\n")
    cp = run("ensure", "--slug", "rev", "--branch", "feature", cwd=wt_of(repo, "b"))
    assert cp.returncode == 0, cp.both
    assert last(cp).endswith(" branch=feature"), cp.out
    wt = last(cp).split("worktree=", 1)[1].split(" slug=", 1)[0]
    assert branch(wt) == "feature"
    assert Path(wt).resolve() not in (repo.resolve(), wt_of(repo, "b").resolve())
    assert branch(repo) == "main" and (repo / "app.py").read_text() == "print('main wip')\n"


def test_ensure_branch_from_linked_checkout_does_not_move_the_host_session(repo):
    """A linked checkout asking for a branch never triggers ASK move for the session in the main checkout."""
    assert run("ensure", "--slug", "a", cwd=repo).returncode == 0
    x = repo.parent / "x"
    git(repo, "worktree", "add", "-q", "-b", "x", str(x), "main")
    git(repo, "branch", "feature", "main")
    cp = run("ensure", "--slug", "rev", "--branch", "feature", cwd=x)
    assert cp.returncode == 0, cp.both
    assert "ASK move" not in cp.out, cp.out
    assert branch(repo) == "superlite/sessions/a"


def test_redirect_to_a_finished_session_names_its_slug(repo):
    """REDIRECT to a checkout whose row already exists prints that row's slug."""
    assert run("ensure", "--slug", "a", cwd=repo).returncode == 0
    assert run("ensure", "--slug", "b", "--move", "yes", cwd=repo).returncode == 0
    cp = run("ensure", "--slug", "rev", "--branch", "superlite/sessions/b", cwd=repo)
    assert cp.returncode == 3, cp.both
    assert "slug=b " in cp.out, cp.out


def test_redirect_to_unregistered_worktree_registers_it(repo):
    """REDIRECT to an unregistered checkout of the requested branch registers it as a session first."""
    x = repo.parent / "x"
    git(repo, "worktree", "add", "-q", "-b", "feature", str(x))
    cp = run("ensure", "--slug", "rev", "--branch", "feature", cwd=repo)
    assert cp.returncode == 3, cp.both
    assert "REDIRECT" in cp.out, cp.out
    rows = [s for s in reg_of(repo)["sessions"] if s.get("branch") == "feature"]
    assert len(rows) == 1, reg_of(repo)
    assert Path(rows[0]["worktree"]).resolve() == x.resolve()
    assert rows[0].get("run", "open") == "open"
    assert rows[0].get("adopted_branch") is True


def test_session_on_base_branch_stays_in_place_when_second_flow_starts(repo):
    """An open session that adopted the base branch stays in the main checkout; the new one goes parallel."""
    cp = run("ensure", "--slug", "a", "--branch", "main", cwd=repo)
    assert cp.returncode == 0, cp.both
    cp = run("ensure", "--slug", "b", "--move", "yes", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert branch(repo) == "main"
    assert Path(row_of(repo, "a")["worktree"]).resolve() == repo.resolve()
    assert reg_of(repo)["active"] == "a"
    assert Path(row_of(repo, "b")["worktree"]).resolve() == wt_of(repo, "b").resolve()


def test_session_on_base_branch_does_not_ask_to_move(repo):
    """No ASK move for a session on the base branch: it cannot leave the main checkout."""
    assert run("ensure", "--slug", "a", "--branch", "main", cwd=repo).returncode == 0
    cp = run("ensure", "--slug", "b", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert "ASK move" not in cp.out, cp.out


def test_rows_record_their_own_base(repo):
    """Each row keeps the base it was cut from, so a later session on another base does not change it."""
    assert run("ensure", "--slug", "a", cwd=repo).returncode == 0
    assert run("ensure", "--slug", "b", "--move", "yes", cwd=repo).returncode == 0
    assert row_of(repo, "b")["base"] == "main"
    git(repo, "switch", "-q", "-c", "feature")
    commit_file(repo, "feat.txt", "feature\n", "feature work")
    assert run("ensure", "--slug", "c", cwd=repo).returncode == 0
    assert row_of(repo, "c")["base"] == "feature" and reg_of(repo)["base"] == "main"
    assert row_of(repo, "c")["base"] == "feature"
    assert row_of(repo, "b")["base"] == "main"


def test_status_measures_each_row_against_its_own_base(repo):
    """`session status` counts main:+N for a row cut from main even after the registry base moved to feature."""
    assert run("ensure", "--slug", "a", cwd=repo).returncode == 0
    assert run("ensure", "--slug", "b", "--move", "yes", cwd=repo).returncode == 0
    git(repo, "switch", "-q", "-c", "feature")
    assert run("ensure", "--slug", "c", cwd=repo).returncode == 0
    advance_main(repo, 2)
    cp = run("status", cwd=repo)
    row_line = next(ln for ln in cp.out.splitlines() if " b | branch" in ln)
    assert " | main:+2" in row_line, row_line


def test_review_of_finished_sessions_branch_keeps_its_uncommitted_work(repo):
    """ensure --branch <the finished session's own branch> reopens it in place; its uncommitted fix stays."""
    assert run("ensure", "--slug", "s", cwd=repo).returncode == 0
    (repo / "app.py").write_text("print('fixed')\n")
    finish_active(repo)
    stashes = git(repo, "stash", "list")
    cp = run("ensure", "--slug", "review", "--branch", "superlite/sessions/s", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert (repo / "app.py").read_text() == "print('fixed')\n"
    assert git(repo, "stash", "list") == stashes
    assert branch(repo) == "superlite/sessions/s"
    assert last(cp).endswith(" branch=superlite/sessions/s"), cp.out
    assert row_of(repo, "s").get("run") == "open"
