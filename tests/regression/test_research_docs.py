"""Research docs must not name the report like a machine plan."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILES = ["skills/research/SKILL.md", "docs/research-flow.md"]


def test_report_path_is_findings():
    for rel in FILES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "<date>-<topic>.md" not in text, rel
        assert "<date>-<topic>.findings.md" in text, rel


def test_every_research_call_names_a_ledger():
    import re
    for rel in FILES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for span in re.findall(r"`([^`]*\bresearch (?:init|source|claim|contradiction|lint|render|downloads|download)\b[^`]*)`", text):
            if "--slug" not in span and "--ledger" not in span:
                # bare names in prose lists are not calls
                assert " --" not in span, (rel, span)
