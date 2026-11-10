"""Keep the user's real ~/.superlite history out of the script tests: lint, schedule and predict read it."""
import pytest


@pytest.fixture(autouse=True)
def _isolated_superlite_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERLITE_HOME", str(tmp_path / "_sl_home"))


@pytest.fixture(autouse=True)
def _not_under_claude_code(monkeypatch):
    """Claude Code sets CLAUDECODE=1; tests that exercise the `session: enter` line set it themselves."""
    monkeypatch.delenv("CLAUDECODE", raising=False)
