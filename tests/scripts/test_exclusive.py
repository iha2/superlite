"""Peer plugins are suspended for a superlite run and restored when it finishes."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from test_plan_tools import SCRIPTS, run

ROOT = SCRIPTS.parents[2]
EX = SCRIPTS / "exclusive.py"
ORCH = SCRIPTS / "orchestrator.py"
HOOK = ROOT / "hooks" / "session-start"
_GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"
BASH = _GIT_BASH if os.name == "nt" and os.path.exists(_GIT_BASH) else shutil.which("bash")
PEER = "superpowers@claude-plugins-official"


def test_orchestrator_set_suspends_and_finish_restores(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(ROOT))
    other = {"enabledPlugins": {PEER: True, "other@market": True}, "theme": "dark"}
    settings = tmp_path / ".claude" / "settings.local.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps(other) + "\n", encoding="utf-8")
    cp = run(ORCH, "set", "ai", "--plan", tmp_path / "plan.md", cwd=tmp_path)
    assert cp.returncode == 0 and "peers: suspended superpowers" in cp.out
    saved = json.loads((tmp_path / ".superlite" / "peers.json").read_text(encoding="utf-8"))
    assert saved["suspended"] is True and saved["previous_enabled"] is True
    assert saved.get("owners") == ["legacy"]
    turned = json.loads(settings.read_text(encoding="utf-8"))
    assert turned["enabledPlugins"][PEER] is False and turned["enabledPlugins"]["other@market"] is True
    assert turned["theme"] == "dark"
    # a second set must not record the false we just wrote as the value to restore
    run(ORCH, "set", "human", "--plan", tmp_path / "plan.md", cwd=tmp_path)
    again = json.loads((tmp_path / ".superlite" / "peers.json").read_text(encoding="utf-8"))
    assert again["since"] == saved["since"] and again["previous_enabled"] is True
    sl = tmp_path / ".superlite"
    (sl / "progress.md").write_text("# Progress ledger\n", encoding="utf-8")
    done = run(SCRIPTS / "finish.py", "--ledger", sl / "progress.md", "--log", sl / "i.jsonl", cwd=tmp_path)
    assert "peers: clear" in done.out
    assert not (sl / "peers.json").is_file()
    restored = json.loads(settings.read_text(encoding="utf-8"))
    assert restored["enabledPlugins"][PEER] is True and restored["theme"] == "dark"


def test_release_removes_a_flag_superlite_added(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE", "1")
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    run(EX, "on", cwd=tmp_path)
    settings = tmp_path / ".claude" / "settings.local.json"
    assert json.loads(settings.read_text(encoding="utf-8"))["enabledPlugins"][PEER] is False
    run(EX, "off", cwd=tmp_path)
    assert not settings.is_file()
    assert "clear" in run(EX, "status", cwd=tmp_path).out


def test_unreadable_settings_are_left_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(ROOT))
    settings = tmp_path / ".claude" / "settings.local.json"
    settings.parent.mkdir()
    settings.write_text("{not json", encoding="utf-8")
    assert run(EX, "on", cwd=tmp_path).returncode == 0
    assert settings.read_text(encoding="utf-8") == "{not json"
    run(EX, "off", cwd=tmp_path)
    assert settings.read_text(encoding="utf-8") == "{not json"


def test_session_start_mentions_the_suspension_only_while_it_holds(tmp_path):
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT), SUPERLITE_HOME=str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    quiet = subprocess.run([BASH, str(HOOK)], cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", env=env)
    assert quiet.returncode == 0
    assert "Peer process plugins" not in json.loads(quiet.stdout)["hookSpecificOutput"]["additionalContext"]
    sl = tmp_path / ".superlite"
    sl.mkdir()
    (sl / "peers.json").write_text(json.dumps({"suspended": True, "peers": ["superpowers"]}) + "\n", encoding="utf-8")
    loud = subprocess.run([BASH, str(HOOK)], cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", env=env)
    ctx = json.loads(loud.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "superpowers" in ctx and "can be used again" in ctx
