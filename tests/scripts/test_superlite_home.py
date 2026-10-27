"""The global superlite home: explicit override, then a legacy ~/.superlite, then the XDG data dir.
Python (history.home) and bash (hooks/session-start) must agree, or history and the intro marker split across dirs."""
import os
import subprocess
import sys

import pytest

from test_plan_tools import SCRIPTS
from test_session_start import BASH, HOOK, ROOT

sys.path.insert(0, str(SCRIPTS))
import history  # noqa: E402


@pytest.fixture
def user(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPERLITE_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def hook_home(user, **extra):
    """Where session-start put its intro marker, i.e. the home it resolved."""
    env = {k: v for k, v in os.environ.items() if k not in ("SUPERLITE_HOME", "XDG_DATA_HOME")}
    env.update(CLAUDE_PLUGIN_ROOT=str(ROOT), HOME=str(user), **extra)
    r = subprocess.run([BASH, str(HOOK)], input="", capture_output=True, text=True, encoding="utf-8", env=env)
    assert r.returncode == 0, r.stderr
    hits = [p.parent for p in user.rglob("intro-shown-*")]
    assert len(hits) == 1, hits
    return hits[0]


def test_default_is_xdg_not_dotdir(user):
    # new installs must not create ~/.superlite
    assert history.home() == user / ".local" / "share" / "superlite"
    assert hook_home(user) == history.home()
    assert not (user / ".superlite").exists()


def test_xdg_data_home_is_honored(user, monkeypatch, tmp_path):
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
    assert history.home() == xdg / "superlite"
    assert hook_home(user, XDG_DATA_HOME=str(xdg)) == xdg / "superlite"


def test_relative_xdg_data_home_is_ignored(user, monkeypatch):
    # the XDG spec says relative values are invalid; resolving against the cwd would scatter history
    monkeypatch.setenv("XDG_DATA_HOME", "rel/path")
    assert history.home() == user / ".local" / "share" / "superlite"
    assert hook_home(user, XDG_DATA_HOME="rel/path") == history.home()


def test_existing_legacy_dir_keeps_winning(user, monkeypatch, tmp_path):
    # upgrading must not orphan existing history.jsonl and preferences.json
    (user / ".superlite").mkdir()
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert history.home() == user / ".superlite"
    assert hook_home(user, XDG_DATA_HOME=str(tmp_path / "xdg")) == user / ".superlite"


def test_superlite_home_beats_everything(user, monkeypatch, tmp_path):
    (user / ".superlite").mkdir()
    explicit = tmp_path / "explicit"
    monkeypatch.setenv("SUPERLITE_HOME", str(explicit))
    assert history.home() == explicit
    assert hook_home(user, SUPERLITE_HOME=str(explicit)) == explicit
