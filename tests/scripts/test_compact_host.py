import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "swarm" / "scripts"
CONFIG = ROOT / "skills" / "swarm" / "config" / "compact.json"

sys.path.insert(0, str(SCRIPTS))
import compact_host  # noqa: E402

FALLBACK = (
    "your host's compact command (none known: write a handoff note and start a fresh session)"
)

EXPECTED_COMMANDS = {
    "claude-code": "/compact",
    "cursor": "/summarize",
    "codex": "/compact",
    "gemini": "/compress",
    "opencode": "/compact",
    "pi": "/compact",
    "kimi": "/compact",
    "hermes": "/compress",
    "devin": "",
    "antigravity": "",
    "muse": "",
}

EXPECTED_CONFIDENCE = {
    "claude-code": "docs",
    "cursor": "forum",
    "codex": "docs",
    "gemini": "docs",
    "opencode": "docs",
    "pi": "docs",
    "kimi": "docs",
    "hermes": "docs",
    "devin": "none",
    "antigravity": "none",
    "muse": "none",
}


def test_each_row_command():
    for host, command in EXPECTED_COMMANDS.items():
        if command:
            assert compact_host.command(host) == command, host
        else:
            assert compact_host.command(host) == FALLBACK, host


def test_each_row_confidence():
    for host, confidence in EXPECTED_CONFIDENCE.items():
        assert compact_host.confidence(host) == confidence, host


def test_unknown_host_fallback():
    assert compact_host.command("nonesuch") == FALLBACK
    assert compact_host.confidence("nonesuch") == "none"


def test_empty_host_fallback():
    assert compact_host.command("") == FALLBACK
    assert compact_host.confidence("") == "none"


def test_json_is_data_only():
    data = json.loads(CONFIG.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert data, "compact.json must not be empty"
    for host, row in data.items():
        assert isinstance(row, dict), host
        assert "command" in row, host
        assert "confidence" in row, host
        assert isinstance(row["command"], str), host
        assert isinstance(row["confidence"], str), host
