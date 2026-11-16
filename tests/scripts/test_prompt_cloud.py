"""hooks/prompt: the cloud hook starts only for a cloud prefix or an attached run, and fails open."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"
# A bare "bash" on Windows finds System32\bash.exe (WSL) first, so ask Git Bash by path.
BASH = _GIT_BASH if os.name == "nt" and os.path.exists(_GIT_BASH) else shutil.which("bash")
ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "hooks" / "prompt"


@pytest.fixture
def env(tmp_path):
    shim = tmp_path / "pyshim"
    log = tmp_path / "calls.log"
    shim.write_text(f'#!/usr/bin/env bash\necho "$@" >> "{log}"\ncat > /dev/null\n'
                    f'[ -n "${{SHIM_OUT:-}}" ] && printf \'%s\' "$SHIM_OUT"\nexit "${{SHIM_RC:-0}}"\n')
    shim.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    e = dict(os.environ, SUPERLITE_PYTHON=str(shim), SUPERLITE_HOME=str(home), HOME=str(tmp_path),
             SUPERLITE_NO_SESSION_HUD="1", SUPERLITE_NO_COST_REPORT="1", CLAUDE_PLUGIN_ROOT=str(ROOT))
    e.pop("SHIM_OUT", None)
    return e, log, home, proj


def call(env, proj, prompt, **extra):
    e = dict(env, **extra)
    data = json.dumps({"prompt": prompt, "cwd": str(proj), "session_id": "s1"})
    return subprocess.run([BASH, str(HOOK)], input=data, capture_output=True, text=True, env=e, cwd=proj, timeout=60)


def hook_calls(log):
    return [l for l in (log.read_text().splitlines() if log.exists() else []) if "cloud.py" in l and "hook" in l]


def test_bash_fastpath(env):
    e, log, home, proj = env
    r = call(e, proj, "hello")
    assert r.returncode == 0
    assert hook_calls(log) == []


def test_prefix_starts_hook(env):
    e, log, home, proj = env
    for prompt in (">> hi", "/superlite:attach r", "/superlite:runs", "/superlite-watch-r"):
        log.unlink(missing_ok=True)
        r = call(e, proj, prompt)
        assert r.returncode == 0
        assert len(hook_calls(log)) == 1, prompt


def test_attached_starts_hook(env):
    e, log, home, proj = env
    (home / "attach.json").write_text('{"run":"login_fix"}')
    call(e, proj, "hello")
    assert len(hook_calls(log)) == 1


def test_block_output_passes_through(env):
    e, log, home, proj = env
    out = '{"decision":"block","reason":"→ login_fix"}'
    r = call(e, proj, ">> hi", SHIM_OUT=out)
    assert r.returncode == 0
    assert r.stdout.strip() == out


def test_fail_open(env):
    e, log, home, proj = env
    r = call(e, proj, ">> hi", SHIM_RC="1")
    assert r.returncode == 0
    assert "block" not in r.stdout


def _reply_env(env, proj):
    e, log, home, _ = env
    sd = proj / ".superlite"
    sd.mkdir()
    (sd / "orchestrator.json").write_text('{"mode":"human","approved":false}')
    return sd / "human-replies.jsonl"


def test_blocked_cloud_line_is_not_recorded_as_reply(env):
    e, log, home, proj = env
    replies = _reply_env(env, proj)
    out = '{"decision": "block", "reason": "ok"}'
    r = call(e, proj, ">> yes approve", SHIM_OUT=out)
    assert r.stdout.strip() == out and not replies.exists()
    cur = '{"continue": false, "user_message": "ok"}'
    call(e, proj, ">> yes approve", SHIM_OUT=cur)
    assert not replies.exists()


def test_backslash_passthrough_and_plain_prompts_are_recorded(env):
    e, log, home, proj = env
    replies = _reply_env(env, proj)
    (home / "attach.json").write_text('{"run":"login_fix"}')
    call(e, proj, "\\approve this", SHIM_OUT='{"hookSpecificOutput": {"additionalContext": "x"}}')
    assert replies.exists() and "approve this" in replies.read_text()
    replies.unlink()
    (home / "attach.json").unlink()
    call(e, proj, "approve")
    assert replies.exists()
