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


def _has(text: str, phrases) -> bool:  # O(n)
    return all(p in text for p in phrases)


FLOW_PHRASES = ("ASK move", "superlite sync:")
SHIP_PHRASES = ("ASK move", "sl session status")
NEW_CASES = [(rel, FLOW_PHRASES) for rel in FLOWS] + [("skills/shipping/SKILL.md", SHIP_PHRASES)]


@pytest.mark.parametrize("rel,phrases", NEW_CASES)  # O(1)
def test_move_and_sync_documented(rel, phrases):  # O(n)
    assert _has((ROOT / rel).read_text(encoding="utf-8"), phrases), rel


@pytest.mark.parametrize("rel,phrases", NEW_CASES)  # O(1)
@pytest.mark.parametrize("idx", [0, 1])  # O(1)
def test_move_and_sync_check_fails_without_phrase(rel, phrases, idx, tmp_path):  # O(n)
    text = (ROOT / rel).read_text(encoding="utf-8")
    p = tmp_path / Path(rel).name
    p.write_text(text.replace(phrases[idx], ""), encoding="utf-8")
    assert not _has(p.read_text(encoding="utf-8"), phrases), (rel, phrases[idx])


@pytest.mark.parametrize("rel", ["commands/small.md", "commands/debug.md"])  # O(1)
def test_command_runs_sl_finish_after_verification(rel):  # O(n)
    text = (ROOT / rel).read_text(encoding="utf-8")
    assert "sl finish" in text, rel
    assert "uncommitted" in text.lower(), rel


def test_inventing_bounded_path_runs_sl_finish():  # O(n)
    text = (ROOT / "skills" / "inventing" / "SKILL.md").read_text(encoding="utf-8")
    m = re.search(r"^\*\*Bounded:\*\*.*?(?=\n\n|\Z)", text, re.S | re.M)
    assert m, "bounded paragraph not found"
    assert "sl finish" in m.group(0), m.group(0)
    assert "uncommitted" in m.group(0).lower(), m.group(0)


def test_feature_step6_registers_integration_session_and_closes_start_session():  # O(n)
    text = (ROOT / "commands" / "feature.md").read_text(encoding="utf-8")
    m = re.search(r"^6\. \*\*Integration build\.\*\*.*?(?=\n\n|\Z)", text, re.S | re.M)
    assert m, "step 6 not found"
    assert "sl session ensure" in m.group(0), m.group(0)
    assert "sl session close" in m.group(0), m.group(0)


def test_review_resolves_target_before_ensure_and_passes_branch():  # O(n)
    lines = (ROOT / "commands" / "review.md").read_text(encoding="utf-8").splitlines()
    idx = next((i for i, ln in enumerate(lines) if "session ensure" in ln), None)
    assert idx is not None, "no ensure line"
    assert re.search(r"--branch\s+<target>", lines[idx]), lines[idx]
    assert "target" in "\n".join(lines[:idx]).lower(), "target is not resolved before the ensure line"


def test_shipping_merge_uses_temporary_checkout_when_main_hosts_other_session():  # O(n)
    text = (ROOT / "skills" / "shipping" / "SKILL.md").read_text(encoding="utf-8")
    m = re.search(r"\*\*Merge locally:\*\*(.*?)(?=\n- \*\*|\n## )", text, re.S)
    assert m, "Merge locally step not found"
    for phrase in ("temporary", "git worktree add", ".superlite/worktrees/", "session status"):
        assert phrase in m.group(1), phrase


def test_review_keeps_path_targets_on_the_current_branch():  # O(n)
    text = (ROOT / "commands" / "review.md").read_text(encoding="utf-8")
    assert "path" in text and "`<target>` is the current branch" in text, text


def test_start_passes_the_review_target_to_ensure():  # O(n)
    text = (ROOT / "commands" / "start.md").read_text(encoding="utf-8")
    assert re.search(r"Review.*--branch <target>", text), "start.md does not hand the review target to ensure"


def test_feature_cleanup_uses_the_started_session_and_discards_first():  # O(n)
    text = (ROOT / "commands" / "feature.md").read_text(encoding="utf-8")
    m = re.search(r"^6\. \*\*Integration build\.\*\*.*?(?=\n\n|\Z)", text, re.S | re.M)
    assert m, "step 6 not found"
    step = m.group(0)
    assert "sl session close --slug <topic>" not in step, step
    assert "SESSION" in step, step
    assert step.index("sl proto discard") < step.index("sl session close"), step
