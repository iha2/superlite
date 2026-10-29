"""Per-worktree sessions: isolate concurrent runs and clean up after themselves."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from test_plan_tools import SCRIPTS, run

SESSION = SCRIPTS / "session.py"
FINISH = SCRIPTS / "finish.py"
ORCH = SCRIPTS / "orchestrator.py"


def test_start_isolates_mvp_paths_and_finish_deletes_the_tree(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    a = run(SESSION, "start", "--host-session", "chat-a", cwd=tmp_path)
    assert a.returncode == 0 and "session: started" in a.out
    sid_a = [ln.split("=", 1)[1].strip() for ln in a.out.splitlines() if ln.startswith("SUPERLITE_SESSION=")][0]
    path_a = tmp_path / ".superlite" / "sessions" / sid_a
    assert path_a.is_dir() and (path_a / "mvp").is_dir()
    monkeypatch.setenv("SUPERLITE_SESSION", sid_a)
    (path_a / "mvp" / "prototype.html").write_text("<html>a</html>", encoding="utf-8")
    (path_a / "progress.md").write_text("- checkpoint T1 abc1234 title\n", encoding="utf-8")
    (path_a / "orchestrator.json").write_text(json.dumps({"mode": "ai", "approved": True, "by": "t", "time": "t"}) + "\n",
                                              encoding="utf-8")

    # Second chat: new session, does not overwrite A's prototype
    monkeypatch.delenv("SUPERLITE_SESSION", raising=False)
    b = run(SESSION, "start", "--host-session", "chat-b", cwd=tmp_path)
    sid_b = [ln.split("=", 1)[1].strip() for ln in b.out.splitlines() if ln.startswith("SUPERLITE_SESSION=")][0]
    assert sid_b != sid_a
    path_b = tmp_path / ".superlite" / "sessions" / sid_b
    (path_b / "mvp" / "prototype.html").write_text("<html>b</html>", encoding="utf-8")
    assert (path_a / "mvp" / "prototype.html").read_text(encoding="utf-8") == "<html>a</html>"

    # finish cleans up session A (ledger path) even when B is active
    monkeypatch.setenv("SUPERLITE_SESSION", sid_b)
    fin = run(FINISH, "--ledger", str(path_a / "progress.md"), cwd=tmp_path)
    assert fin.returncode == 0 and f"session: ended {sid_a}" in fin.out
    assert not path_a.exists()
    assert path_b.exists() and (path_b / "mvp" / "prototype.html").read_text(encoding="utf-8") == "<html>b</html>"

    # finish B → empty registry, flat again
    (path_b / "progress.md").write_text("- checkpoint T1 abc1234 title\n", encoding="utf-8")
    fin_b = run(FINISH, "--ledger", str(path_b / "progress.md"), cwd=tmp_path)
    assert fin_b.returncode == 0 and f"session: ended {sid_b}" in fin_b.out
    assert not (tmp_path / ".superlite" / "sessions").exists()
    assert not (tmp_path / ".superlite" / "sessions.json").exists()
    assert not (tmp_path / ".superlite" / "active").exists()


def test_same_host_session_restart_ends_the_previous_tree(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    first = run(SESSION, "start", "--host-session", "same-chat", cwd=tmp_path)
    sid1 = [ln.split("=", 1)[1].strip() for ln in first.out.splitlines() if ln.startswith("SUPERLITE_SESSION=")][0]
    old = tmp_path / ".superlite" / "sessions" / sid1
    (old / "mvp" / "prototype.html").write_text("old", encoding="utf-8")
    monkeypatch.delenv("SUPERLITE_SESSION", raising=False)
    second = run(SESSION, "start", "--host-session", "same-chat", cwd=tmp_path)
    sid2 = [ln.split("=", 1)[1].strip() for ln in second.out.splitlines() if ln.startswith("SUPERLITE_SESSION=")][0]
    assert sid2 != sid1
    assert not old.exists()
    assert (tmp_path / ".superlite" / "sessions" / sid2).is_dir()


def test_legacy_flat_layout_untouched_until_session_start(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "plan.md").write_text("# p\n", encoding="utf-8")
    cp = run(ORCH, "set", "ai", "--plan", tmp_path / "plan.md", cwd=tmp_path)
    assert cp.returncode == 0
    assert (tmp_path / ".superlite" / "orchestrator.json").is_file()
    assert not (tmp_path / ".superlite" / "sessions").exists()
    st = run(SESSION, "status", cwd=tmp_path)
    assert "session: none" in st.out


def test_mvp_defaults_follow_active_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import sys
    sys.path.insert(0, str(SCRIPTS))
    import session as sl_session
    import mvp_check
    sid, path = sl_session.start(tmp_path, host_session="mvp")
    monkeypatch.setenv("SUPERLITE_SESSION", sid)
    assert Path(mvp_check.default_page()).resolve() == (path / "mvp" / "prototype.html").resolve()
    assert Path(mvp_check.default_feedback()).resolve() == (path / "mvp" / "mvp-feedback.json").resolve()


def test_plan_under_sandbox_docs_plans_binds_to_sandbox_superlite(tmp_path):
    """Prototype builds live under .superlite/sandboxes/<topic>/; their plans must not see the parent's feature.json."""
    import sys
    sys.path.insert(0, str(SCRIPTS))
    import orchestrator
    import session as sl_session
    sl = tmp_path / ".superlite"
    sl.mkdir()
    (sl / "orchestrator.json").write_text(json.dumps({"mode": "human", "approved": True, "by": "t", "time": "t"}) + "\n",
                                          encoding="utf-8")
    (sl / "feature.json").write_text(json.dumps({"status": "tryout"}) + "\n", encoding="utf-8")
    proto_plan = sl / "sandboxes" / "export-proto" / "docs" / "plans" / "p.proto.md"
    proto_plan.parent.mkdir(parents=True)
    proto_plan.write_text("# p\n", encoding="utf-8")
    (sl / "sandboxes" / "export-proto" / ".superlite").mkdir(parents=True)
    assert sl_session.project_sl(proto_plan) == sl / "sandboxes" / "export-proto" / ".superlite"
    assert orchestrator.gate(proto_plan, "dispatch") is None
    assert orchestrator.gate(tmp_path / "docs" / "plans" / "p.md", "dispatch") == orchestrator.TRYOUT_BLOCK


def test_end_from_ledger_path_without_active_pointer(tmp_path, monkeypatch):
    """finish must clean up even when active/env were cleared (self-cleanup)."""
    monkeypatch.chdir(tmp_path)
    import sys
    sys.path.insert(0, str(SCRIPTS))
    import session as sl_session
    sid, path = sl_session.start(tmp_path, host_session="orphan")
    (path / "progress.md").write_text("x\n", encoding="utf-8")
    monkeypatch.delenv("SUPERLITE_SESSION", raising=False)
    (tmp_path / ".superlite" / "active").unlink()
    reg = json.loads((tmp_path / ".superlite" / "sessions.json").read_text(encoding="utf-8"))
    reg["active"] = None
    (tmp_path / ".superlite" / "sessions.json").write_text(json.dumps(reg) + "\n", encoding="utf-8")
    assert "ended" in sl_session.end(path / "progress.md")
    assert not path.exists()
    assert not (tmp_path / ".superlite" / "sessions.json").exists()


def test_end_rejects_path_traversal_ids(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import sys
    sys.path.insert(0, str(SCRIPTS))
    import session as sl_session
    sl_session.start(tmp_path, host_session="safe")
    src = tmp_path / "src"
    src.mkdir()
    (src / "keep.txt").write_text("safe", encoding="utf-8")
    (tmp_path / ".superlite" / "sessions").mkdir(exist_ok=True)
    with pytest.raises(ValueError, match="invalid session id"):
        sl_session.end(tmp_path, session_id="../../src")
    assert (src / "keep.txt").read_text(encoding="utf-8") == "safe"
    bad = run(SESSION, "end", "--id", "../../src", cwd=tmp_path)
    assert bad.returncode == 2 and "invalid session id" in bad.err


def test_path_encoded_session_wins_over_active(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import sys
    sys.path.insert(0, str(SCRIPTS))
    import session as sl_session
    sid_a, path_a = sl_session.start(tmp_path, host_session="a")
    monkeypatch.delenv("SUPERLITE_SESSION", raising=False)
    sid_b, path_b = sl_session.start(tmp_path, host_session="b")
    monkeypatch.setenv("SUPERLITE_SESSION", sid_b)
    plan_a = path_a / "plan.md"
    plan_a.write_text("# a\n", encoding="utf-8")
    assert sl_session.state_dir(plan_a) == path_a
    assert sl_session.current_id(plan_a) == sid_a
    assert sl_session.state_dir() == path_b


def test_fresh_worktree_does_not_inherit_parent_superlite(tmp_path):
    import sys
    sys.path.insert(0, str(SCRIPTS))
    import session as sl_session
    parent = tmp_path / "main"
    parent.mkdir()
    (parent / ".git").mkdir()
    (parent / ".superlite").mkdir()
    (parent / ".superlite" / "orchestrator.json").write_text('{"mode":"ai"}\n', encoding="utf-8")
    wt = parent / ".superlite" / "worktrees" / "topic"
    wt.mkdir(parents=True)
    (wt / ".git").write_text("gitdir: ../../.git/worktrees/topic\n", encoding="utf-8")
    assert sl_session.project_sl(wt) == wt / ".superlite"
    assert sl_session.project_sl(wt) != parent / ".superlite"


def test_stable_and_dispatch_writers_match_readers(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import sys
    sys.path.insert(0, str(SCRIPTS))
    import session as sl_session
    import next_tasks as nt
    import dispatch
    sid, path = sl_session.start(tmp_path, host_session="stable")
    monkeypatch.setenv("SUPERLITE_SESSION", sid)
    (path / "stable.json").write_text(json.dumps({
        "stable": ["T1", "T2"], "critical_path": ["T1"], "tiers": {"T1": "sonnet"},
    }) + "\n", encoding="utf-8")
    plan = tmp_path / "docs" / "plans" / "p.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# p\n", encoding="utf-8")
    assert nt.stable_filter(plan) == {"T1", "T2"}
    assert dispatch.stable_cp(plan) == {"T1"}
    dispatch.write_dispatch_json(plan, [("T1", "fresh tier=sonnet")])
    assert (path / "dispatch.json").is_file()
    data = json.loads((path / "dispatch.json").read_text(encoding="utf-8"))
    assert data["batches"] == [["T1"]]


def test_peers_release_only_after_last_owner(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import sys
    sys.path.insert(0, str(SCRIPTS))
    import exclusive
    import session as sl_session
    sid_a, _ = sl_session.start(tmp_path, host_session="pa")
    exclusive.suspend(tmp_path, owner=sid_a)
    monkeypatch.delenv("SUPERLITE_SESSION", raising=False)
    sid_b, path_b = sl_session.start(tmp_path, host_session="pb")
    exclusive.suspend(tmp_path, owner=sid_b)
    peers = json.loads((tmp_path / ".superlite" / "peers.json").read_text(encoding="utf-8"))
    assert set(peers["owners"]) == {sid_a, sid_b}
    msg = exclusive.release(tmp_path, owner=sid_a)
    assert "still suspended" in msg
    assert (tmp_path / ".superlite" / "peers.json").is_file()
    assert exclusive.release(path_b, owner=sid_b) == exclusive.CLEAR
    assert not (tmp_path / ".superlite" / "peers.json").is_file()


def test_finish_legacy_ledger_does_not_end_active_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import sys
    sys.path.insert(0, str(SCRIPTS))
    import session as sl_session
    sid, path = sl_session.start(tmp_path, host_session="keep-me")
    monkeypatch.setenv("SUPERLITE_SESSION", sid)
    flat = tmp_path / ".superlite" / "progress.md"
    # Flat ledger next to sessions.json (not inside sessions/<id>/)
    flat.write_text("- checkpoint T1 abc1234567 title\n", encoding="utf-8")
    fin = run(FINISH, "--ledger", str(flat), cwd=tmp_path)
    assert fin.returncode == 0 and "session: none" in fin.out
    assert path.is_dir()  # active session B must survive a legacy finish


def test_host_lookup_does_not_treat_dot_as_wildcard(tmp_path):
    """Hooks must not match chat-a when looking up chat.a."""
    import subprocess
    import shutil
    bash = shutil.which("bash")
    if not bash:
        return
    sl = tmp_path / ".superlite"
    (sl / "sessions" / "sid-dash").mkdir(parents=True)
    (sl / "sessions" / "sid-dot").mkdir(parents=True)
    (sl / "sessions.json").write_text(json.dumps({
        "sessions": {"sid-dash": {}, "sid-dot": {}},
        "by_host": {"chat-a": "sid-dash", "chat.a": "sid-dot"},
        "active": "sid-dash",
    }) + "\n", encoding="utf-8")
    hook = SCRIPTS.parents[2] / "hooks" / "_sl_state_dir.sh"
    script = f'''
set -e
. "{hook}"
proj="{tmp_path}"
SUPERLITE_HOST_SESSION="chat.a"
sl_resolve_state_dir "$proj"
printf "%s" "$sd"
'''
    out = subprocess.run([bash, "-c", script], capture_output=True, text=True, check=True)
    assert out.stdout.endswith("sessions/sid-dot")


def test_end_releases_peers_owner_and_keeps_lock_file(tmp_path, monkeypatch):

    monkeypatch.chdir(tmp_path)
    import sys
    sys.path.insert(0, str(SCRIPTS))
    import exclusive
    import session as sl_session
    sid_a, _ = sl_session.start(tmp_path, host_session="ea")
    exclusive.suspend(tmp_path, owner=sid_a)
    monkeypatch.delenv("SUPERLITE_SESSION", raising=False)
    sid_b, path_b = sl_session.start(tmp_path, host_session="eb")
    exclusive.suspend(tmp_path, owner=sid_b)
    assert "ended" in sl_session.end(tmp_path, session_id=sid_a)
    peers = json.loads((tmp_path / ".superlite" / "peers.json").read_text(encoding="utf-8"))
    assert peers["owners"] == [sid_b]
    assert (tmp_path / ".superlite" / "sessions.lock").is_file()  # retained across empty-ish registry
    assert "ended" in sl_session.end(path_b)
    assert not (tmp_path / ".superlite" / "peers.json").is_file()
    assert (tmp_path / ".superlite" / "sessions.lock").is_file()  # still retained when registry cleared


def test_mvp_defaults_follow_plan_session_not_active(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import sys
    sys.path.insert(0, str(SCRIPTS))
    import session as sl_session
    import mvp_check
    sid_a, path_a = sl_session.start(tmp_path, host_session="ma")
    monkeypatch.delenv("SUPERLITE_SESSION", raising=False)
    sid_b, _ = sl_session.start(tmp_path, host_session="mb")
    monkeypatch.setenv("SUPERLITE_SESSION", sid_b)
    plan_a = path_a / "plan.md"
    plan_a.write_text("# a\n", encoding="utf-8")
    assert Path(mvp_check.default_page(plan_a)).resolve() == (path_a / "mvp" / "prototype.html").resolve()
    assert Path(mvp_check.default_feedback(plan_a)).resolve() == (path_a / "mvp" / "mvp-feedback.json").resolve()


def test_cwd_subfolder_finds_parent_superlite_without_git(tmp_path, monkeypatch):

    """`sl` from a nested folder still sees the project run when there is no .git marker."""
    import sys
    sys.path.insert(0, str(SCRIPTS))
    import session as sl_session
    (tmp_path / ".superlite").mkdir()
    (tmp_path / ".superlite" / "orchestrator.json").write_text('{"mode":"ai"}\n', encoding="utf-8")
    sub = tmp_path / "movedocs" / "api"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    assert sl_session.project_sl() == tmp_path / ".superlite"
    assert sl_session.state_dir() == tmp_path / ".superlite"


def test_feature_status_reads_project_feature_json(tmp_path, monkeypatch):

    monkeypatch.chdir(tmp_path)
    import sys
    sys.path.insert(0, str(SCRIPTS))
    import session as sl_session
    import orchestrator
    sid, path = sl_session.start(tmp_path, host_session="feat")
    monkeypatch.setenv("SUPERLITE_SESSION", sid)
    (path / "feature.json").write_text(json.dumps({"status": "tryout"}) + "\n", encoding="utf-8")
    assert orchestrator.feature_status(tmp_path) is None
    (tmp_path / ".superlite" / "feature.json").write_text(json.dumps({"status": "tryout"}) + "\n", encoding="utf-8")
    assert orchestrator.feature_status(tmp_path) == "tryout"


def test_history_and_feature_report_use_project_container(tmp_path, monkeypatch):
    """Session finish deletes the tree; project history/stats and feature.json must survive."""
    monkeypatch.chdir(tmp_path)
    import sys
    sys.path.insert(0, str(SCRIPTS))
    import history
    import report
    import session as sl_session
    from test_metrics_history import setup_run
    from test_routing import mech_plan

    monkeypatch.setenv("SUPERLITE_HOME", str(tmp_path / "home"))
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    p = mech_plan(tmp_path, 2)
    setup_run(tmp_path)
    sid, path = sl_session.start(tmp_path, host_session="hist")
    # Move metrics + pool into the session (producers write session-local)
    for name in ("metrics.jsonl", "pool.json"):
        src = tmp_path / ".superlite" / name
        if src.is_file():
            (path / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            src.unlink()
    rows = history.record_run(p, path, run_id="sess1")
    assert rows and rows[0]["language"] == "python"
    proj = tmp_path / ".superlite"
    assert (proj / "history.jsonl").is_file() and (proj / "stats.json").is_file()
    assert not (path / "history.jsonl").exists()

    head = {"wall_min": {"projected": 4.0, "actual": 5.0}, "cost_usd_agents": {"projected": 0.5, "actual": 0.6}}
    (proj / "feature.json").write_text(json.dumps({
        "topic": "export", "status": "discarded", "proto_report": {"headline": head, "tasks": 2},
    }), encoding="utf-8")
    integ = {"wall_min": {"projected": 12.0, "actual": 15.0}, "cost_usd_agents": {"projected": 2.0, "actual": 2.4}}
    f = report.feature_parts(path, integ)
    assert f is not None and f["topic"] == "export"

    sl_session.end(tmp_path, session_id=sid)
    assert not path.exists()
    assert (proj / "history.jsonl").is_file() and (proj / "feature.json").is_file()


def test_list_empty_when_no_sessions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = run(SESSION, "list", cwd=tmp_path)
    assert out.returncode == 0
    assert "session: list active=none count=0" in out.out


def test_list_marks_active_and_shows_stopped_and_orch(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import sys
    sys.path.insert(0, str(SCRIPTS))
    import session as sl_session
    sid_a, path_a = sl_session.start(tmp_path, host_session="list-a")
    (path_a / "stopped.md").write_text("paused\n", encoding="utf-8")
    (path_a / "orchestrator.json").write_text(
        json.dumps({"mode": "human", "approved": True, "by": "t", "time": "t"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("SUPERLITE_SESSION", raising=False)
    sid_b, path_b = sl_session.start(tmp_path, host_session="list-b")
    (path_b / "orchestrator.json").write_text(
        json.dumps({"mode": "ai", "approved": True, "by": "t", "time": "t"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SUPERLITE_SESSION", sid_b)
    out = run(SESSION, "list", cwd=tmp_path)
    assert out.returncode == 0
    assert f"session: list active={sid_b} count=2" in out.out
    # Active row marked with leading "* "; others with two spaces
    assert any(ln.startswith(f"* {sid_b}") and "orch=ai" in ln and "stopped=no" in ln for ln in out.out.splitlines())
    assert any(ln.startswith(f"  {sid_a}") and "orch=human" in ln and "stopped=yes" in ln and "host=list-a" in ln
               for ln in out.out.splitlines())


def test_use_switches_active_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import sys
    sys.path.insert(0, str(SCRIPTS))
    import session as sl_session
    sid_a, path_a = sl_session.start(tmp_path, host_session="use-a")
    (path_a / "progress.md").write_text("- checkpoint T1\n", encoding="utf-8")
    monkeypatch.delenv("SUPERLITE_SESSION", raising=False)
    sid_b, _ = sl_session.start(tmp_path, host_session="use-b")
    monkeypatch.setenv("SUPERLITE_SESSION", sid_b)
    assert sl_session.current_id(tmp_path) == sid_b
    used = run(SESSION, "use", "--id", sid_a, cwd=tmp_path)
    assert used.returncode == 0 and f"session: using {sid_a}" in used.out
    assert f"SUPERLITE_SESSION={sid_a}" in used.out
    monkeypatch.setenv("SUPERLITE_SESSION", sid_a)
    assert sl_session.current_id(tmp_path) == sid_a
    reg = json.loads((tmp_path / ".superlite" / "sessions.json").read_text(encoding="utf-8"))
    assert reg["active"] == sid_a
    assert (tmp_path / ".superlite" / "active").read_text(encoding="utf-8").strip() == sid_a
    assert sl_session.state_dir(tmp_path) == path_a


def test_use_rejects_unknown_and_invalid_ids(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run(SESSION, "start", "--host-session", "u", cwd=tmp_path)
    miss = run(SESSION, "use", "--id", "no-such-session", cwd=tmp_path)
    assert miss.returncode == 2 and "unknown session" in miss.err
    bad = run(SESSION, "use", "--id", "../../src", cwd=tmp_path)
    assert bad.returncode == 2 and "invalid session id" in bad.err


def test_discard_ends_non_active_and_keeps_others(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import sys
    sys.path.insert(0, str(SCRIPTS))
    import session as sl_session
    sid_a, path_a = sl_session.start(tmp_path, host_session="d-a")
    (path_a / "mvp" / "prototype.html").write_text("a", encoding="utf-8")
    monkeypatch.delenv("SUPERLITE_SESSION", raising=False)
    sid_b, path_b = sl_session.start(tmp_path, host_session="d-b")
    monkeypatch.setenv("SUPERLITE_SESSION", sid_b)
    gone = run(SESSION, "discard", "--id", sid_a, cwd=tmp_path)
    assert gone.returncode == 0 and f"session: ended {sid_a}" in gone.out
    assert not path_a.exists()
    assert path_b.is_dir()
    assert sl_session.current_id(tmp_path) == sid_b


def test_sessions_command_documents_list_use_discard():
    text = (SCRIPTS.parents[2] / "commands" / "sessions.md").read_text(encoding="utf-8")
    assert "sl session list" in text
    assert "sl session use" in text
    assert "sl session discard" in text
    assert "SUPERLITE_SESSION" in text
