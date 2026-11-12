import re
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parent.parent / "src" / "content" / "docs" / "start"
PAGES = ["quickstart", "install", "commands"]


def split(text):
    m = re.match(r"---\n(.*?)\n---\n(.*)", text, re.S)
    assert m, "missing frontmatter"
    return m.group(1), m.group(2)


def prose_words(body):
    body = re.sub(r"```.*?```", "", body, flags=re.S)
    lines = [l for l in body.splitlines() if not l.lstrip().startswith("|")]
    return len(re.findall(r"\w+", "\n".join(lines)))


@pytest.mark.parametrize("name", PAGES)
def test_start_pages(name):
    front, body = split((DOCS / f"{name}.md").read_text())
    assert re.search(r"^title: .+", front, re.M)
    assert re.search(r"^description: .+", front, re.M)
    assert re.search(r"^\s+order: \d+", front, re.M)
    assert body.lstrip().startswith(("```mermaid", "```text"))
    heads = re.findall(r"^## (.+)$", re.sub(r"```.*?```", "", body, flags=re.S), re.M)
    assert heads == ["What you do", "What superlite does", "Example"]
    assert prose_words(body) <= 300
