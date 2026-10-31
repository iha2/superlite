"""Seam check: every flow entry runs `sl session ensure` before its first other `sl` call."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FLOWS = ["commands/start.md", "commands/small.md", "commands/mvp.md", "commands/feature.md",
         "commands/debug.md", "commands/review.md", "skills/inventing/SKILL.md"]
ENSURE = re.compile(r"\bsl session ensure\b")  # O(1)
OTHER = re.compile(r"\bsl (?!session\b)(\w+)")  # O(1)
EXEMPT = {"start.md": {"suggest_flow"}}  # mode suggestion precedes the request being known


def order_ok(rel: str, text: str) -> bool:  # O(n)
    m = ENSURE.search(text)
    if not m:
        return False
    exempt = EXEMPT.get(Path(rel).name, set())
    for o in OTHER.finditer(text):
        if o.group(1) in exempt:
            continue
        if o.start() < m.start():
            line_start = text.rfind("\n", 0, m.start()) + 1
            if o.start() >= line_start:
                continue  # same line as the ensure: "BEFORE `sl orchestrator set`" wording
            return False
        break
    return True


@pytest.mark.parametrize("rel", FLOWS)  # O(1)
def test_ensure_first_and_ask_documented(rel):  # O(n)
    text = (ROOT / rel).read_text(encoding="utf-8")
    assert order_ok(rel, text), rel
    assert "ASK untracked" in text, rel


@pytest.mark.parametrize("rel", FLOWS)  # O(1)
def test_check_fails_without_ensure(rel, tmp_path):  # O(n)
    text = (ROOT / rel).read_text(encoding="utf-8")
    stripped = "\n".join(ln for ln in text.splitlines() if "sl session ensure" not in ln)
    p = tmp_path / Path(rel).name
    p.write_text(stripped, encoding="utf-8")
    assert not order_ok(rel, p.read_text(encoding="utf-8")), rel


def test_shipping_closes_and_handles_ask():  # O(1)
    text = (ROOT / "skills/shipping/SKILL.md").read_text(encoding="utf-8")
    assert "sl session close" in text
    assert "ASK untracked" in text
