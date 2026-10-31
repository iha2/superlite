"""session ensure / close / run status / mark_run (feature checks 1-9)."""
from __future__ import annotations

import json
import os
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
    return next(s for s in reg_of(r)["sessions"] if s["slug"] == slug)


def branch(cwd):
    return git(cwd, "branch", "--show-current").strip()


def last(cp):
    return cp.out.strip().splitlines()[-1]


def test_ensure_creates_in_place_on_main_checkout(repo):
    cp = run("ensure", "--slug", "demo", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert last(cp) == f"SESSION worktree={repo} slug=demo branch=superlite/sessions/demo"
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


def test_ensure_goes_parallel_while_active_is_open(repo):
    run("ensure", "--slug", "demo", cwd=repo)
    (repo / "docs" / "plans" / "other.md").write_text("# other\n")
    cp = run("ensure", "--slug", "next", cwd=repo)
    assert cp.returncode == 0, cp.both
    wt = repo / ".superlite" / "worktrees" / "sessions" / "next"
    assert branch(wt) == "superlite/sessions/next"
    assert last(cp) == f"SESSION worktree={wt} slug=next branch=superlite/sessions/next"
    assert branch(repo) == "superlite/sessions/demo"
    assert (repo / "docs" / "plans" / "other.md").is_file()
    assert row_of(repo, "next")["owned"] is True and reg_of(repo)["active"] == "demo"


def test_finished_active_is_parked_and_new_session_goes_in_place(repo):
    run("ensure", "--slug", "demo", cwd=repo)
    (repo / ".superlite" / "orchestrator.json").write_text('{"mode":"ai"}\n')
    assert session.mark_run(repo, "finished") == "demo"
    row = row_of(repo, "demo")
    assert row["run"] == "finished" and isinstance(row["finished_at"], int) and row["finished_at"] > 10**12
    cp = run("ensure", "--slug", "after", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert branch(repo) == "superlite/sessions/after"
    assert not (repo / ".superlite" / "worktrees" / "sessions" / "after").exists()
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
    row = row_of(repo, "demo")
    assert row["run"] == "finished" and row["status"] == "parked" and row["has_stash"] is True
    assert (repo / ".superlite" / "session-store" / "demo" / "progress.md").is_file()
    assert git(repo, "branch", "--list", "superlite/sessions/demo").strip()
    assert reg_of(repo)["active"] is None
    # WIP survives: using the session restores it
    assert run("use", "--slug", "demo", cwd=repo).returncode == 0
    assert (repo / "app.py").read_text() == "print('wip')\n"


def test_close_parallel_removes_owned_worktree_keeps_branch(repo):
    run("ensure", "--slug", "demo", cwd=repo)
    run("ensure", "--slug", "next", cwd=repo)
    wt = repo / ".superlite" / "worktrees" / "sessions" / "next"
    cp = run("close", "--slug", "next", "--keep-branch", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert not wt.exists()
    assert git(repo, "branch", "--list", "superlite/sessions/next").strip()
    assert row_of(repo, "next")["run"] == "finished"
    assert branch(repo) == "superlite/sessions/demo"


def test_close_parallel_without_keep_branch_deletes_it(repo):
    run("ensure", "--slug", "demo", cwd=repo)
    run("ensure", "--slug", "next", cwd=repo)
    assert run("close", "--slug", "next", cwd=repo).returncode == 0
    assert not git(repo, "branch", "--list", "superlite/sessions/next").strip()
    assert all(s["slug"] != "next" for s in reg_of(repo)["sessions"])


def test_close_unknown_slug_fails(repo):
    run("ensure", "--slug", "demo", cwd=repo)
    assert run("close", "--slug", "nope", cwd=repo).returncode == 2


def test_legacy_run_is_adopted_then_new_slug_goes_parallel(repo):
    (repo / ".superlite").mkdir()
    (repo / ".superlite" / "orchestrator.json").write_text('{"mode":"ai"}\n')
    (repo / ".superlite" / "feature.json").write_text('{"topic":"old-thing"}\n')
    cp = run("ensure", "--slug", "new", cwd=repo)
    assert cp.returncode == 0, cp.both
    reg = reg_of(repo)
    assert reg["active"] == "old-thing" and reg["host_worktree"] == repo.as_posix()
    assert row_of(repo, "old-thing")["run"] == "open"
    assert branch(repo) == "superlite/sessions/old-thing"
    assert (repo / ".superlite" / "orchestrator.json").read_text() == '{"mode":"ai"}\n'
    wt = repo / ".superlite" / "worktrees" / "sessions" / "new"
    assert wt.is_dir() and last(cp).startswith(f"SESSION worktree={wt} slug=new")


def test_legacy_run_on_feature_branch_keeps_its_branch(repo):
    git(repo, "switch", "-q", "-c", "feat/x")
    (repo / ".superlite").mkdir()
    (repo / ".superlite" / "orchestrator.json").write_text("{}\n")
    run("ensure", "--slug", "new", cwd=repo)
    assert branch(repo) == "feat/x"
    assert row_of(repo, "feat-x")["branch"] == "feat/x"


def test_legacy_run_on_session_branch_reuses_its_slug(repo):
    git(repo, "switch", "-q", "-c", "superlite/sessions/foo")
    (repo / ".superlite").mkdir()
    (repo / ".superlite" / "orchestrator.json").write_text("{}\n")
    run("ensure", "--slug", "new", cwd=repo)
    assert branch(repo) == "superlite/sessions/foo"
    assert row_of(repo, "foo")["branch"] == "superlite/sessions/foo"
    assert reg_of(repo)["active"] == "foo"
    assert not any(s["slug"] == "foo-2" for s in reg_of(repo)["sessions"])


def test_unregistered_linked_worktree_is_registered_in_place(repo, tmp_path):
    other = tmp_path.resolve() / "linked"
    git(repo, "worktree", "add", "-q", "-b", "feat/y", str(other))
    cp = run("ensure", "--slug", "x", cwd=other)
    assert cp.returncode == 0, cp.both
    row = row_of(repo, "x")
    assert row["owned"] is False and row["branch"] == "feat/y" and row["run"] == "open"
    assert Path(row["worktree"]).resolve() == other
    assert last(cp) == f"SESSION worktree={other} slug=x branch=feat/y"
    assert not (repo / ".superlite" / "worktrees" / "sessions" / "x").exists()
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


def test_separate_host_stays_host_and_redirects(repo):
    assert run("create", "--slug", "old", cwd=repo).returncode == 0
    host = repo / ".superlite" / "worktrees" / "sessions" / "host"
    session.mark_run(host, "finished")
    cp = run("ensure", "--slug", "fresh", cwd=repo)
    assert cp.returncode == 3, cp.both
    assert f"REDIRECT worktree={host} slug=fresh branch=superlite/sessions/fresh" in cp.out
    assert last(cp).startswith("SESSION ") and reg_of(repo)["host_worktree"] == host.as_posix()
    assert branch(repo) == "main" and branch(host) == "superlite/sessions/fresh"


def test_status_shows_run_for_create_rows(repo):
    run("create", "--slug", "a", cwd=repo)
    assert "run:open" in run("status", cwd=repo).out


def test_mark_run_none_without_registry_or_row_and_never_raises(repo, tmp_path):
    assert session.mark_run(repo, "finished") is None
    assert session.mark_run(tmp_path / "missing", "finished") is None
    run("ensure", "--slug", "demo", cwd=repo)
    run("ensure", "--slug", "next", cwd=repo)
    wt = repo / ".superlite" / "worktrees" / "sessions" / "next"
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


def test_leftover_run_files_without_active_survive_ensure(repo):
    (repo / ".superlite").mkdir()
    (repo / ".superlite" / "notes.txt").write_text("keep\n")
    cp = run("ensure", "--slug", "demo", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert (repo / ".superlite" / "notes.txt").read_text() == "keep\n"


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
    assert (repo / ".superlite" / "notes.txt").read_text() == "keep\n"
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
    run("ensure", "--slug", "next", cwd=repo)
    run("close", "--slug", "next", "--keep-branch", cwd=repo)
    out = run("status", cwd=repo).out
    assert "worktree (removed)" in out and "worktree None" not in out


def snapshot(repo):
    return (branch(repo), (repo / ".superlite" / "sessions.json").read_text(),
            sorted(p.name for p in (repo / "docs" / "plans").iterdir()))


def test_ask_is_default_and_changes_nothing(repo):
    _finished_demo(repo, 1)
    before = snapshot(repo)
    cp = run("ensure", "--slug", "after", cwd=repo)
    assert cp.returncode == 4, cp.both
    assert "ASK untracked=1 slug=demo: docs/plans/other.md" in cp.out
    assert "rerun with --untracked stash|keep, or set it with sl session untracked stash|keep" in cp.out
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
    host = repo / ".superlite" / "worktrees" / "sessions" / "host"
    (host / "docs" / "plans" / "other.md").write_text("x\n")
    before = (reg_of(repo), branch(host))
    cp = run("create", "--slug", "two", cwd=repo)
    assert cp.returncode == 4 and "ASK untracked=1 slug=demo: docs/plans/other.md" in cp.out
    assert (reg_of(repo), branch(host)) == before
    assert run("create", "--slug", "wt", "--worktree", cwd=repo).returncode == 0
    cp = run("create", "--slug", "two", "--untracked", "stash", cwd=repo)
    assert cp.returncode == 0 and "session: stashed 1 untracked file(s) with demo: docs/plans/other.md" in cp.out
    assert not (host / "docs" / "plans" / "other.md").exists()
    (host / "docs" / "plans" / "o3.md").write_text("y\n")
    run("untracked", "keep", cwd=repo)
    cp = run("create", "--slug", "three", cwd=repo)
    assert cp.returncode == 0 and "session: kept 1 untracked file(s) in place: docs/plans/o3.md" in cp.out
    assert (host / "docs" / "plans" / "o3.md").is_file()


def test_ensure_leaves_committed_gitignore_and_status_clean(repo):
    before = (repo / ".gitignore").read_text()
    assert run("ensure", "--slug", "demo", cwd=repo).returncode == 0
    assert (repo / ".gitignore").read_text() == before
    assert git(repo, "status", "--porcelain").strip() == ""
    from subprocess import run as sp
    assert sp(["git", "check-ignore", "-q", ".superlite/x"], cwd=repo).returncode == 0
    session.mark_run(repo, "finished")
    assert run("close", "--slug", "demo", "--keep-branch", cwd=repo).returncode == 0
    assert row_of(repo, "demo")["has_stash"] is False
    assert "stash:no" in run("status", cwd=repo).out


def test_no_gitignore_uses_info_exclude(repo):
    git(repo, "rm", "-q", ".gitignore")
    git(repo, "commit", "-q", "-m", "no ignore")
    assert run("ensure", "--slug", "demo", cwd=repo).returncode == 0
    assert not (repo / ".gitignore").exists()
    assert ".superlite/" in (repo / ".git" / "info" / "exclude").read_text().splitlines()
    assert git(repo, "status", "--porcelain").strip() == ""
    run("ensure", "--slug", "next", cwd=repo)
    assert (repo / ".git" / "info" / "exclude").read_text().splitlines().count(".superlite/") == 1
