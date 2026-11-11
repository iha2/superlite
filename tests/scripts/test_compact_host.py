import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "swarm" / "scripts"

sys.path.insert(0, str(SCRIPTS))
import compact_host  # noqa: E402

FALLBACK = (
    "your host's compact command (none known: write a handoff note and start a fresh session)"
)


def test_unknown_host_fallback():
    assert compact_host.command("nonesuch") == FALLBACK
    assert compact_host.confidence("nonesuch") == "none"
