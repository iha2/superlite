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
