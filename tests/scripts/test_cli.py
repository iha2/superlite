import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "swarm" / "scripts"
_GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"
BASH = _GIT_BASH if os.name == "nt" and os.path.exists(_GIT_BASH) else shutil.which("bash")


def test_cli_runs_a_tool(tmp_path):
    (tmp_path / ".superlite").mkdir()
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path)}
    r = subprocess.run([BASH, str(SCRIPTS / "superlite-cli"), "orchestrator", "status"],
                       cwd=tmp_path, capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr


def test_sl_is_gone():
    assert not (SCRIPTS / "sl").exists()
    assert (SCRIPTS / "superlite-cli").is_file()
