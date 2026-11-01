import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "hooks" / "pre-tool"
# Git Bash on Windows (WSL's bash.exe in System32 would not see Windows paths), bash elsewhere.
_GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"
BASH = _GIT_BASH if os.name == "nt" and os.path.exists(_GIT_BASH) else shutil.which("bash")


def run(payload, tool="Bash", raw=None):
    if raw is None:
        raw = json.dumps({"tool_name": tool, "tool_input": {"command": payload}})
    return subprocess.run(
        [BASH, str(HOOK)], input=raw, capture_output=True, text=True
    )


def test_blocks_taskkill_im():
    for cmd in ("taskkill /F /IM python.exe", "taskkill //F //IM python.exe", "TASKKILL /im node.exe"):
        r = run(cmd)
        assert r.returncode == 2, cmd
        assert "PID" in r.stderr


def test_blocks_pkill_killall_stop_process_name():
    for cmd, tool in (
        ("pkill -f server", "Bash"),
        ("killall node", "Bash"),
        ("Stop-Process -Name python -Force", "PowerShell"),
        ("stop-process -name python", "PowerShell"),
        ("kill -9 -1", "Bash"),
    ):
        assert run(cmd, tool).returncode == 2, cmd


def test_allows_kill_by_pid():
    for cmd, tool in (
        ("kill 1234", "Bash"),
        ("kill -9 1234", "Bash"),
        ("taskkill /PID 1234", "Bash"),
        ("taskkill //F //PID 1234", "Bash"),
        ("Stop-Process -Id 12", "PowerShell"),
    ):
        r = run(cmd, tool)
        assert r.returncode == 0, cmd
        assert r.stdout == "" and r.stderr == ""


def test_allows_unrelated_commands():
    for cmd in ("ls -la", "python -m pytest", "echo skill"):
        assert run(cmd).returncode == 0, cmd
    # other tools are ignored
    assert run("pkill x", tool="Read").returncode == 0


def test_malformed_input_fails_open():
    for raw in ("", "not json", "[]", json.dumps({"tool_name": "Bash"}),
                json.dumps({"tool_name": "Bash", "tool_input": {"command": 5}})):
        r = run(None, raw=raw)
        assert r.returncode == 0, raw
        assert r.stdout == "" and r.stderr == ""


def test_hooks_json_has_both_entries():
    data = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    assert "SessionStart" in data["hooks"]
    (pre,) = data["hooks"]["PreToolUse"]
    assert {"Bash", "PowerShell"} <= set(pre["matcher"].split("|"))
    assert pre["hooks"][0]["command"] == 'bash "${CLAUDE_PLUGIN_ROOT}/hooks/pre-tool"'


def test_blocks_multiline_and_escaped_newlines():
    for cmd in ("echo x\npkill -f y", "echo x\r\npkill -f y", "echo x\n\tkillall node"):
        assert run(cmd).returncode == 2, repr(cmd)
    # a literal backslash-n typed by the model is treated the same way
    assert run("echo x\npkill -f y").returncode == 2


def test_blocks_get_process_pipe_taskkill_filter_kill_all():
    for cmd, tool in (
        ("Get-Process python | Stop-Process -Force", "PowerShell"),
        ("get-process -Name node|stop-process", "PowerShell"),
        ("taskkill /F /FI \"IMAGENAME eq python.exe\"", "Bash"),
        ("taskkill //FI \"STATUS eq running\"", "Bash"),
        ("kill -KILL -1", "Bash"),
        ("kill -s KILL -1", "Bash"),
    ):
        assert run(cmd, tool).returncode == 2, cmd


def test_allows_pid_forms_of_new_rules():
    for cmd, tool in (
        ("Get-Process -Id 12 | Stop-Process -Id 12", "PowerShell"),
        ("kill -KILL 1234", "Bash"),
        ("kill -s KILL 1234", "Bash"),
        ("kill -9 12345", "Bash"),
        ("echo x\nkill 1234", "Bash"),
    ):
        assert run(cmd, tool).returncode == 0, cmd
