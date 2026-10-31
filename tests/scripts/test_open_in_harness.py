"""Seam test: every plan-based flow opens its plan when open_in_harness is on."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FLAG = "open_in_harness"
OPEN_ROW = "| Open a plan or spec for the user (`open_in_harness`) |"

FLOW_FILES = [
    "skills/drafting/SKILL.md",
    "skills/drafting/review-plan.md",
    "skills/inventing/SKILL.md",
    "skills/swarm/orchestrator.md",
    "skills/mvp-prototype/SKILL.md",
    "skills/writing-specs/SKILL.md",
    "agents/implementer.md",
    "commands/feature.md",
    "commands/approve.md",
    "commands/mvp.md",
    "commands/resume.md",
    "docs/feature-flow.md",
    "docs/mvp-flow.md",
    "docs/tools.md",
]
PLANLESS = ["commands/small.md", "commands/debug.md", "commands/review.md"]
SUMMARY_FILES = [
    "skills/drafting/SKILL.md",
    "skills/inventing/SKILL.md",
    "skills/swarm/orchestrator.md",
]


def text(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


@pytest.mark.parametrize("rel", FLOW_FILES)
def test_flow_steps_open(rel):
    assert FLAG in text(rel), f"{rel} must mention {FLAG}"


def test_harness_rows():
    files = sorted((ROOT / "skills/using-superlite/references").glob("*-tools.md"))
    assert len(files) == 8
    for f in files:
        rows = [l for l in f.read_text(encoding="utf-8").splitlines() if l.startswith(OPEN_ROW)]
        assert len(rows) == 1, f"{f.name}: expected one open row, found {len(rows)}"


@pytest.mark.parametrize("rel", PLANLESS)
def test_planless_flows_open_nothing(rel):
    assert FLAG not in text(rel)


@pytest.mark.parametrize("rel", SUMMARY_FILES)
def test_five_line_summary_kept(rel):
    assert "five-line summary" in text(rel)
