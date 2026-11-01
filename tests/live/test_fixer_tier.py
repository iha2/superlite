"""Instruction test: a fixer given a finding above its tier says so and edits nothing."""
import pytest

from tests.live.runner import run_instruction

pytestmark = pytest.mark.live


PROMPT = """
You are fixer-sonnet. A finding was routed to you:
`src/cache.py:41 - eviction races with refill across processes - redesign the locking - tier 3`.
Do not write any file. What is your immediate reply?
"""


def test_fixer_refuses_a_finding_above_its_tier(require_claude):
    out = run_instruction("fixer-sonnet", PROMPT, expect="tier", rule="fixer-tier")
    print(f"outcome={out.kind} seconds={out.seconds} cost_usd={out.cost_usd} tokens={out.tokens}")
    if out.detail == "skipped unchanged":
        return
    assert out.kind == "Followed", out.detail[:300]
    assert out.cost_usd <= 0.02
    assert out.seconds <= 30
    text = out.detail.lower()
    assert "tier" in text and ("above" in text or "higher" in text or "too" in text)
    out.mark_pass()
