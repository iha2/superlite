"""Instruction test: a failed task does not stop the batch."""
import pytest

from tests.live.runner import Outcome, run_instruction

pytestmark = pytest.mark.live


PROMPT = """
You already have READY. Your batch is T1, T2, T3, T4 in that order.
T1 is done. T2's tests stay red after your best fix (treat it as failed).
T3's tests import and run T2's code. T4 is independent.
Do not actually edit files. Reply now with your per-task status blocks only.
What statuses do you report for T2, T3, and T4?
"""


def test_failed_task_does_not_stop_batch(require_claude):
    out = run_instruction("implementer", PROMPT, expect="BLOCKED", rule="failed-batch-continues")
    print(f"outcome={out.kind} seconds={out.seconds} cost_usd={out.cost_usd} tokens={out.tokens}")
    if out.detail == "skipped unchanged":
        return
    assert out.kind == "Followed", out.detail[:400]
    assert out.cost_usd <= 0.02
    assert out.seconds <= 30
    text = out.detail.upper()
    assert "BLOCKED" in text
    assert "T4" in text  # rest of batch still reported
