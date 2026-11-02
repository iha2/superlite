import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import base  # noqa: E402
import ident  # noqa: E402
import journal  # noqa: E402
import planfile  # noqa: E402
import state  # noqa: E402

PLAN = textwrap.dedent("""\
    # Plan

    ```mermaid
    flowchart LR
      T1 --> T2
      T1 --> T3
      %% taskgraph
      T1 --> T2
      T1 --> T3
    ```

    ### T1 first
    **Owns:** src/a.py
    **Produces:** `make(n: int) -> Box`
      e.g. `make(1)` -> `Box(1)`

    ### T2 second
    **Owns:** src/{a,b}.py, tests/test_a.py
    **Consumes:** `make(n: int) -> Box` (T1)
    **Produces:** `use(b: Box) -> str`

    ### T3 third
    **Owns:** src/c.py
    **Consumes:** `use(b: Box) -> str` (T2)
    """)


@pytest.fixture
def plan(tmp_path):
    p = tmp_path / "p.md"
    p.write_text(PLAN, encoding="utf-8")
    return planfile.read(str(p))


def test_plan_fields_and_braces(plan):
    assert plan.tasks["T2"].owns == ["src/a.py", "src/b.py", "tests/test_a.py"]
    assert plan.tasks["T1"].examples == ["make(1)` -> `Box(1)"]
    assert plan.tasks["T2"].consumes == ["make(n: int) -> Box` (T1)"]
    assert plan.edges == [("T1", "T2"), ("T1", "T3")]


def test_example_edge_not_waited(plan):
    # T1 -> T2 carries make(), which has an e.g.; T1 -> T3 carries nothing, so it stays
    assert plan.waited_edges() == [("T1", "T3")]


def test_read_json_missing_default(tmp_path):
    assert base.read_json(tmp_path / "missing.json", {}) == {}


def test_locked_update_example(tmp_path):
    p = tmp_path / "s.json"
    assert base.locked_update(p, lambda d: {**d, "a": 1}) == {"a": 1}


def test_locked_update_two_processes_100(tmp_path):
    p = tmp_path / "n.json"
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import base; from pathlib import Path\n"
            "for _ in range(50): base.locked_update(Path(sys.argv[2]), lambda d: {'n': d.get('n', 0) + 1})")
    procs = [subprocess.Popen([sys.executable, "-c", code, str(SCRIPTS), str(p)]) for _ in range(2)]
    assert [x.wait(timeout=60) for x in procs] == [0, 0]
    assert base.read_json(p, {}) == {"n": 100}


def test_write_json_killed_mid_write_leaves_no_partial(tmp_path):
    p = tmp_path / "big.json"
    base.write_json(p, {"v": "old"})
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import base; from pathlib import Path\n"
            "while True: base.write_json(Path(sys.argv[2]), {'v': 'x' * 2_000_000})")
    proc = subprocess.Popen([sys.executable, "-c", code, str(SCRIPTS), str(p)])
    try:
        for _ in range(30):
            time.sleep(0.02)
            assert base.read_json(p, None) is not None
    finally:
        proc.kill()
        proc.wait()
    assert base.read_json(p, None) is not None  # whole old or whole new, never partial


def test_state_path_example():
    assert state.path(Path("/r/wt"), "pool.json") == Path("/r/wt/.superlite/pool.json")


def test_hub_root_is_main_checkout(tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    base.git("init", "-q", cwd=main)
    base.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x", cwd=main)
    wt = tmp_path / "wt"
    base.git("worktree", "add", "-q", str(wt), "-b", "b", cwd=main)
    assert ident.same_path(state.hub_root(wt), main)


def _commit(cwd):
    base.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x", cwd=cwd)


def test_hub_root_of_a_submodule_is_its_own_checkout(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    base.git("init", "-q", cwd=lib)
    _commit(lib)
    sup = tmp_path / "sup"
    sup.mkdir()
    base.git("init", "-q", cwd=sup)
    base.git("-c", "protocol.file.allow=always", "submodule", "add", "-q", str(lib), "mod", cwd=sup)
    assert ident.same_path(state.hub_root(sup / "mod"), sup / "mod")


def test_hub_root_of_a_separate_git_dir_is_its_own_checkout(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    base.git("init", "-q", "--separate-git-dir", str(tmp_path / "gitdir"), cwd=work)
    _commit(work)
    assert ident.same_path(state.hub_root(work), work)


def test_within_whole_components():
    assert ident.within(Path("/r/host-extra"), Path("/r/host")) is False
    assert ident.within(Path("/r/host/sub"), Path("/r/host")) is True
    assert ident.same_path(Path("/r/host"), Path("/r/host/../host"))


def test_git_error_does_not_exit(tmp_path):
    with pytest.raises(base.GitError, match="not a git repository|fatal"):
        base.git("rev-parse", "HEAD", cwd=tmp_path)
    assert base.git("rev-parse", "HEAD", cwd=tmp_path, check=False).returncode != 0


def test_journal_undoes_newest_first():
    order = []
    with pytest.raises(journal.JournalError, match="second"):
        with journal.Journal() as j:
            j.undo(lambda: order.append(1), "first")
            j.undo(lambda: (_ for _ in ()).throw(RuntimeError("bad")), "bad undo")
            j.undo(lambda: order.append(3), "second")
            raise ValueError("boom")
    assert order == [3, 1]


def test_journal_clean_exit_runs_no_undo():
    ran = []
    with journal.Journal() as j:
        j.undo(lambda: ran.append(1))
    assert ran == []


def test_flag_precedence(tmp_path, monkeypatch):
    f = tmp_path / "c.json"
    base.write_json(f, {"k": True})
    monkeypatch.delenv("SL_T_FLAG", raising=False)
    assert base.flag("SL_T_FLAG", f, "k", False) is True
    monkeypatch.setenv("SL_T_FLAG", "off")
    assert base.flag("SL_T_FLAG", f, "k", True) is False
    assert base.flag("NOPE_X", tmp_path / "none.json", "k", True) is True


@pytest.mark.skipif(os.name == "nt", reason="posix")
def test_spawn_and_kill_tree(tmp_path):
    pid = base.spawn_background([sys.executable, "-c", "import time; time.sleep(60)"], tmp_path / "l.log")
    base.kill_tree(pid)
    for _ in range(50):
        try:
            os.waitpid(pid, os.WNOHANG)
            os.kill(pid, 0)
        except (ChildProcessError, ProcessLookupError):
            return
        time.sleep(0.05)
    pytest.fail("process still alive")


def _repo(tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    base.git("init", "-q", cwd=main)
    base.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x", cwd=main)
    return main


def test_linked_worktree_does_not_see_parent_state(tmp_path):
    main = _repo(tmp_path)
    wt = tmp_path / "wt"
    base.git("worktree", "add", "-q", str(wt), "-b", "b", cwd=main)
    assert ident.same_path(state.run_dir(wt), wt / ".superlite")
    assert not ident.same_path(state.run_dir(wt), state.run_dir(main))


def test_subfolder_resolves_to_toplevel(tmp_path):
    main = _repo(tmp_path)
    sub = main / "a" / "b"
    sub.mkdir(parents=True)
    assert ident.same_path(state.run_dir(sub), main / ".superlite")


def test_journal_interrupt_runs_every_undo_and_propagates():
    undone = []
    with pytest.raises(KeyboardInterrupt):
        with journal.Journal() as j:
            j.step("one")
            j.undo(lambda: undone.append(1))
            j.step("two")
            j.undo(lambda: undone.append(2))
            raise KeyboardInterrupt()
    assert undone == [2, 1]


def test_journal_system_exit_runs_undos_and_propagates():
    undone = []
    with pytest.raises(SystemExit):
        with journal.Journal() as j:
            j.undo(lambda: undone.append(1))
            raise SystemExit(3)
    assert undone == [1]


def test_journal_keeps_original_error_as_cause():
    with pytest.raises(journal.JournalError) as ei:
        with journal.Journal() as j:
            j.step("copy")
            raise OSError("disk full")
    assert isinstance(ei.value.__cause__, OSError) and "disk full" in str(ei.value)


def test_graph_edges_chain_fan_and_dedupe(tmp_path):
    p = tmp_path / "g.md"
    p.write_text("### T1 a\n### T2 b\n### T3 c\n```mermaid\n%% taskgraph\nflowchart LR\n"
                 "T1 --> T2 & T3\nT1 --> T2\nT2[\"T9 x\"] -->|l| T3\n```\n", encoding="utf-8")
    assert planfile.read(str(p)).edges == [("T1", "T2"), ("T1", "T3"), ("T2", "T3")]


def _owns(text):
    return planfile.Task("T1", fields={"owns": [text]}).owns


def test_owns_drops_note_after_braced_path():
    assert _owns("pkg/{d,e}.py (the cli), tests/test_d.py") == ["pkg/d.py", "pkg/e.py", "tests/test_d.py"]


def test_owns_drops_note_after_plain_path():
    assert _owns("`a/b.py` (new, tiny)") == ["a/b.py"]


def test_owns_no_note_and_glob():
    assert _owns("a/b.py") == ["a/b.py"]
    assert _owns("src/**/*.py, docs/") == ["src/**/*.py", "docs/"]


def test_owns_matches_next_tasks_on_every_plan():
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "skills" / "swarm" / "scripts"))
    import next_tasks
    files = list((root / "docs" / "plans").glob("*.md")) + list((root / "tests" / "scripts" / "fixtures").rglob("*.md"))
    assert files
    for f in files:
        try:
            pl = planfile.read(str(f))
        except Exception:
            continue
        for tid, t in pl.tasks.items():
            if len(t.fields.get("owns", [])) != 1:
                continue  # prose continuation lines: not a path list
            raw = t.fields["owns"][0]
            assert t.owns == next_tasks.parse_owns(raw), (f.name, tid)


def test_write_json_leaves_no_temp_file_when_the_dump_fails(tmp_path):
    with pytest.raises(TypeError):
        base.write_json(tmp_path / "x.json", {"k": object()})
    assert list(tmp_path.iterdir()) == []


def test_advisory_errors_cover_data_faults_but_not_bugs():
    errs = base.advisory_errors()
    assert issubclass(OSError, errs) and issubclass(planfile.PlanError, errs) and issubclass(base.GitError, errs)
    assert not issubclass(TypeError, errs) and not issubclass(AttributeError, errs)


def test_journal_an_undo_raising_typeerror_does_not_stop_the_next_undo(capsys):
    order = []
    with pytest.raises(journal.JournalError):
        with journal.Journal() as j:
            j.undo(lambda: order.append(1), "first")
            j.undo(lambda: (_ for _ in ()).throw(TypeError("bug")), "buggy")
            raise ValueError("boom")
    assert order == [1]
    assert "buggy" in capsys.readouterr().err


def test_bash_prefers_superlite_bash(monkeypatch):
    monkeypatch.setenv("SUPERLITE_BASH", "/opt/bash")
    assert base.bash() == "/opt/bash"


def test_git_bash_is_found_next_to_git_not_the_wsl_stub(tmp_path):
    # A bare "bash" on Windows finds System32\bash.exe (WSL) before PATH, so base.bash() asks git_bash first.
    git_root = tmp_path / "Git"
    (git_root / "cmd").mkdir(parents=True)
    (git_root / "bin").mkdir()
    (git_root / "bin" / "bash.exe").write_text("")
    assert base.git_bash(str(git_root / "cmd" / "git.exe"), str(tmp_path / "none")) == str(git_root / "bin" / "bash.exe")
    assert base.git_bash(str(git_root / "mingw64" / "bin" / "git.exe"), str(tmp_path / "none")) == \
        str(git_root / "bin" / "bash.exe")   # a portable Git puts mingw64\bin on PATH
    assert base.git_bash(None, str(tmp_path)) == str(git_root / "bin" / "bash.exe")
    assert base.git_bash(None, str(tmp_path / "none")) is None


def test_win_retry_rides_out_a_brief_sharing_violation(monkeypatch):
    # Windows: a reader of a file being os.replace'd (or a replace over an open file) gets PermissionError briefly.
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError("in use")
        return "ok"
    monkeypatch.setattr(base.os, "name", "nt")
    monkeypatch.setattr(base.time, "sleep", lambda s: None)
    assert base._win_retry(flaky) == "ok" and len(calls) == 3


def test_win_retry_runs_once_off_windows(monkeypatch):
    calls = []

    def denied():
        calls.append(1)
        raise PermissionError("denied")
    monkeypatch.setattr(base.os, "name", "posix")
    with pytest.raises(PermissionError):
        base._win_retry(denied)
    assert len(calls) == 1
