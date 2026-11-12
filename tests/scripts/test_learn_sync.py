"""learn.py: sync, note, spawn and main, against the real lessons_store, lessons_score and learn_signals."""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import base  # noqa: E402
import learn  # noqa: E402
import lessons_store as lessons  # noqa: E402


def jl(path, *rows, raw=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
        if raw:
            fh.write(raw)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A checkout folder (not a git repo), its run dir, and an isolated history home."""
    repo = tmp_path / "repo"
    sl = repo / ".superlite"
    sl.mkdir(parents=True)
    home = tmp_path / "home"
    monkeypatch.setenv("SUPERLITE_HOME", str(home))
    tasks: dict = {}
    monkeypatch.setattr(learn, "_tasks", lambda _sl, _plan: tasks)
    monkeypatch.setattr(learn, "_history_rows", lambda _plan, _sl, run: [
        {"v": 1, "run": run, "task": tid, "first_time_pass": t.get("first_time_pass"),
         "rework_cost": t.get("rework_cost", 0), "cost_usd": t.get("cost_usd", 0)} for tid, t in sorted(tasks.items())])
    return type("Env", (), {"sl": sl, "hub": sl, "home": home, "tasks": tasks, "repo": repo})()


def sync(e, run="r1", plan=None, **kw):
    return learn.sync(e.sl, plan, run, **kw)


def store(e):
    return lessons.load(e.hub)


def inc(e, ctx="assert fails"):
    jl(e.sl / "incidents.jsonl", {"file": "skills/x/y.py", "kind": "test-failure", "context": ctx})


def hist(e):
    p = e.home / "history.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def test_incident_becomes_pitfall(env):
    inc(env)
    sync(env)
    les = next(iter(store(env)["lessons"].values()))
    assert les["kind"] == "pitfall" and les["module"] == "skills/x" and les["text"] == "avoid: assert fails"
    assert les["sources"] == ["r1:incidents.jsonl:0+0"]


def test_two_runs_admit_and_second_sync_empty(env):
    inc(env)
    sync(env, "r1")
    inc(env)
    ev = sync(env, "r2")
    assert len(ev) == 1 and ev[0].startswith("admitted L-")
    assert sync(env, "r2") == []


def test_practices_from_first_time_pass(env):
    env.tasks["T1"] = {"owns": ["skills/x/a.py"], "first_time_pass": True,
                       "changed": ["skills/x/a.py", "skills/x/test_a.py"], "tests_run": ["test_a.py"]}
    sync(env, plan="p.md")
    texts = sorted(x["text"] for x in store(env)["lessons"].values())
    assert texts == ["do: change skills/x/a.py with skills/x/test_a.py", "do: run test_a.py"]
    assert all(x["kind"] == "practice" for x in store(env)["lessons"].values())


def test_no_practice_when_module_has_finding(env):
    env.tasks["T1"] = {"owns": ["skills/x/a.py"], "first_time_pass": True, "changed": ["skills/x/a.py"],
                       "tests_run": ["t.py"]}
    jl(env.sl / "review-findings.jsonl", {"file": "skills/x/a.py", "problem": "p", "category": "state"})
    sync(env, plan="p.md")
    assert [x["kind"] for x in store(env)["lessons"].values()] == ["pitfall"]


FIX_NOTE = ("# F-T2-1 · tier 1 · round 1 · FIXED\n"
            "Goal: resolve run paths through state\n"
            "Where: skills/x/y.py:10-20, tests/scripts/test_y.py:5-9\n"
            "Changed: skills/x/y.py:12 - use state.path - the hard-coded dir broke worktrees\n"
            "Tried and failed: nothing\n"
            "Evidence: test_y::test_path - FileNotFoundError\n"
            "Next: none\n")


def test_fix_note_verified(env):
    """The frozen fix-note format (skills/swarm/fixing.md) becomes a verified practice keyed `fix:`."""
    (env.sl / "fixes").mkdir()
    (env.sl / "fixes" / "F-T2-1.md").write_text(FIX_NOTE, encoding="utf-8")
    (env.sl / "fixes" / "F-T2-2.md").write_text(FIX_NOTE.replace("F-T2-1", "F-T2-2").replace("FIXED", "NOT_FIXED"),
                                               encoding="utf-8")
    ev = sync(env)
    les = list(store(env)["lessons"].values())
    assert len(les) == 1 and les[0]["kind"] == "practice" and les[0]["module"] == "skills/x"
    assert les[0]["text"] == "do: use state.path (the hard-coded dir broke worktrees)"
    assert les[0]["key"].startswith("fix:") and les[0]["sources"] == ["r1:fixes:F-T2-1.md"]
    assert les[0]["status"] == "cached" and ev[0].startswith("admitted")


def test_fix_note_windows_code_page_and_crlf(env):
    """A note saved by a Windows editor (cp1252 `·`, CRLF, backslash paths) still parses."""
    (env.sl / "fixes").mkdir()
    note = FIX_NOTE.replace("skills/x/y.py", "skills\\x\\y.py").replace("\n", "\r\n")
    (env.sl / "fixes" / "F-T2-1.md").write_bytes(note.encode("cp1252"))
    sync(env)
    les = list(store(env)["lessons"].values())
    assert len(les) == 1 and les[0]["module"] == "skills/x" and les[0]["kind"] == "practice"


def test_fix_note_left_live_is_not_a_second_run(env):
    (env.sl / "fixes").mkdir()
    (env.sl / "fixes" / "F-T2-1.md").write_text(FIX_NOTE, encoding="utf-8")
    sync(env, "r1")
    sync(env, "r2")
    les = next(iter(store(env)["lessons"].values()))
    assert les["runs"] == ["r1"] and les["sources"] == ["r1:fixes:F-T2-1.md"]


def test_fixed_practice_can_be_proven(env):
    (env.sl / "fixes").mkdir()
    (env.sl / "fixes" / "F-T2-1.md").write_text(FIX_NOTE, encoding="utf-8")
    sync(env, "r1")
    lid = next(iter(store(env)["lessons"]))
    env.tasks["T1"] = {"owns": ["skills/x/y.py"], "first_time_pass": True}
    for n in range(2, 8):
        jl(env.sl / "deliveries.jsonl", {"run": f"r{n}", "task": "T1", "lessons": [lid]})
        ev = sync(env, f"r{n}", plan="p.md")
        if any(e.startswith("proven") for e in ev):
            break
    assert store(env)["lessons"][lid]["proven"] is True


def test_learned_row_key_is_tag(env):
    learn.note(env.sl, "r1", "T1", "pairing", "a.py goes with test_a.py")
    assert json.loads((env.sl / "learned.jsonl").read_text()) == {
        "v": 1, "run": "r1", "task": "T1", "tag": "pairing", "text": "a.py goes with test_a.py"}
    env.tasks["T1"] = {"owns": ["skills/x/a.py"]}
    sync(env, plan="p.md")
    les = next(iter(store(env)["lessons"].values()))
    assert les["key"] == "pairing" and les["text"] == "a.py goes with test_a.py"
    assert lessons.lesson_id("practice", "skills/x", "pairing") in store(env)["lessons"]


def test_learned_waits_for_task_ownership(env):
    """A plan-less sync (review recording, session close, registration) never files a learned line under `.`."""
    learn.note(env.sl, "r1", "T1", "test", "run pytest -x")
    sync(env)
    assert store(env)["lessons"] == {}
    env.tasks["T1"] = {"owns": ["skills/x/a.py"]}
    sync(env, plan="p.md")
    assert [x["module"] for x in store(env)["lessons"].values()] == ["skills/x"]


def test_planless_sync_recovers_the_runs_plan(env, tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text("# plan\n")
    env.tasks["T1"] = {"owns": ["skills/x/y.py"], "first_time_pass": False}
    sync(env, plan=str(plan))
    env.tasks["T1"] = {"owns": ["skills/x/y.py"], "first_time_pass": True}
    sync(env, status="finished")  # session close: no --plan
    rows = hist(env)
    assert len(rows) == 2 and rows[-1]["first_time_pass"] is True and rows[-1]["status"] == "finished"


def test_outbox_only_for_hooks_and_mismatches(env):
    jl(env.sl / "incidents.jsonl",
       {"kind": "rule-block", "agent": "a", "tool": "Bash", "command": "rm /secret/x"},
       {"kind": "rule-block", "agent": "a", "tool": "Bash", "command": "rm y"},
       {"kind": "rule-rewrite", "agent": "a", "tool": "Edit", "target": "/home/u/f"})
    jl(env.sl / "metrics.jsonl", {"ev": "launch", "agent_id": "a1", "type": "superlite:implementer",
                                  "model": "claude-opus-5-5"})
    (env.sl / "dispatch.json").write_text(json.dumps({"batches": [["T1"]], "lines": ["T1 implementer"]}))
    sync(env)
    out = env.home / "learn" / "outbox.jsonl"
    rows = [json.loads(x) for x in out.read_text().splitlines()]
    assert {(r["signal"], r.get("tool"), r.get("count")) for r in rows if r["signal"] != "model-mismatch"} == \
        {("hook-block", "Bash", 2), ("hook-rewrite", "Edit", 1)}
    assert any(r["signal"] == "model-mismatch" and r["requested"] == "sonnet" and r["ran"] == "opus" for r in rows)
    assert len({r["run"] for r in rows}) == 1 and "r1" not in {r["run"] for r in rows}
    assert len({r["id"] for r in rows}) == len(rows)
    blob = json.dumps(rows)
    assert "/secret" not in blob and "/home" not in blob and str(env.repo) not in blob
    assert store(env)["lessons"] == {}
    sync(env)
    assert len(out.read_text().splitlines()) == len(rows)


def test_partial_line_waits(env):
    jl(env.sl / "incidents.jsonl", raw='{"file": "skills/x/y.py", "kind": "k", "context": "c"')
    sync(env)
    assert store(env)["lessons"] == {}
    with open(env.sl / "incidents.jsonl", "a") as fh:
        fh.write("}\n")
    sync(env)
    assert len(store(env)["lessons"]) == 1


def test_crash_between_store_save_and_watermark(env):
    inc(env)
    env.tasks["T1"] = {"owns": ["skills/x/y.py"], "first_time_pass": True, "changed": []}
    jl(env.sl / "incidents.jsonl", {"kind": "rule-block", "tool": "Bash"})
    sync(env, plan="p.md")
    (env.hub / "learn-state.json").unlink()  # watermark lost, store and history kept
    sync(env, plan="p.md")
    assert all(len(x["sources"]) == len(set(x["sources"])) == 1 for x in store(env)["lessons"].values())
    assert len(hist(env)) == 1
    assert len((env.home / "learn" / "outbox.jsonl").read_text().splitlines()) == 1


def test_history_newest_outcome_wins(env):
    env.tasks["T1"] = {"owns": ["skills/x/y.py"], "first_time_pass": False, "rework_cost": 2, "cost_usd": 1}
    sync(env, plan="p.md", status="unfinished")
    sync(env, plan="p.md", status="unfinished")
    assert len(hist(env)) == 1
    env.tasks["T1"] = {"owns": ["skills/x/y.py"], "first_time_pass": True, "rework_cost": 0, "cost_usd": 1}
    sync(env, plan="p.md", status="finished")
    rows = hist(env)
    assert len(rows) == 2 and rows[-1]["first_time_pass"] is True and rows[-1]["status"] == "finished"
    assert rows[-1]["repository"] and rows[-1]["findings"] == 0
    assert store(env)["modules"]["skills/x"]["r1"] == {"tasks": 1, "ftp": 1}


def test_delivery_hit_newest_outcome(env):
    inc(env)
    sync(env, "r1")
    inc(env)
    sync(env, "r2")
    lid = next(iter(store(env)["lessons"]))
    jl(env.sl / "deliveries.jsonl", {"run": "r2", "task": "T1", "agent": "implementer", "lessons": [lid]})
    env.tasks["T1"] = {"owns": ["skills/x/y.py"], "first_time_pass": False}
    sync(env, "r2", plan="p.md")
    assert store(env)["lessons"][lid]["deliveries"] == [{"run": "r2", "task": "T1", "hit": False}]
    env.tasks["T1"] = {"owns": ["skills/x/y.py"], "first_time_pass": True}
    sync(env, "r2", plan="p.md")
    sync(env, "r2", plan="p.md")
    assert store(env)["lessons"][lid]["deliveries"] == [{"run": "r2", "task": "T1", "hit": True}]


def test_pitfall_not_hit_when_key_found(env):
    jl(env.sl / "review-findings.jsonl", {"file": "skills/x/y.py", "problem": "p", "category": "state"})
    sync(env, "r1")
    jl(env.sl / "review-findings.jsonl", {"file": "skills/x/y.py", "problem": "p", "category": "state"})
    sync(env, "r2")
    lid = next(iter(store(env)["lessons"]))
    jl(env.sl / "deliveries.jsonl", {"run": "r2", "task": "T1", "lessons": [lid]})
    env.tasks["T1"] = {"owns": ["skills/x/y.py"], "first_time_pass": True}
    sync(env, "r2", plan="p.md")
    assert store(env)["lessons"][lid]["deliveries"][0]["hit"] is False


def test_no_plan_ok(env):
    """A sync without a plan keeps the outcomes an earlier plan sync recorded."""
    env.tasks["T1"] = {"owns": ["skills/x/y.py"], "first_time_pass": True}
    sync(env, plan="p.md")
    inc(env)
    sync(env)
    assert store(env)["modules"]["skills/x"]["r1"] == {"tasks": 1, "ftp": 1}
    assert len(hist(env)) == 1 and len(store(env)["lessons"]) == 1


def test_final_prints_summary(env, capsys):
    inc(env)
    sync(env, "r1", final=True)
    inc(env)
    sync(env, "r2", final=True)
    assert capsys.readouterr().out.splitlines() == [
        "learn: admitted 0, evicted 0, proven 0", "learn: admitted 1, evicted 0, proven 0"]


def test_run_id_from_current_run(env):
    (env.sl / "current-run.json").write_text(json.dumps(
        {"id": "20261112-000000-a", "topic": "t", "slug": "s", "started_at": "2026-11-12T00:00:00Z"}))
    inc(env)
    learn.sync(env.sl)
    assert next(iter(store(env)["lessons"].values()))["runs"] == ["20261112-000000-a"]


def test_changed_files_from_checkpoint(tmp_path):
    base.git("init", "-q", cwd=tmp_path)
    base.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "base", cwd=tmp_path)
    (tmp_path / "a.py").write_text("x\n")
    (tmp_path / "test_a.py").write_text("y\n")
    base.git("add", "a.py", "test_a.py", cwd=tmp_path)
    base.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x", cwd=tmp_path)
    sha = base.git("rev-parse", "HEAD", cwd=tmp_path).stdout.strip()
    sl = tmp_path / ".superlite"
    sl.mkdir()
    (sl / "progress.md").write_text(f"- checkpoint T1 {sha[:10]} title\n")
    assert learn._checkpoint_shas(sl) == {"T1": sha[:10]}
    assert learn._changed(sl, sha[:10]) == ["a.py", "test_a.py"]
    assert learn._changed(sl, "deadbeef") == []
    assert learn._changed(tmp_path / "nowhere" / ".superlite", sha) == []


def test_spawn_detached(env, monkeypatch):
    seen = {}
    monkeypatch.setattr(base, "spawn_background", lambda argv, log: seen.update(argv=argv, log=log) or 1)
    learn.spawn(env.sl, "plan.md")
    assert seen["argv"][1:] == [str(Path(learn.__file__).resolve()), "sync", "--detached", "--plan", "plan.md"]
    assert seen["log"] == env.repo / ".superlite" / "learn.log"

    def boom(argv, log):
        raise OSError("no")
    monkeypatch.setattr(base, "spawn_background", boom)
    learn.spawn(env.sl)  # never raises


def test_main_sync_note_and_errors(env, capsys):
    sl = str(env.sl)
    assert learn.main(["note", "--sl", sl, "--run", "r1", "--task", "T1", "--tag", "test", "--text", "x"]) == 0
    assert learn.main(["sync", "--sl", sl, "--run", "r1"]) == 0
    (env.hub / "lessons.json").write_text("{not json")
    assert learn.main(["sync", "--sl", sl, "--run", "r1"]) == 0


def test_main_sync_without_args(env, monkeypatch):
    monkeypatch.chdir(env.repo)
    assert learn.main(["sync"]) == 0


def test_no_network_imports():
    banned = {"socket", "urllib", "http", "requests", "ftplib", "smtplib", "ssl"}
    for f in (SCRIPTS / "learn.py", SCRIPTS / "learn_signals.py", SCRIPTS / "lessons_store.py", SCRIPTS / "lessons.py"):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            assert not {n.split(".")[0] for n in names} & banned, f.name


def test_version_from_manifest():
    assert learn._version() == json.loads((SCRIPTS.parents[2] / ".claude-plugin" / "plugin.json").read_text())["version"]


def test_run_change_keeps_file_boundary(env):
    """Live signal files kept across a run change (ensure_run copies them) are not re-read for the new run."""
    inc(env)
    jl(env.sl / "incidents.jsonl", {"kind": "rule-block", "tool": "Bash"})
    sync(env, "r1")
    assert sync(env, "r2") == []
    les = next(iter(store(env)["lessons"].values()))
    assert les["runs"] == ["r1"] and les["status"] == "candidate"
    assert len((env.home / "learn" / "outbox.jsonl").read_text().splitlines()) == 1


def test_row_run_id_is_honoured(env):
    """An unsynced row of the earlier run, read after the run changed, still counts for the run that wrote it."""
    jl(env.sl / "incidents.jsonl", {"file": "skills/x/y.py", "kind": "test-failure", "context": "c", "run": "r1"})
    sync(env, "r2")
    les = next(iter(store(env)["lessons"].values()))
    assert les["runs"] == ["r1"] and les["status"] == "candidate"
    assert store(env)["run_index"] == ["r1", "r2"]


def test_replaced_file_restarts_offset(env):
    for _ in range(3):
        inc(env, "old")
    sync(env, "r1")
    (env.sl / "incidents.jsonl").unlink()  # archived away; the new run's file grows past the old offset
    for _ in range(4):
        inc(env, "new " + "x" * 10)
    sync(env, "r2")
    assert any(x["text"].startswith("avoid: new") for x in store(env)["lessons"].values())


def test_verified_rows_are_not_pitfalls(env):
    jl(env.sl / "incidents.jsonl", {"file": "skills/x/y.py", "kind": "verified", "context": "run_tests full: 8 pass"})
    sync(env, "r1")
    jl(env.sl / "incidents.jsonl", {"file": "skills/x/y.py", "kind": "verified", "context": "run_tests full: 8 pass"})
    sync(env, "r2")
    assert store(env)["lessons"] == {}


def test_later_outcome_retracts_task_practice(env):
    """Two runs whose tasks first look like first-time passes but end with a finding never admit the practice."""
    for run in ("r1", "r2"):
        env.tasks["T1"] = {"owns": ["skills/x/a.py"], "first_time_pass": True,
                           "changed": ["skills/x/a.py", "skills/x/test_a.py"], "tests_run": ["skills/x/test_a.py"]}
        sync(env, run, plan="p.md")
        env.tasks["T1"] = dict(env.tasks["T1"], first_time_pass=False)
        sync(env, run, plan="p.md")
    assert not [x for x in store(env)["lessons"].values() if x["kind"] == "practice"]


def test_retract_keeps_other_tasks_evidence(env):
    for tid in ("T1", "T2"):
        env.tasks[tid] = {"owns": [f"skills/x/{tid}.py"], "first_time_pass": True,
                          "changed": ["skills/x/a.py"], "tests_run": ["skills/x/test_a.py"]}
    sync(env, "r1", plan="p.md")
    env.tasks["T1"] = dict(env.tasks["T1"], first_time_pass=False)
    sync(env, "r1", plan="p.md")
    (les,) = store(env)["lessons"].values()
    assert les["sources"] == ["r1:tasks.jsonl:T2"] and les["runs"] == ["r1"]


def test_findings_counted_once_by_file_owner(env):
    env.tasks["T1"] = {"owns": ["pkg/a.py"], "first_time_pass": False}
    env.tasks["T2"] = {"owns": ["pkg/b.py"], "first_time_pass": True}
    jl(env.sl / "review-findings.jsonl", {"run": "r1", "file": "pkg/a.py", "problem": "p", "category": "state"})
    sync(env, plan="p.md")
    assert {r["task"]: r["findings"] for r in hist(env)} == {"T1": 1, "T2": 0}


def test_outbox_failure_does_not_lose_hook_rows(env, monkeypatch):
    jl(env.sl / "incidents.jsonl", {"kind": "rule-block", "tool": "Bash"})
    real = learn._append

    def boom(path, rows):
        if path.name == "outbox.jsonl":
            raise OSError("disk full")
        real(path, rows)
    monkeypatch.setattr(learn, "_append", boom)
    with pytest.raises(OSError):
        sync(env)
    monkeypatch.setattr(learn, "_append", real)
    sync(env)
    sync(env)
    rows = [json.loads(x) for x in (env.home / "learn" / "outbox.jsonl").read_text().splitlines()]
    assert [(r["signal"], r["count"]) for r in rows] == [("hook-block", 1)]
    assert store(env)["outbox_pending"] == []


def test_publish_dedupes_by_id(env):
    jl(env.sl / "incidents.jsonl", {"kind": "rule-block", "tool": "Bash"})
    sync(env)
    out = env.home / "learn" / "outbox.jsonl"
    rows = [json.loads(x) for x in out.read_text().splitlines()]
    learn._publish(env.hub, out, {"outbox_pending": rows})  # a crash before unstaging repeats the publish
    assert len(out.read_text().splitlines()) == 1


def test_concurrent_syncs_keep_each_others_offsets(tmp_path, monkeypatch):
    """Two worktrees syncing at once into one hub: the slower one must not save a stale offsets snapshot."""
    import threading
    import time
    monkeypatch.setenv("SUPERLITE_HOME", str(tmp_path / "home"))
    hub = tmp_path / "hub"
    monkeypatch.setattr(lessons, "hub_dir", lambda _cwd: hub)
    sl_a, sl_b = tmp_path / "a" / ".superlite", tmp_path / "b" / ".superlite"
    for sl in (sl_a, sl_b):
        jl(sl / "incidents.jsonl", {"file": "skills/x/y.py", "kind": "test-failure", "context": "c"})
    real, threads = learn._fold_incidents, []

    def slow(store, rows, start, run, ctx):
        if run == "rA" and not threads:
            threads.append(threading.Thread(target=learn.sync, args=(sl_b, None, "rB")))
            threads[0].start()
            time.sleep(0.4)
        return real(store, rows, start, run, ctx)
    monkeypatch.setattr(learn, "_fold_incidents", slow)
    learn.sync(sl_a, None, "rA")
    threads[0].join(10)
    offsets = json.loads((hub / "learn-state.json").read_text())["offsets"]
    assert len([k for k in offsets if k.endswith("incidents.jsonl")]) == 2  # A's offsets survive B's save


RID = "20261112-045224-my-topic"


def _live(e, rid=RID):
    (e.sl / "current-run.json").write_text(json.dumps(
        {"id": rid, "topic": "my topic", "slug": "my-topic", "started_at": "2026-11-12T04:52:24"}))


def _worker(e, monkeypatch, plan="p.md"):
    """The argv a checkpoint's spawn would run, captured now (while the run is live), to be run later."""
    seen = {}
    monkeypatch.setattr(base, "spawn_background", lambda argv, log: seen.update(argv=argv) or 1)
    learn.spawn(e.sl, plan)
    return seen["argv"][2:] + ["--sl", str(e.sl)]


def test_late_checkpoint_sync_after_final_keeps_finished_rows(env, monkeypatch, capsys):
    """A detached sync spawned before finish but locked after its final sync must not rewrite `finished`."""
    _live(env)
    env.tasks["T1"] = {"owns": ["skills/x/a.py"], "first_time_pass": True}
    argv = _worker(env, monkeypatch)
    assert argv[:4] == ["sync", "--detached", "--run", RID]
    learn.sync(env.sl, "p.md", RID, status="finished", final=True)
    inc(env)
    assert learn.main(argv) == 0
    assert [r["status"] for r in hist(env)] == ["finished"]
    assert store(env)["lessons"] == {}  # the worker folded nothing
    learn.sync(env.sl, "p.md", RID)  # nor does any other non-final sync of the sealed run
    assert [r["status"] for r in hist(env)] == ["finished"]


def test_late_checkpoint_sync_after_archive_is_a_no_op(env, monkeypatch):
    """After finish renames or archives the run, the worker reads no emptied folder and writes no live state."""
    _live(env)
    env.tasks["T1"] = {"owns": ["skills/x/a.py"], "first_time_pass": True}
    argv = _worker(env, monkeypatch)
    learn.sync(env.sl, "p.md", RID, status="finished", final=True)
    for p in list(env.sl.iterdir()):  # the archive empties the live folder but the stationary learning files
        if p.name not in ("lessons.json", "learn-state.json") and not p.name.endswith(".lock"):
            p.unlink()
    env.tasks["T1"] = {"owns": ["skills/x/a.py"], "first_time_pass": None}
    before = sorted(p.name for p in env.sl.iterdir())
    learn.main(argv)
    assert store(env)["modules"]["skills/x"][RID] == {"tasks": 1, "ftp": 1}
    assert sorted(p.name for p in env.sl.iterdir()) == before and len(hist(env)) == 1
    _live(env, RID + "-2")  # finish renamed the run's id: the old id's worker is stale too
    learn.main(argv)
    assert RID + "-2" not in store(env)["run_index"]


def test_detached_sync_of_the_live_run_still_folds(env, monkeypatch):
    _live(env)
    inc(env)
    learn.main(_worker(env, monkeypatch))
    assert next(iter(store(env)["lessons"].values()))["runs"] == [RID]


def test_retracted_practice_is_demoted(env):
    """r1 and a provisional r2 admit a practice; r2's revised outcome retracts r2, so it leaves the cache."""
    t = {"owns": ["skills/x/a.py"], "first_time_pass": True, "changed": ["skills/x/a.py"], "tests_run": ["t.py"]}
    env.tasks["T1"] = dict(t)
    sync(env, "r1", plan="p.md")
    sync(env, "r2", plan="p.md")
    assert {x["status"] for x in store(env)["lessons"].values()} == {"cached"}
    env.tasks["T1"] = dict(t, first_time_pass=False)
    ev = sync(env, "r2", plan="p.md")
    assert {x["status"] for x in store(env)["lessons"].values()} == {"candidate"}
    assert [e.split()[0] for e in ev] == ["demoted"]


def test_outbox_run_hides_topic_slug(env):
    _live(env)
    jl(env.sl / "incidents.jsonl", {"kind": "rule-block", "tool": "Bash"})
    learn.sync(env.sl)
    text = (env.home / "learn" / "outbox.jsonl").read_text()
    assert "my-topic" not in text and "045224" not in text and json.loads(text)["run"]


def test_recreated_hook_log_with_same_rows_restarts(env):
    """After finish moves the log away, a new log of identical hook rows (no timestamps) is read from 0."""
    import os
    jl(env.sl / "incidents.jsonl", {"kind": "rule-block", "tool": "Bash"})
    sync(env, "r1")
    (env.sl / "incidents.jsonl").rename(env.repo / "archived.jsonl")  # archive moves it: the inode lives on
    jl(env.sl / "incidents.jsonl", {"kind": "rule-block", "tool": "Bash"}, {"kind": "rule-block", "tool": "Bash"})
    st = (env.repo / "archived.jsonl").stat()
    os.utime(env.sl / "incidents.jsonl", ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    sync(env, "r2")
    rows = [json.loads(x) for x in (env.home / "learn" / "outbox.jsonl").read_text().splitlines()]
    assert sorted(r["count"] for r in rows) == [1, 2]


def test_restored_copy_keeps_offset(env):
    """A run file copied back unchanged (session restore uses copy2: a new inode, the same mtime) keeps its offset."""
    import shutil
    jl(env.sl / "incidents.jsonl", {"kind": "rule-block", "tool": "Bash"})
    sync(env, "r1")
    shutil.copy2(env.sl / "incidents.jsonl", env.repo / "parked.jsonl")
    (env.sl / "incidents.jsonl").unlink()
    shutil.copy2(env.repo / "parked.jsonl", env.sl / "incidents.jsonl")
    sync(env, "r1")
    assert len((env.home / "learn" / "outbox.jsonl").read_text().splitlines()) == 1


def test_outbox_ids_differ_across_repositories(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERLITE_HOME", str(tmp_path / "home"))
    for name in ("a", "b"):
        sl = tmp_path / name / ".superlite"
        jl(sl / "incidents.jsonl", {"kind": "rule-block", "tool": "Bash"})
        learn.sync(sl)  # no current-run marker: both runs are `unknown`
    rows = [json.loads(x) for x in (tmp_path / "home" / "learn" / "outbox.jsonl").read_text().splitlines()]
    assert len(rows) == 2 and len({r["repo"] for r in rows}) == 2


def test_copied_run_tasks_are_not_credited_to_the_new_run(env):
    """ensure_run copies an unfinished run but leaves metrics and the ledger live: its tasks stay with it."""
    env.tasks["T1"] = {"owns": ["skills/x/a.py"], "first_time_pass": True, "agent": "a1", "sha": "abc1234",
                       "changed": ["skills/x/a.py", "skills/x/test_a.py"], "tests_run": ["skills/x/test_a.py"]}
    sync(env, "r1", plan="p.md")
    sync(env, "r2", plan="p.md")
    assert "r2" not in store(env)["modules"]["skills/x"]
    assert {r["run"] for r in hist(env)} == {"r1"}
    assert all(x["runs"] == ["r1"] for x in store(env)["lessons"].values())
    env.tasks["T1"] = dict(env.tasks["T1"], agent="a2", sha="def5678")  # r2 dispatched and checkpointed it again
    sync(env, "r2", plan="p.md")
    assert store(env)["modules"]["skills/x"]["r2"] == {"tasks": 1, "ftp": 1}
    assert {r["run"] for r in hist(env)} == {"r1", "r2"}


def test_incident_category_keys_the_pitfall(env):
    jl(env.sl / "incidents.jsonl",
       {"file": "skills/x/y.py", "kind": "exception", "category": "state", "context": "a"},
       {"file": "skills/x/y.py", "kind": "exception", "category": "encoding", "context": "b"},
       {"file": "skills/x/y.py", "kind": "exception", "context": "c"})
    sync(env, "r1")
    jl(env.sl / "review-findings.jsonl", {"file": "skills/x/y.py", "problem": "p", "category": "state"})
    sync(env, "r2")
    les = store(env)["lessons"]
    assert sorted(x["key"] for x in les.values()) == ["encoding", "exception", "state"]
    assert les[lessons.lesson_id("pitfall", "skills/x", "state")]["runs"] == ["r1", "r2"]


def test_fixer_delivery_scored_by_its_fix(env):
    """A fixer's delivery is a hit when its fix notes say FIXED and the newest test run failed nothing, though the
    task itself (reworked) is no first-time pass."""
    inc(env)
    sync(env, "r1")
    inc(env)
    sync(env, "r2")
    lid = next(iter(store(env)["lessons"]))
    (env.sl / "fixes").mkdir()
    (env.sl / "fixes" / "F-T1-1.md").write_text(FIX_NOTE.replace("F-T2-1", "F-T1-1"), encoding="utf-8")
    (env.sl / "progress.md").write_text("- tests fast passed=3 failed=1 skipped=0 seconds=1.0\n"
                                        "- tests fast passed=4 failed=0 skipped=0 seconds=1.0\n")
    jl(env.sl / "deliveries.jsonl", {"run": "r2", "task": "T1", "agent": "fixer", "lessons": [lid]},
       {"run": "r2", "task": "T1", "agent": "implementer", "lessons": [lid]})
    env.tasks["T1"] = {"owns": ["skills/x/y.py"], "first_time_pass": False}
    sync(env, "r2", plan="p.md")
    assert store(env)["lessons"][lid]["deliveries"] == [{"run": "r2", "task": "T1", "hit": True, "agent": "fixer"},
                                                         {"run": "r2", "task": "T1", "hit": False}]
    with open(env.sl / "progress.md", "a") as fh:
        fh.write("- tests fast passed=3 failed=1 skipped=0 seconds=1.0\n")
    sync(env, "r2", plan="p.md")
    assert store(env)["lessons"][lid]["deliveries"][0]["hit"] is False


def test_findings_owner_matches_globs(env):
    env.tasks["T1"] = {"owns": ["src/**/*.py"], "first_time_pass": False}
    jl(env.sl / "review-findings.jsonl", {"run": "r1", "file": "src/pkg/a.py", "problem": "p", "category": "state"})
    sync(env, plan="p.md")
    assert {r["task"]: r["findings"] for r in hist(env)} == {"T1": 1}
