"""hooks/pre-tool denies a live watch run inline; a background run, a terminal handoff and other commands pass."""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "hooks" / "pre-tool"
_GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"
BASH = _GIT_BASH if os.name == "nt" and os.path.exists(_GIT_BASH) else shutil.which("bash")
CLI = (ROOT / "skills" / "swarm" / "scripts" / "superlite-cli").as_posix()


def run(command, tool="Bash", background=None, **extra):
    tool_input = {"command": command}
    if background is not None:
        tool_input["run_in_background"] = background
    raw = json.dumps({"tool_name": tool, "tool_input": tool_input, **extra})
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PLUGIN_ROOT"}
    return subprocess.run([BASH, str(HOOK)], input=raw, capture_output=True, text=True, env=env)


WATCHES = [
    f"bash {CLI} cloud watch login_fix",
    "superlite-cli cloud watch login_fix",
    "/home/u/.claude/plugins/cache/superlite/skills/swarm/scripts/superlite-cli factory watch warp:1a2b3c4d",
    'bash "C:\\Users\\me\\superlite\\skills\\swarm\\scripts\\superlite-cli" factory watch lab:r1',
    "cd /tmp && superlite-cli cloud watch login_fix 2>&1 | tee out.txt",
    "SUPERLITE_HOME=/x bash superlite-cli cloud watch r --after 3",
    "python3 skills/swarm/scripts/cloud_watch.py login_fix",
    "python skills/swarm/scripts/factory.py watch lab:r1",
    "python3 skills/swarm/scripts/cloud.py watch r1",
    "timeout 600 bash superlite-cli cloud watch r1",
    "bash -lc 'superlite-cli cloud watch r1'",
    "superlite-cli cloud watch r1 --follow",
]


@pytest.mark.parametrize("cmd", WATCHES)
def test_foreground_watch_is_denied_with_the_handoff(cmd):
    r = run(cmd)
    assert r.returncode == 2 and r.stdout == "", (r.returncode, r.stdout, r.stderr)
    assert "run_in_background" in r.stderr and "terminal, tab or pane" in r.stderr
    assert " watch " in r.stderr and "--follow" not in r.stderr


def test_handoff_names_the_kind_and_run():
    for cmd, tail in ((WATCHES[0], "cloud watch login_fix"), (WATCHES[3], "factory watch lab:r1")):
        assert re.search(r"`bash \S*superlite-cli'? " + re.escape(tail) + "`", run(cmd).stderr)


@pytest.mark.parametrize("cmd", WATCHES)
def test_background_watch_passes(cmd):
    r = run(cmd, background=True)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")


def test_background_false_is_still_denied():
    assert run(WATCHES[0], background=False).returncode == 2


@pytest.mark.parametrize("tool", ["Shell", "PowerShell"])
def test_other_shell_tools_are_guarded(tool):
    assert run(WATCHES[0], tool=tool).returncode == 2


@pytest.mark.parametrize("extra", [{"agent_id": "a1"}, {"parent_tool_call_id": "t1"}])
def test_subagent_calls_are_denied_too(extra):
    assert run(WATCHES[1], **extra).returncode == 2


@pytest.mark.parametrize("cmd", [
    f"bash {CLI} cloud runs",
    f"bash {CLI} cloud runs --all",
    f"bash {CLI} factory list",
    f"bash {CLI} factory attach lab:r1",
    f"bash {CLI} cloud attach r1 && bash {CLI} cloud detach",
    "grep -rn watch skills/",
    "git log --oneline | grep 'cloud watch'",
    'echo "run superlite-cli cloud watch r1 in a pane"',
    "cat skills/swarm/scripts/cloud_watch.py",
    "grep cloud_watch.py docs/tools.md",
    "tmux split-window -h 'bash superlite-cli cloud watch r1'",
    "herdr pane run 7 'bash superlite-cli factory watch lab:r1'",
    "npm run watch",
    "pytest tests/scripts/test_cloud_watch.py",
    "watch -n 5 ls",
    "ls /tmp # superlite-cli",
])
def test_other_commands_are_untouched(cmd):
    r = run(cmd)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")


def test_other_tools_are_untouched():
    raw = json.dumps({"tool_name": "Read", "tool_input": {"file_path": f"{CLI} cloud watch r1"}})
    r = subprocess.run([BASH, str(HOOK)], input=raw, capture_output=True, text=True)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")
