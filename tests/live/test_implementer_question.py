"""Instruction test: a question is sent at once with done and unstarted lists."""
import pytest

from tests.live.runner import run_instruction

pytestmark = pytest.mark.live


PROMPT = """
You already have READY. Your batch is T1, T2, T3 in that order.
T1 is finished and checkpointed. You are about to start T2. The plan for T2 says
"return the parsed value" but never says which type. You cannot resolve it from
`sl task` or the SPEC. Do not write any file. What is your immediate reply?
"""


def test_question_at_once_lists_done_and_unstarted(require_claude):
    out = run_instruction("implementer", PROMPT, expect="NEEDS_CONTEXT", rule="question-at-once")
    print(f"outcome={out.kind} seconds={out.seconds} cost_usd={out.cost_usd} tokens={out.tokens}")
    if out.detail == "skipped unchanged":
        return
    assert out.kind == "Followed", out.detail[:300]
    assert out.cost_usd <= 0.02
    assert out.seconds <= 30
    text = out.detail.upper()
    assert "NEEDS_CONTEXT" in text
    # done + unstarted named (T1 finished; T2/T3 not started)
    assert "T1" in text
    assert "T2" in text or "T3" in text
