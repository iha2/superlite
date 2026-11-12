import re
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parent.parent / "src" / "content" / "docs"
PATHS = [
    "reference/skills.md",
    "reference/agents.md",
    "reference/hooks.md",
    "reference/gates.md",
    "reference/cli.md",
    "reference/config.md",
    "reference/language.md",
    "reference/harnesses.md",
    "results/benchmark.md",
]


def _parts(rel):
    text = (DOCS / rel).read_text()
    m = re.match(r"---\n(.*?)\n---\n(.*)", text, re.S)
    assert m, f"{rel}: no frontmatter"
    return m.group(1), m.group(2)


def _prose_words(body):
    out, fenced = [], False
    for line in body.split("\n"):
        if line.strip().startswith("```"):
            fenced = not fenced
            continue
        if fenced or line.strip().startswith("|") or line.startswith("#"):
            continue
        out.append(line)
    return len(re.findall(r"\S+", " ".join(out)))


@pytest.mark.parametrize("rel", PATHS)
def test_reference_pages(rel):
    front, body = _parts(rel)
    assert re.search(r"^title: \S", front, re.M)
    assert re.search(r"^description: \S", front, re.M)
    assert "order:" in front
    assert _prose_words(body) <= 300
