import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LAUNCH = ROOT / "hooks" / "capture-agent-launch"
STOP = ROOT / "hooks" / "capture-agent-stop"
_GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"
BASH = _GIT_BASH if os.name == "nt" and os.path.exists(_GIT_BASH) else shutil.which("bash")

LAUNCH_PAYLOAD = {
    "tool_name": "Agent",
    "tool_input": {"subagent_type": "implementer-haiku", "description": "T1 build parser",
                   "prompt": "do it", "model": "haiku"},
    "tool_response": {"status": "async_launched", "agentId": "a123", "description": "T1 build parser",
                      "resolvedModel": "claude-haiku-5-5", "outputFile": "x"},
}
STOP_PAYLOAD = {
    "hook_event_name": "SubagentStop", "agent_id": "a123", "agent_type": "implementer-haiku",
    "agent_transcript_path": "C:\\Users\\me\\.claude\\agent-a123.jsonl", "last_assistant_message": "Done: T1",
    "session_id": "s", "transcript_path": "t",
}


@pytest.fixture
def proj(tmp_path):
    (tmp_path / ".superlite").mkdir()
    (tmp_path / ".superlite" / "pool.json").write_text("{}")
    return tmp_path


def run(hook, proj, payload=None, raw=None):
    if raw is None:
        raw = json.dumps(payload)
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(proj)}
    return subprocess.run([BASH, str(hook)], input=raw, capture_output=True, text=True, env=env)


def records(proj):
    p = proj / ".superlite" / "metrics.jsonl"
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def ms():
    return time.time() * 1000


def test_launch_record(proj):
    t0 = ms()
    r = run(LAUNCH, proj, LAUNCH_PAYLOAD)
    t1 = ms()
    assert r.returncode == 0 and r.stdout == "" and r.stderr == ""
    (rec,) = records(proj)
    t = rec.pop("t")
    assert t0 - 50 <= t <= t1 + 50
    assert rec == {"ev": "launch", "agent_id": "a123", "type": "implementer-haiku",
                   "model": "claude-haiku-5-5", "desc": "T1 build parser", "parent": ""}
    assert list(json.loads((proj / ".superlite" / "metrics.jsonl").read_text()).keys())[:2] == ["ev", "t"]


def test_launch_from_a_subagent_records_its_parent(proj):
    # PostToolUse carries the calling loop's agent_id when a subagent made the Agent call
    r = run(LAUNCH, proj, {**LAUNCH_PAYLOAD, "agent_id": "coord9", "agent_type": "superlite:coordinator-sonnet"})
    assert r.returncode == 0
    (rec,) = records(proj)
    assert rec["agent_id"] == "a123" and rec["parent"] == "coord9"


def test_stop_record(proj):
    t0 = ms()
    r = run(STOP, proj, STOP_PAYLOAD)
    t1 = ms()
    assert r.returncode == 0 and r.stdout == "" and r.stderr == ""
    (rec,) = records(proj)
    t = rec.pop("t")
    assert t0 - 50 <= t <= t1 + 50
    assert rec == {"ev": "stop", "agent_id": "a123", "type": "implementer-haiku",
                   "transcript": "C:/Users/me/.claude/agent-a123.jsonl", "last": "Done: T1"}


def test_records_append(proj):
    run(LAUNCH, proj, LAUNCH_PAYLOAD)
    run(STOP, proj, STOP_PAYLOAD)
    assert [r["ev"] for r in records(proj)] == ["launch", "stop"]


@pytest.mark.parametrize("marker", ["stable.json", "prime.md", "pool.json"])
def test_markers(tmp_path, marker):
    (tmp_path / ".superlite").mkdir()
    (tmp_path / ".superlite" / marker).write_text("x")
    run(STOP, tmp_path, STOP_PAYLOAD)
    assert len(records(tmp_path)) == 1


def test_inactive_run_writes_nothing(tmp_path):
    for hook, payload in ((LAUNCH, LAUNCH_PAYLOAD), (STOP, STOP_PAYLOAD)):
        r = run(hook, tmp_path, payload)
        assert r.returncode == 0
    assert not (tmp_path / ".superlite").exists()


def test_other_tool_ignored(proj):
    run(LAUNCH, proj, {**LAUNCH_PAYLOAD, "tool_name": "Bash"})
    assert records(proj) == []


def test_malformed_input_fails_open(proj):
    for hook in (LAUNCH, STOP):
        for raw in ("", "not json", "[]", "{", '{"tool_name":"Agent"', '{"agent_id": 5}'):
            r = run(hook, proj, raw=raw)
            assert r.returncode == 0, raw
            assert r.stdout == "" and r.stderr == ""
    for rec in records(proj):  # whatever was written must still be valid JSON
        assert rec["ev"] in ("launch", "stop")


def test_escaping_round_trips(proj):
    nasty = 'say "hi" \\ back\\slash\nnew\tline {x}'
    p = json.loads(json.dumps(LAUNCH_PAYLOAD))
    p["tool_response"]["description"] = nasty
    p["tool_input"]["description"] = nasty
    run(LAUNCH, proj, p)
    s = {**STOP_PAYLOAD, "last_assistant_message": nasty + "é" * 10, "agent_type": 'a"b'}
    run(STOP, proj, s)
    launch, stop = records(proj)
    assert launch["desc"] == 'say "hi" \\ back\\slash new line {x}'
    assert stop["last"].startswith('say "hi" \\ back\\slash new line {x}')
    assert stop["type"] == 'a"b'


def test_truncation(proj):
    p = json.loads(json.dumps(LAUNCH_PAYLOAD))
    p["tool_input"]["description"] = "d" * 300
    run(LAUNCH, proj, p)
    run(STOP, proj, {**STOP_PAYLOAD, "last_assistant_message": 'q"' * 200})
    launch, stop = records(proj)
    assert len(launch["desc"]) == 120
    assert len(stop["last"]) == 160


def test_hooks_json_registers_both():
    data = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    post = data["PostToolUse"][0]
    assert post["matcher"] == "Agent"
    assert post["hooks"][0]["command"] == 'bash "${CLAUDE_PLUGIN_ROOT}/hooks/capture-agent-launch"'
    assert data["SubagentStop"][0]["hooks"][0]["command"] == 'bash "${CLAUDE_PLUGIN_ROOT}/hooks/capture-agent-stop"'


def test_lf_endings():
    for h in (LAUNCH, STOP):
        assert b"\r" not in h.read_bytes()


def test_fast(proj):
    def best(argv, raw=""):
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(proj)}
        times = []
        for _ in range(3):
            t = time.perf_counter()
            subprocess.run(argv, input=raw, capture_output=True, text=True, env=env)
            times.append(time.perf_counter() - t)
        return min(times)

    base = best([BASH, "-c", "exit 0"])
    for hook, payload in ((LAUNCH, LAUNCH_PAYLOAD), (STOP, STOP_PAYLOAD)):
        t = best([BASH, str(hook)], json.dumps(payload))
        assert t < max(0.6, base + 0.35), (hook.name, t, base)
