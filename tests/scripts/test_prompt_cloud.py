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
                    f'[ -n "${{SHIM_OUT_FILE:-}}" ] && cat "$SHIM_OUT_FILE"\nexit "${{SHIM_RC:-0}}"\n')
    shim.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    e = dict(os.environ, SUPERLITE_PYTHON=str(shim), SUPERLITE_HOME=str(home), HOME=str(tmp_path),
             SUPERLITE_NO_SESSION_HUD="1", SUPERLITE_NO_COST_REPORT="1", CLAUDE_PLUGIN_ROOT=str(ROOT))
    e.pop("SHIM_OUT", None)
    e.pop("SHIM_OUT_FILE", None)
    return e, log, home, proj


def call(env, proj, prompt, **extra):
    e = dict(env, **extra)
    if "SHIM_OUT" in e:  # a file, not an env var: Windows narrows env values to the ANSI code page and loses "→"
        out_file = proj.parent / "shim-out"
        out_file.write_bytes(e.pop("SHIM_OUT").encode("utf-8"))
        e["SHIM_OUT_FILE"] = out_file.as_posix()
    data = json.dumps({"prompt": prompt, "cwd": str(proj), "session_id": "s1"})
    return subprocess.run([BASH, str(HOOK)], input=data, capture_output=True, text=True, encoding="utf-8", env=e, cwd=proj, timeout=60)


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


def test_fail_open_for_unrelated_lines(env):
    e, log, home, proj = env
    r = call(e, proj, "/superlite:runs", SHIM_RC="1")
    assert r.returncode == 0 and "block" not in r.stdout
    (home / "attach.json").write_text('{"run":"login_fix"}')
    for line in ("/some-other-command", "\\literal"):
        r = call(e, proj, line, SHIM_RC="1")
        assert r.returncode == 0 and "block" not in r.stdout, line


@pytest.mark.parametrize("extra", [{"SHIM_RC": "1"}, {"SHIM_OUT": "Traceback (most recent call last)"}])
def test_hook_failure_blocks_lines_meant_for_a_run(env, extra):
    e, log, home, proj = env
    for line in (">> hi", "  >>hi", "/superlite:attach r", "/superlite-watch-lab-1234", "/superlite:give r a@b.co"):
        r = call(e, proj, line, **extra)
        assert r.returncode == 0 and json.loads(r.stdout)["decision"] == "block", line
    (home / "attach.json").write_text('{"run":"login_fix"}')
    for line in ("also add a test", "/cancel", "/approve"):
        r = call(e, proj, line, **extra)
        assert json.loads(r.stdout)["decision"] == "block", line
    r = call(e, proj, ">> hi", **extra, CURSOR_AGENT="1")
    assert json.loads(r.stdout)["continue"] is False


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
