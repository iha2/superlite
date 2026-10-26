import importlib
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parents[2] / "skills"
          / "swarm" / "scripts" / "models_refresh.py")
DEFAULT = (Path(__file__).resolve().parents[2] / "skills"
           / "swarm" / "config" / "models.json")
sys.path.insert(0, str(SCRIPT.parent))
models_refresh = importlib.import_module("models_refresh")


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True)


def seed_dest(dest: Path) -> None:
    dest.write_text(DEFAULT.read_text(encoding="utf-8"), encoding="utf-8")


def test_refresh_atomic_write_no_stale_tmp(tmp_path):
    dest = tmp_path / "models.json"
    feed = tmp_path / "feed.json"
    seed_dest(dest)
    feed.write_text(json.dumps({"models": {"sonnet": {"input": 3.0}}}), encoding="utf-8")
    out = models_refresh.refresh(str(dest), str(feed))
    assert not list(tmp_path.glob("*.tmp"))
    on_disk = json.loads(dest.read_text(encoding="utf-8"))
    assert on_disk["models"]["sonnet"]["input"] == 3.0
    assert out == on_disk


def test_refresh_updates_rates_and_timestamp(tmp_path):
    dest = tmp_path / "models.json"
    feed = tmp_path / "feed.json"
    seed_dest(dest)
    feed.write_text(json.dumps({"models": {"sonnet": {"input": 3.0}}}), encoding="utf-8")
    out = models_refresh.refresh(str(dest), str(feed))
    assert out["models"]["sonnet"]["input"] == 3.0
    assert isinstance(out.get("updated_at"), str) and out["updated_at"].strip()
    assert json.loads(dest.read_text(encoding="utf-8"))["updated_at"] == out["updated_at"]


def test_refresh_missing_feed_exits_2(tmp_path):
    dest = tmp_path / "models.json"
    seed_dest(dest)
    r = run("refresh", "--dest", str(dest), "--feed", str(tmp_path / "missing.json"))
    assert r.returncode == 2
