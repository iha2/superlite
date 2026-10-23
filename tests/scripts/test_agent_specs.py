"""sl agent_specs on|off|status|open|prefer: opt-in pre-impl specs."""
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parents[1] / "skills" / "subagent-driven-development" / "scripts"
SCRIPT = SCRIPTS / "agent_specs.py"


def run(*args, cwd, env=None):
    base = {k: v for k, v in os.environ.items() if k not in (
        "SUPERLITE_AGENT_SPECS", "SUPERLITE_AGENT_SPECS_OPEN", "SUPERLITE_HOME")}
    if env:
        base.update(env)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, cwd=cwd, env=base, encoding="utf-8",
    )


def test_default_off(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    r = run("status", cwd=tmp_path, env={"SUPERLITE_HOME": str(home)})
    assert r.returncode == 0, r.stderr
    assert "agent_specs: off" in r.stdout
    assert "default" in r.stdout


def test_project_on_off(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = {"SUPERLITE_HOME": str(home)}
    assert run("on", cwd=tmp_path, env=env).returncode == 0
    data = json.loads((tmp_path / ".superlite" / "agent-specs.json").read_text(encoding="utf-8"))
    assert data["agent_specs"] is True
    assert data.get("open_in_harness") is True
    st = run("status", cwd=tmp_path, env=env)
    assert "agent_specs: on" in st.stdout and "open_in_harness: on" in st.stdout
    assert run("off", cwd=tmp_path, env=env).returncode == 0
    assert "agent_specs: off" in run("status", cwd=tmp_path, env=env).stdout


def test_env_overrides_project_on(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = {"SUPERLITE_HOME": str(home)}
    run("on", cwd=tmp_path, env=env)
    st = run("status", cwd=tmp_path, env={**env, "SUPERLITE_AGENT_SPECS": "0"})
    assert "agent_specs: off" in st.stdout
    assert "SUPERLITE_AGENT_SPECS" in st.stdout


def test_open_flag(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = {"SUPERLITE_HOME": str(home)}
    run("on", cwd=tmp_path, env=env)
    assert run("open", "off", cwd=tmp_path, env=env).returncode == 0
    st = run("status", cwd=tmp_path, env=env)
    assert "agent_specs: on" in st.stdout and "open_in_harness: off" in st.stdout
    data = json.loads((tmp_path / ".superlite" / "agent-specs.json").read_text(encoding="utf-8"))
    assert data["open_in_harness"] is False


def test_prefer_once(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = {"SUPERLITE_HOME": str(home)}
    assert "agent_specs" not in json.loads("{}")
    r = run("prefer", "off", cwd=tmp_path, env=env)
    assert r.returncode == 0, r.stderr
    pref = json.loads((home / "preferences.json").read_text(encoding="utf-8"))
    assert pref["agent_specs"] is False
    assert pref.get("open_in_harness") is True
    st = run("status", cwd=tmp_path, env=env)
    assert "agent_specs: off" in st.stdout and "prefer" in st.stdout
    run("prefer", "on", cwd=tmp_path, env=env)
    assert "agent_specs: on" in run("status", cwd=tmp_path, env=env).stdout


def test_project_overrides_prefer(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = {"SUPERLITE_HOME": str(home)}
    run("prefer", "on", cwd=tmp_path, env=env)
    run("off", cwd=tmp_path, env=env)
    assert "agent_specs: off" in run("status", cwd=tmp_path, env=env).stdout
    assert "agent-specs.json" in run("status", cwd=tmp_path, env=env).stdout
