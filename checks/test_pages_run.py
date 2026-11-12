import re
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parent.parent / "src" / "content" / "docs" / "run"
PAGES = ["orchestrators", "plan-and-approval", "parallel-build", "reviews-and-fixes", "report"]


def _prose_words(text):
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    lines = [l for l in text.split("\n") if not l.strip().startswith("|") and not l.startswith("#")]
    return len(" ".join(lines).split())


@pytest.mark.parametrize("name", PAGES)
def test_run_pages(name):
    text = (DOCS / f"{name}.md").read_text()
    assert text.startswith("---\n")
    fm, body = text[4:].split("\n---\n", 1)
    assert "title:" in fm and "description:" in fm and "order:" in fm
    assert re.match(r"\s*```(mermaid|text)\n", body)
    heads = [l for l in body.split("\n") if l.startswith("## ")]
    assert heads == ["## What you do", "## What superlite does", "## Example"]
    assert _prose_words(body) <= 300


def test_haiku_never_fixes():
    text = (DOCS / "reviews-and-fixes.md").read_text()
    assert "Haiku never fixes" in text
    for tier in ("fixer-sonnet", "fixer-sonnet-high", "fixer-opus"):
        assert tier in text
