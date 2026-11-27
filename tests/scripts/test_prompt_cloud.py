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
    (home / "attach").mkdir(parents=True, exist_ok=True)
    (home / "attach" / "s1.json").write_text('{"run":"login_fix"}')
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
    (home / "attach").mkdir(parents=True, exist_ok=True)
    (home / "attach" / "s1.json").write_text('{"run":"login_fix"}')
    for line in ("/some-other-command", "\\literal"):
        r = call(e, proj, line, SHIM_RC="1")
        assert r.returncode == 0 and "block" not in r.stdout, line


@pytest.mark.parametrize("extra", [{"SHIM_RC": "1"}, {"SHIM_OUT": "Traceback (most recent call last)"}])
def test_hook_failure_blocks_lines_meant_for_a_run(env, extra):
    e, log, home, proj = env
    for line in (">> hi", "  >>hi", "/superlite:attach r", "/superlite-watch-lab-1234", "/superlite:give r a@b.co"):
        r = call(e, proj, line, **extra)
        assert r.returncode == 0 and json.loads(r.stdout)["decision"] == "block", line
    (home / "attach").mkdir(parents=True, exist_ok=True)
    (home / "attach" / "s1.json").write_text('{"run":"login_fix"}')
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
    (home / "attach").mkdir(parents=True, exist_ok=True)
    (home / "attach" / "s1.json").write_text('{"run":"login_fix"}')
    call(e, proj, "\\approve this", SHIM_OUT='{"hookSpecificOutput": {"additionalContext": "x"}}')
    assert replies.exists() and "approve this" in replies.read_text()
    replies.unlink()
    (home / "attach" / "s1.json").unlink()
    call(e, proj, "approve")
    assert replies.exists()


def test_other_session_is_not_captured_by_an_attachment(env):
    e, log, home, proj = env
    (home / "attach").mkdir(parents=True, exist_ok=True)
    (home / "attach" / "sess-a.json").write_text('{"run":"login_fix"}')
    (home / "attach.json").write_text('{"run":"login_fix"}')  # the legacy shared file routes nothing
    data = lambda sid: json.dumps({"prompt": "hello", "cwd": str(proj), **({"session_id": sid} if sid else {})})
    run = lambda sid, **x: subprocess.run([BASH, str(HOOK)], input=data(sid), capture_output=True, text=True,
                                          encoding="utf-8", env=dict(e, **x), cwd=proj, timeout=60)
    assert run("sess-b").returncode == 0 and hook_calls(log) == []
    assert run(None, CLAUDE_CODE_SESSION_ID="").returncode == 0 and hook_calls(log) == []
    run("sess-a")
    assert len(hook_calls(log)) == 1
    log.unlink()
    run(None, CLAUDE_CODE_SESSION_ID="sess-a")  # no id in the input: the env var names the session
    assert len(hook_calls(log)) == 1


def test_attached_session_key_is_sanitized_like_python(env):
    e, log, home, proj = env
    (home / "attach").mkdir(parents=True, exist_ok=True)
    (home / "attach" / "a_b_c.json").write_text('{"run":"login_fix"}')
    data = json.dumps({"prompt": "hello", "cwd": str(proj), "session_id": "a/b c"})
    subprocess.run([BASH, str(HOOK)], input=data, capture_output=True, text=True, encoding="utf-8", env=e,
                   cwd=proj, timeout=60)
    assert len(hook_calls(log)) == 1


def _sid_call(e, proj, sid, prompt="hello"):
    data = json.dumps({"prompt": prompt, "cwd": str(proj), "session_id": sid})
    return subprocess.run([BASH, str(HOOK)], input=data, capture_output=True, text=True, encoding="utf-8", env=e,
                          cwd=proj, timeout=60)


def test_fastpath_routes_only_the_attached_session(env):
    e, log, home, proj = env
    (home / "attach").mkdir(parents=True, exist_ok=True)
    (home / "attach" / "A.json").write_text('{"run":"login_fix"}')
    assert _sid_call(e, proj, "B").returncode == 0
    assert hook_calls(log) == []
    _sid_call(e, proj, "A")
    assert len(hook_calls(log)) == 1


def test_fastpath_ignores_legacy_attach_json(env):
    e, log, home, proj = env
    (home / "attach.json").write_text('{"run":"login_fix"}')
    for sid in ("A", "s1", "x y"):
        assert _sid_call(e, proj, sid).returncode == 0
    assert hook_calls(log) == []


@pytest.mark.parametrize("sid", ["  A  ", "a/b c", "\tA.b\n", " é x ", "a-b_c"])
def test_fastpath_key_matches_clean_key(env, sid):
    import sys
    scripts = str(ROOT / "skills" / "swarm" / "scripts")
    sys.path.insert(0, scripts)
    try:
        import cloud_hook
        e, log, home, proj = env
        old = os.environ.get("SUPERLITE_HOME")
        os.environ["SUPERLITE_HOME"] = e["SUPERLITE_HOME"]
        try:
            cloud_hook.set_session(sid)
            p = cloud_hook.attach_path()
            assert p is not None
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('{"run":"login_fix"}')
        finally:
            cloud_hook.set_session(None)
            if old is None:
                os.environ.pop("SUPERLITE_HOME", None)
            else:
                os.environ["SUPERLITE_HOME"] = old
    finally:
        sys.path.remove(scripts)
    _sid_call(e, proj, sid)
    assert len(hook_calls(log)) == 1


def test_fastpath_key_astral_char_is_one_underscore(env):
    e, log, home, proj = env
    (home / "attach").mkdir(parents=True, exist_ok=True)
    (home / "attach" / "a_b.json").write_text('{"run":"login_fix"}')
    _sid_call(e, proj, "a\U0001F600b")  # json.dumps emits a surrogate pair
    assert len(hook_calls(log)) == 1
