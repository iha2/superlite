"""One contract for every hook script hooks/hooks.json registers: registration, LF endings, fail-open."""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOKS = ROOT / "hooks"
_GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"
BASH = _GIT_BASH if os.name == "nt" and os.path.exists(_GIT_BASH) else shutil.which("bash")

PRE_TOOL_MATCHER = "Edit|Write|NotebookEdit|Bash|PowerShell|Monitor|Agent"
# (event, matcher, script): the whole of hooks.json. A hook added or moved without an entry here fails.
REGISTERED = [
    ("SessionStart", "startup|clear|compact", "session-start"),
    ("PreToolUse", PRE_TOOL_MATCHER, "pre-tool"),
    ("PostToolUse", "Agent", "capture-agent-launch"),
    ("SubagentStop", None, "capture-agent-stop"),
    ("Stop", None, "capture-host-usage"),
    ("UserPromptSubmit", None, "prompt"),
]
SCRIPTS = [script for _e, _m, script in REGISTERED]


def registered():
    data = json.loads((HOOKS / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    found = []
    for event, entries in data.items():
        for entry in entries:
            for h in entry["hooks"]:
                m = re.fullmatch(r'bash "\$\{CLAUDE_PLUGIN_ROOT\}/hooks/([\w-]+)"', h["command"])
                assert m, h["command"]
                found.append((event, entry.get("matcher"), m.group(1)))
    return found


def test_every_registered_hook_is_in_the_contract():
    assert sorted(registered(), key=str) == sorted(REGISTERED, key=str)


@pytest.mark.parametrize("event,matcher,script", REGISTERED, ids=SCRIPTS)
def test_registered_with_the_right_event_and_matcher(event, matcher, script):
    assert (event, matcher, script) in registered()
    assert (HOOKS / script).is_file()


@pytest.mark.parametrize("path", sorted(p for p in HOOKS.iterdir() if p.is_file() and p.suffix != ".json"),
                         ids=lambda p: p.name)
def test_hook_scripts_have_lf_endings(path):
    assert b"\r" not in path.read_bytes()


def _tool(name, tool_input=None, **extra):
    return json.dumps({"tool_name": name, **({} if tool_input is None else {"tool_input": tool_input}), **extra})


MALFORMED = {
    "empty": "",
    "not-json": "not json",
    "brace-not-json": "{not json",
    "garbage": "garbage {{{",
    "empty-object": "{}",
    "empty-array": "[]",
    "open-brace": "{",
    "truncated": '{"tool_name": "Bash", "tool_input": {"command": "pk',
    "truncated-agent": '{"tool_name":"Agent"',
    "no-tool-name": json.dumps({"tool_input": {"command": "ls"}}),
    "no-tool-input-bash": _tool("Bash"),
    "no-tool-input-agent": _tool("Agent"),
    "edit-empty-input": _tool("Edit", {}),
    "bash-command-int": _tool("Bash", {"command": 5}),
    "agent-id-int": '{"agent_id": 5}',
    "prompt-int": '{"prompt": 5}',
    "prompt-cwd-only": '{"cwd": "x"}',
    "null-fields": json.dumps({"tool_name": None, "tool_input": None, "cwd": None}),
    "unknown-tool": _tool("Frobnicate", {"x": 1}),
    "binary": "\x01\x02\x7f" * 50,
    "big-write": _tool("Write", {"file_path": "docs/a.md", "content": "x" * 1_000_000}),
}


# session-start does not parse stdin and each run costs seconds (it reconciles sessions), so it gets a
# few shapes; the tool hooks, which do parse stdin, get every one.
SESSION_START_INPUTS = ("empty", "garbage", "binary")
CASES = [pytest.param(script, raw, id=f"{script}-{name}")
         for script in SCRIPTS for name, raw in MALFORMED.items()
         if script != "session-start" or name in SESSION_START_INPUTS]


@pytest.mark.parametrize("script,raw", CASES)
def test_fails_open(script, raw, tmp_path):
    if script == "pre-tool" and '"Agent"' in raw:
        pytest.skip("an Agent call with no subagent_type is general-purpose, which the human gate denies by design")
    (tmp_path / ".superlite").mkdir()
    (tmp_path / ".superlite" / "orchestrator.json").write_text('{"mode": "human", "approved": false}')
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path), "SUPERLITE_NO_AUTOCOMPACT": "1"}
    r = subprocess.run([BASH, str(HOOKS / script)], input=raw, capture_output=True, text=True,
                       env=env, cwd=str(tmp_path))
    assert r.returncode == 0, (script, raw[:80], r.stderr)
    assert '"deny"' not in r.stdout
    if script != "session-start":  # session-start injects the intro with no input; every other hook stays silent
        assert r.stdout == "" and r.stderr == "", (script, raw[:80])
    for log in (tmp_path / ".superlite").glob("*.jsonl"):  # whatever a hook wrote must still be valid JSON
        for line in log.read_text(encoding="utf-8").splitlines():
            json.loads(line)


@pytest.mark.skipif(not BASH, reason="bash not available")
@pytest.mark.parametrize("path", sorted(p for p in HOOKS.iterdir() if p.is_file() and p.suffix != ".json"),
                         ids=lambda p: p.name)
def test_hook_scripts_parse_under_bash(path):
    assert subprocess.run([BASH, "-n", str(path)], capture_output=True).returncode == 0
