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
                   "model": "claude-haiku-5-5", "desc": "T1 build parser", "parent": "",
                   "session": "", "slug": ""}
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
                   "transcript": "C:/Users/me/.claude/agent-a123.jsonl", "last": "Done: T1",
                   "session": "s", "slug": ""}


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


def test_cursor_hooks_register_capture():
    data = json.loads((ROOT / "hooks" / "hooks-cursor.json").read_text(encoding="utf-8"))["hooks"]
    assert "capture-agent-launch" in data["subagentStart"][0]["command"]
    assert "capture-agent-stop" in data["subagentStop"][0]["command"]


CURSOR_START = {
    "hook_event_name": "subagentStart", "subagent_id": "abc-123", "subagent_type": "explore",
    "task": "Prototype container rules", "subagent_model": "claude-haiku-5-5",
    "parent_conversation_id": "conv-1", "tool_call_id": "tc-1",
}
CURSOR_STOP = {
    "hook_event_name": "subagentStop", "subagent_id": "abc-123", "subagent_type": "explore",
    "status": "completed", "task": "Prototype container rules", "description": "Prototype container rules",
    "summary": "Web keep is the shell.", "duration_ms": 45000,
    "agent_transcript_path": "C:\\Users\\me\\.cursor\\agent.jsonl",
}


def test_cursor_payloads_record_when_the_orchestrator_is_set(tmp_path):
    (tmp_path / ".superlite").mkdir()
    (tmp_path / ".superlite" / "orchestrator.json").write_text('{"mode":"ai"}')
    assert run(LAUNCH, tmp_path, CURSOR_START).returncode == 0
    assert run(STOP, tmp_path, CURSOR_STOP).returncode == 0
    launch, stop = records(tmp_path)
    assert launch["agent_id"] == "abc-123" and launch["type"] == "explore"
    assert launch["model"] == "claude-haiku-5-5" and launch["desc"] == "Prototype container rules"
    assert launch["parent"] == ""
    assert stop["agent_id"] == "abc-123" and stop["type"] == "explore"
    assert stop["transcript"].endswith("agent.jsonl") and stop["last"] == "Web keep is the shell."


def test_feature_json_is_a_run_marker(tmp_path):
    (tmp_path / ".superlite").mkdir()
    (tmp_path / ".superlite" / "feature.json").write_text("{}")
    run(STOP, tmp_path, CURSOR_STOP)
    assert len(records(tmp_path)) == 1


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


# ---- session identity: the hook's session_id and the superlite session slug from .superlite/sessions.json ----

def registry(host, active, rows):
    (host / ".superlite").mkdir(exist_ok=True)
    reg = {"v": 2, "base": "main", "active": active, "host_worktree": str(host),
           "sessions": [{"slug": s, "branch": f"superlite/sessions/{s}", "worktree": str(w), "status": "active"} for s, w in rows]}
    (host / ".superlite" / "sessions.json").write_text(json.dumps(reg, indent=1))


def run_at(hook, cwd, payload):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    return subprocess.run([BASH, str(hook)], input=json.dumps({**payload, "cwd": str(cwd)}), capture_output=True, text=True, env=env)


@pytest.mark.parametrize("hook,payload", [(LAUNCH, LAUNCH_PAYLOAD), (STOP, STOP_PAYLOAD)])
def test_rows_carry_session_id_and_the_hosts_active_slug(proj, hook, payload):
    registry(proj, "blink", [("blink", proj), ("other", proj / ".superlite" / "worktrees" / "other")])
    assert run_at(hook, proj, {**payload, "session_id": "sess-42"}).returncode == 0
    (rec,) = records(proj)
    assert rec["session"] == "sess-42" and rec["slug"] == "blink"


@pytest.mark.parametrize("hook,payload", [(LAUNCH, LAUNCH_PAYLOAD), (STOP, STOP_PAYLOAD)])
def test_rows_in_a_session_worktree_carry_that_sessions_slug(tmp_path, hook, payload):
    host = tmp_path / "host"
    wt = host / ".superlite" / "worktrees" / "other"
    (wt / ".superlite").mkdir(parents=True)
    (wt / ".superlite" / "pool.json").write_text("{}")
    registry(host, "blink", [("blink", host), ("other", wt), ("oth", host / ".superlite" / "worktrees" / "oth")])
    assert run_at(hook, wt / "src", {**payload, "session_id": "s2"}).returncode == 0
    (rec,) = records(wt)
    assert rec["session"] == "s2" and rec["slug"] == "other"


def test_rows_in_a_prototype_sandbox_carry_the_active_slug(tmp_path):
    host = tmp_path / "host"
    sb = host / ".superlite" / "sandboxes" / "blink-proto"
    (sb / ".superlite").mkdir(parents=True)
    (sb / ".superlite" / "stable.json").write_text("{}")
    registry(host, "blink", [("blink", host)])
    assert run_at(LAUNCH, sb, LAUNCH_PAYLOAD).returncode == 0
    (rec,) = records(sb)
    assert rec["slug"] == "blink" and rec["session"] == ""


@pytest.mark.parametrize("hook,payload", [(LAUNCH, LAUNCH_PAYLOAD), (STOP, STOP_PAYLOAD)])
def test_rows_in_a_linked_worktree_outside_the_main_checkout_find_its_registry(tmp_path, hook, payload):
    host, wt = tmp_path / "host", tmp_path / "elsewhere" / "feat"
    (host / ".git" / "worktrees" / "feat").mkdir(parents=True)
    (wt / ".superlite").mkdir(parents=True)
    (wt / ".superlite" / "pool.json").write_text("{}")
    (wt / ".git").write_text(f"gitdir: {(host / '.git' / 'worktrees' / 'feat').as_posix()}\n")
    registry(host, "blink", [("blink", host), ("feat", wt)])
    assert run_at(hook, wt / "src", {**payload, "session_id": "s3"}).returncode == 0
    (rec,) = records(wt)
    assert rec["session"] == "s3" and rec["slug"] == "feat"


def test_udecode_turns_json_unicode_escapes_into_utf8():
    script = '. "$1/_unicode"; s=$2; udecode s; printf "%s" "$s"'
    raw = json.dumps("/tmp/café/naïve/€/😀/a\\u0041")[1:-1]  # json escapes, plus a literal backslash-u kept as is
    r = subprocess.run([BASH, "-c", script, "x", str(ROOT / "hooks"), raw.replace("\\\\", "/")], capture_output=True)
    assert r.returncode == 0
    assert r.stdout.decode("utf-8") == "/tmp/café/naïve/€/😀/a/u0041"


@pytest.mark.parametrize("hook,payload", [(LAUNCH, LAUNCH_PAYLOAD), (STOP, STOP_PAYLOAD)])
def test_rows_match_a_worktree_whose_path_the_registry_escaped(tmp_path, hook, payload):
    host = tmp_path / "café"
    wt = host / ".superlite" / "worktrees" / "naïve"
    (wt / ".superlite").mkdir(parents=True)
    (wt / ".superlite" / "pool.json").write_text("{}")
    registry(host, "blink", [("blink", host), ("naive", wt)])  # json.dumps writes é, as base.write_json does
    assert "\\u00e9" in (host / ".superlite" / "sessions.json").read_text()
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    raw = json.dumps({**payload, "cwd": str(wt)}, ensure_ascii=False)  # the host sends UTF-8 as is
    assert subprocess.run([BASH, str(hook)], input=raw.encode("utf-8"), capture_output=True, env=env).returncode == 0
    (rec,) = records(wt)
    assert rec["slug"] == "naive"
