import re
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parent.parent / "src" / "content" / "docs" / "run"
PAGES = ["orchestrators", "plan-and-approval", "parallel-build", "reviews-and-fixes", "report"]
MERMAID_TYPES = ("flowchart", "sequenceDiagram", "stateDiagram-v2", "gitGraph")


def _page(name):
    return (DOCS / f"{name}.mdx").read_text()


def _prose_words(text):
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    lines = [l for l in text.split("\n") if not l.strip().startswith("|") and not l.startswith("#")]
    return len(" ".join(lines).split())


def _body(name):
    text = _page(name)
    assert text.startswith("---\n")
    fm, body = text[4:].split("\n---\n", 1)
    return fm, body


@pytest.mark.parametrize("name", PAGES)
def test_run_pages(name):
    fm, body = _body(name)
    assert "title:" in fm and "description:" in fm and "order:" in fm
    lines = [l for l in body.split("\n") if l.strip() and not l.startswith("import ")]
    assert lines[0] == ":::tip[TL;DR]"
    end = lines.index(":::")
    assert len(" ".join(lines[1:end]).split()) <= 60
    first_h2 = body.index("\n## ")
    head = body[:first_h2]
    assert re.search(r"```(mermaid|text)\n", head), "diagram before the first ## heading"
    for block in re.findall(r"```mermaid\n(.*?)```", body, flags=re.S):
        first = next(l.strip() for l in block.split("\n") if l.strip() and not l.strip().startswith("%%"))
        assert first.split()[0] in MERMAID_TYPES
    assert any(l.startswith("## Example") for l in body.split("\n"))
    assert _prose_words(body) <= 900


def test_orchestrators_tabs_and_limits():
    body = _page("orchestrators")
    assert "<Tabs>" in body and body.count("<TabItem") == 2
    assert "sequenceDiagram" in body
    assert "push, merge or publish" in body
    for state in ("human, awaiting approval", "AI", "not set (ungated)"):
        assert state in body


def test_plan_and_approval_formats():
    body = _page("plan-and-approval")
    assert "review plan" in body and "machine plan" in body
    assert ".review.md" in body and "%% taskgraph" in body
    assert "orchestrator approve --review" in body
    assert "SIZE:" in body and "STABLE" in body


def test_parallel_build_waves_and_tiers():
    body = _page("parallel-build")
    for word in ("implementer-haiku", "implementer", "Kind: mechanical", "refs/superlite/*", "critical path"):
        assert word in body
    assert len(re.findall(r"\bT[1-6]\b \w", body)) >= 6
    assert "three waves" in body


def test_haiku_never_fixes():
    text = _page("reviews-and-fixes")
    assert "Haiku never fixes" in text
    for tier in ("fixer-sonnet", "fixer-sonnet-high", "fixer-opus"):
        assert tier in text
    assert "package-reviewer" in text and "final-reviewer" in text
    assert "NOT_FIXED" in text and "regression test" in text


def test_report_explains_numbers():
    body = _page("report")
    for word in ("Projected", "first-time pass", "critical path", "takeaway", "history write", "/superlite:report"):
        assert word in body


@pytest.mark.parametrize("name", PAGES)
def test_example_is_a_framed_walkthrough(name):
    body = _page(name)
    example = body[body.index("\n## Example"):]
    assert "<Steps>" in example and "</Steps>" in example
    assert re.search(r'```(text|md) (frame="terminal" )?[^\n]*title=', example) or "![" in example
    assert "**What you end up with:**" in example
    assert "illustrative" not in body.lower()
    for img in re.findall(r"\]\((\.\./[^)]*\.png)\)", body):
        assert (DOCS / img).resolve().exists(), img
