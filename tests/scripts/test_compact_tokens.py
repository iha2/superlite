import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"))
import compact_due  # noqa: E402
import project_config  # noqa: E402


@pytest.fixture(autouse=True)
def _noenv(monkeypatch):
    monkeypatch.delenv("SUPERLITE_COMPACT_TOKENS", raising=False)


def _cfg(tmp_path, value):
    (tmp_path / "superlite.json").write_text(json.dumps({"version": 1, "compact_tokens": value}))


def test_default_limit(tmp_path):
    assert compact_due.limit(str(tmp_path)) == 150000


def test_config_limit(tmp_path):
    _cfg(tmp_path, 50000)
    assert compact_due.limit(str(tmp_path)) == 50000


def test_env_beats_config(tmp_path, monkeypatch):
    _cfg(tmp_path, 50000)
    monkeypatch.setenv("SUPERLITE_COMPACT_TOKENS", "40000")
    assert compact_due.limit(str(tmp_path)) == 40000


def test_invalid_value_rejected(tmp_path, monkeypatch):
    for bad in ("lots", 0):
        _cfg(tmp_path, bad)
        with pytest.raises(ValueError, match="compact_tokens"):
            compact_due.limit(str(tmp_path))
    (tmp_path / "superlite.json").unlink()
    monkeypatch.setenv("SUPERLITE_COMPACT_TOKENS", "abc")
    with pytest.raises(ValueError, match="compact_tokens"):
        compact_due.limit(str(tmp_path))


def test_token_nudge_first():
    assert compact_due.token_nudge(162000, 150000, "/compact") == (
        "Context is 162k tokens (limit 150k): run /compact now, "
        "then re-read .superlite/progress.md and continue."
    )


def test_token_nudge_repeat():
    assert compact_due.token_nudge(170000, 150000, "/compact", request=2) == (
        "Compact still pending (request 2, 170k tokens, limit 150k): "
        "run /compact now before anything else."
    )


def test_old_api_unchanged(tmp_path):
    assert compact_due.nudge_line() == (
        "Session context is stale: please compact now (host compact / summarize), "
        "then continue. Stamp .superlite/last-compact when done."
    )
    assert compact_due.is_due(str(tmp_path), 2000.0, 1800) is True
    assert compact_due.is_due(str(tmp_path), 100.0, 1800) is False


def test_project_config_accepts_compact_tokens(tmp_path):
    _cfg(tmp_path, 1000)
    assert project_config.load(tmp_path)["compact_tokens"] == 1000
    (tmp_path / "superlite.json").write_text('{"version": 1, "bogus": 1}')
    with pytest.raises(project_config.ProjectConfigError):
        project_config.load(tmp_path)
