import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "swarm" / "scripts"
HOOK = ROOT / "hooks" / "compact-nudge"
_GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"
BASH = _GIT_BASH if os.name == "nt" and os.path.exists(_GIT_BASH) else shutil.which("bash")

sys.path.insert(0, str(SCRIPTS))
import compact_due  # noqa: E402


def _sl(tmp_path, *, compact_mtime=None, launch_t=0):
    sl = tmp_path / ".superlite"
    sl.mkdir()
    (sl / "metrics.jsonl").write_text(
        json.dumps({"ev": "launch", "t": launch_t, "agent_id": "a1"}) + "\n", encoding="utf-8"
    )
    if compact_mtime is not None:
        marker = sl / "last-compact"
        marker.write_text("", encoding="utf-8")
        os.utime(marker, (compact_mtime, compact_mtime))
    return sl


def test_due_when_stale(tmp_path):
    sl = _sl(tmp_path, compact_mtime=0, launch_t=0)
    assert compact_due.is_due(str(sl), now=10000.0, window_s=100.0) is True


def test_not_due_when_fresh(tmp_path):
    sl = _sl(tmp_path, compact_mtime=50, launch_t=0)
    assert compact_due.is_due(str(sl), now=100.0, window_s=100.0) is False


def test_opt_out_env(tmp_path):
    _sl(tmp_path, compact_mtime=0, launch_t=0)
    env = {**os.environ, "SUPERLITE_NO_AUTOCOMPACT": "1", "CLAUDE_PROJECT_DIR": str(tmp_path)}
    r = subprocess.run([BASH, str(HOOK)], input="{}", capture_output=True, text=True, env=env)
    assert r.returncode == 0
    assert "compact" not in r.stdout.lower()
    assert "compact" not in r.stderr.lower()


def test_nudge_line_mentions_compact():
    assert "compact" in compact_due.nudge_line().lower()
