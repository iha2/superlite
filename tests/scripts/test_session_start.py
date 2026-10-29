import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "hooks" / "session-start"
_GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"
BASH = _GIT_BASH if os.name == "nt" and os.path.exists(_GIT_BASH) else shutil.which("bash")


def test_session_start_emits_skill_text_verbatim(tmp_path):
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT), SUPERLITE_HOME=str(tmp_path))  # never touch the real ~/.superlite
    r = subprocess.run([BASH, str(HOOK)], input="", capture_output=True, text=True,
                       encoding="utf-8", env=env)
    assert r.returncode == 0
    ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    skill = (ROOT / "skills" / "using-superlite" / "SKILL.md").read_text(encoding="utf-8")
    assert skill.replace("\r", "").rstrip("\n") in ctx   # newlines, quotes, backslashes survive
    assert "\r" not in ctx


def _context(home):
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT), SUPERLITE_HOME=str(home))
    r = subprocess.run([BASH, str(HOOK)], input="", capture_output=True, text=True, encoding="utf-8", env=env)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]


def test_intro_is_shown_once_per_installed_version(tmp_path):
    version = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    intro = (ROOT / "assets" / "intro.txt").read_text(encoding="utf-8").replace("\r", "")
    first = _context(tmp_path)
    assert "was just installed" in first and "verbatim" in first
    assert "do not call Read" in first
    assert "```text" in first and intro in first   # embedded: no Read of a plugin path
    assert (tmp_path / f"intro-shown-{version}").is_file()
    assert "was just installed" not in _context(tmp_path)   # the second session stays quiet


def test_agent_specs_prefer_asked_once(tmp_path):
    first = _context(tmp_path)
    assert "pre-implementation specs" in first and "agent_specs prefer" in first
    (tmp_path / "preferences.json").write_text('{"agent_specs": false, "open_in_harness": true}\n', encoding="utf-8")
    assert "agent_specs prefer" not in _context(tmp_path)


def test_intro_and_help_files_fit_a_terminal():
    for name in ("intro.txt", "help.txt"):
        text = (ROOT / "assets" / name).read_text(encoding="utf-8")
        assert all(len(line) <= 80 for line in text.splitlines()), name
        assert "/superlite:intro" in text and "/superlite:help" in text, name
    commands = {p.stem for p in (ROOT / "commands").glob("*.md")}
    intro = (ROOT / "assets" / "intro.txt").read_text(encoding="utf-8")
    help_text = (ROOT / "assets" / "help.txt").read_text(encoding="utf-8")
    for c in commands:   # every command is listed in the detailed help
        assert f"/superlite:{c}" in help_text, c
    for c in commands - {"intro", "help"}:   # and on the intro screen
        assert f"/superlite:{c}" in intro, c


def test_session_start_tells_user_about_open_sessions(tmp_path):
    """Registered sessions survive stop; a new chat must be told they are still open."""
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT), SUPERLITE_HOME=str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    quiet = subprocess.run([BASH, str(HOOK)], cwd=tmp_path, capture_output=True, text=True,
                           encoding="utf-8", env=env)
    assert quiet.returncode == 0
    assert "still open" not in json.loads(quiet.stdout)["hookSpecificOutput"]["additionalContext"]

    sl = tmp_path / ".superlite"
    sid = "20261028T120000-open1"
    (sl / "sessions" / sid).mkdir(parents=True)
    (sl / "sessions" / sid / "stopped.md").write_text("paused\n", encoding="utf-8")
    (sl / "sessions.json").write_text(json.dumps({
        "sessions": {sid: {"created": "2026-10-28T12:00:00", "host_session": "chat-1"}},
        "by_host": {"chat-1": sid},
        "active": sid,
    }) + "\n", encoding="utf-8")
    (sl / "active").write_text(sid + "\n", encoding="utf-8")

    loud = subprocess.run([BASH, str(HOOK)], cwd=tmp_path, capture_output=True, text=True,
                          encoding="utf-8", env=env)
    assert loud.returncode == 0, loud.stderr
    ctx = json.loads(loud.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "still open" in ctx
    assert sid in ctx
    assert "/superlite:sessions" in ctx
    assert "Tell the user" in ctx


def test_sessions_command_tells_user_about_open_sessions():
    text = (ROOT / "commands" / "sessions.md").read_text(encoding="utf-8")
    assert "Tell the user" in text and "still open" in text
