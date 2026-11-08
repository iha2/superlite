"""Research docs must not name the report like a machine plan."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILES = ["skills/research/SKILL.md", "docs/research-flow.md"]


def test_report_path_is_findings():
    for rel in FILES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "<date>-<topic>.md" not in text, rel
        assert "<date>-<topic>.findings.md" in text, rel
