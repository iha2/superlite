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
    first = _context(tmp_path)
    assert "was just installed" in first and "assets/intro.txt" in first and "verbatim" in first
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
