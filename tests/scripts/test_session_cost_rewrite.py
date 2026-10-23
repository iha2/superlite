import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "hooks" / "rewrite-test-runs"
_GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"
BASH = _GIT_BASH if os.name == "nt" and os.path.exists(_GIT_BASH) else shutil.which("bash")


def run(proj, command=None, raw=None, **extra):
    if raw is None:
        raw = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, **extra})
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(proj)}
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    return subprocess.run([BASH, str(HOOK)], input=raw, capture_output=True, text=True, env=env)


def test_scoped_pytest_rewrites_to_fast(tmp_path):
    (tmp_path / ".superlite").mkdir()
    (tmp_path / ".superlite" / "pool.json").write_text("{}")
    r = run(tmp_path, "pytest tests/x.py")
    assert r.returncode == 0 and r.stderr == ""
    out = json.loads(r.stdout)["hookSpecificOutput"]
    cmd = out["updatedInput"]["command"]
    assert "--fast" in cmd
    assert "tests/x.py" in cmd
    assert "--full" not in cmd


def test_subagent_full_suite_denied(tmp_path):
    (tmp_path / ".superlite").mkdir()
    (tmp_path / ".superlite" / "pool.json").write_text("{}")
    r = run(tmp_path, "npm test", agent_id="a1", agent_type="implementer")
    assert r.returncode == 2
    assert r.stdout == ""
