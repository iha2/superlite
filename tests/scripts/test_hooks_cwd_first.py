"""Hooks resolve the run by walking up from the input cwd before CLAUDE_PROJECT_DIR."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOKS = ROOT / "hooks"
BASH = shutil.which("bash")
HUMAN = '{"mode": "human", "approved": false}'


@pytest.fixture
def tree(tmp_path):
    hub = tmp_path / "hub"
    wt = hub / ".superlite" / "worktrees" / "sessions" / "demo"
    (wt / ".superlite").mkdir(parents=True)
    (hub / ".superlite").mkdir(exist_ok=True)
    return hub, wt


def run(hook, hub, payload, env_extra=None):
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(hub), **(env_extra or {})}
    return subprocess.run([BASH, str(HOOKS / hook)], input=json.dumps(payload), capture_output=True,
                          text=True, env=env, cwd=str(hub))


def test_reply_recorded_in_worktree_not_hub(tree):
    hub, wt = tree
    (wt / ".superlite" / "orchestrator.json").write_text(HUMAN)
    r = run("record-human-reply", hub, {"cwd": str(wt), "prompt": "hello there"})
    assert r.returncode == 0
    assert (wt / ".superlite" / "human-replies.jsonl").exists()
    assert not (hub / ".superlite" / "human-replies.jsonl").exists()


def test_reply_at_root_unchanged(tmp_path):
    (tmp_path / ".superlite").mkdir()
    (tmp_path / ".superlite" / "orchestrator.json").write_text(HUMAN)
    run("record-human-reply", tmp_path, {"cwd": str(tmp_path), "prompt": "hello there"})
    assert (tmp_path / ".superlite" / "human-replies.jsonl").exists()


def test_guard_denies_code_write_from_worktree_run(tree):
    hub, wt = tree
    (wt / ".superlite" / "orchestrator.json").write_text('{"mode": "human", "approved": true}')
    (wt / ".superlite" / "stable.json").write_text("{}")
    r = run("block-coordinator-edits", hub,
            {"tool_name": "Write", "tool_input": {"file_path": str(wt / "src" / "a.py")}, "cwd": str(wt)})
    assert r.returncode == 2


def test_guard_prefers_worktree_over_hub_run(tree):
    hub, wt = tree
    (hub / ".superlite" / "orchestrator.json").write_text(HUMAN)
    (wt / ".superlite" / "orchestrator.json").write_text('{"mode":"ai","approved":false}')
    r = run("block-coordinator-edits", hub,
            {"tool_name": "Write", "tool_input": {"file_path": str(wt / "a.txt")}, "cwd": str(wt)})
    assert r.returncode == 0


@pytest.mark.parametrize("hook,payload", [
    ("capture-agent-launch", {"tool_name": "Agent", "tool_input": {"subagent_type": "i", "description": "T1",
                                                                  "prompt": "x", "model": "haiku"},
                              "tool_response": {"agentId": "a1", "description": "T1"}}),
    ("capture-agent-stop", {"agent_id": "a1", "agent_type": "i", "last_assistant_message": "Done"}),
])
def test_capture_ledger_lands_in_worktree(tree, hook, payload):
    hub, wt = tree
    (wt / ".superlite" / "pool.json").write_text("{}")
    r = run(hook, hub, {**payload, "cwd": str(wt)})
    assert r.returncode == 0
    assert (wt / ".superlite" / "metrics.jsonl").exists()
    assert not (hub / ".superlite" / "metrics.jsonl").exists()


def test_compact_nudge_reads_worktree_run(tree):
    hub, wt = tree
    (hub / ".superlite" / "orchestrator.json").write_text(HUMAN)
    (hub / ".superlite" / "last-compact").write_text("")  # fresh at the hub: not due
    (wt / ".superlite" / "orchestrator.json").write_text(HUMAN)  # no stamp: due
    r = run("compact-nudge", hub, {"cwd": str(wt)}, {"CLAUDE_PLUGIN_ROOT": str(ROOT)})
    assert r.stdout.strip() != ""


def test_hooks_parse_under_bash(tree):
    for h in ("record-human-reply", "block-coordinator-edits", "capture-agent-launch",
              "capture-agent-stop", "compact-nudge"):
        assert subprocess.run(["/bin/bash", "-n", str(HOOKS / h)]).returncode == 0


@pytest.mark.parametrize("cwd", ["", "/", "C:\\\\Users\\\\x", "/no such/dir with \\\"quote", "relative/dir", None])
def test_guard_odd_cwd_keeps_hub_run_and_never_crashes(tree, cwd):
    hub, _wt = tree
    (hub / ".superlite" / "orchestrator.json").write_text('{"mode": "human", "approved": true}')
    (hub / ".superlite" / "stable.json").write_text("{}")
    payload = {"tool_name": "Write", "tool_input": {"file_path": str(hub / "src" / "a.py")}}
    if cwd is not None:
        payload["cwd"] = cwd
    r = run("block-coordinator-edits", hub, payload)
    assert r.returncode == 2
    assert "syntax error" not in r.stderr
