"""Instruction test: the final reviewer answers `Verdict:` then `file:line - problem - fix - tier N` lines."""
import re

import pytest

from tests.live.runner import run_instruction

pytestmark = pytest.mark.live


PROMPT = """
Final review packet (tiny). One file changed, src/mean.py:
    def mean(xs):
        return sum(xs) / len(xs)
The spec says mean([]) must return 0.0. No test covers the empty list.
Do not read or write any file. Reply with your final review now.
"""

FINDING = re.compile(r"\S+:\d+\s+-\s+.+\s+-\s+.+\s+-\s+tier\s+[123]", re.I)


def test_final_reviewer_uses_the_verdict_and_findings_format(require_claude):
    out = run_instruction("final-reviewer", PROMPT, expect="Verdict:", rule="final-reviewer-format")
    print(f"outcome={out.kind} seconds={out.seconds} cost_usd={out.cost_usd} tokens={out.tokens}")
    if out.detail == "skipped unchanged":
        return
    assert out.kind == "Followed", out.detail[:300]
    assert out.cost_usd <= 0.02
    assert out.seconds <= 30
    assert "Verdict:" in out.detail
    assert FINDING.search(out.detail), out.detail[:300]
    out.mark_pass()
