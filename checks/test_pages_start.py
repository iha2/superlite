import re
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parent.parent / "src" / "content" / "docs"
START = DOCS / "start"
PAGES = {"quickstart": 1, "concepts": 2, "install": 3, "commands": 4}
MERMAID = ("flowchart", "sequenceDiagram", "stateDiagram-v2", "gitGraph")
CONCEPT_LINKS = [
    "/superlite/flows/choose/",
    "/superlite/sessions/sessions/",
    "/superlite/sessions/worktrees/",
    "/superlite/sessions/sandbox/",
    "/superlite/sessions/lifecycle/",
    "/superlite/run/orchestrators/",
    "/superlite/run/plan-and-approval/",
    "/superlite/run/parallel-build/",
    "/superlite/run/reviews-and-fixes/",
    "/superlite/run/report/",
]


def split(text):
    m = re.match(r"---\n(.*?)\n---\n(.*)", text, re.S)
    assert m, "missing frontmatter"
    return m.group(1), m.group(2)


def strip_fences(body):
    return re.sub(r"```.*?```", "", body, flags=re.S)


def prose_words(body):
    lines = [l for l in strip_fences(body).splitlines() if not l.lstrip().startswith("|")]
    return len(re.findall(r"\w+", "\n".join(lines)))


def page(name):
    return split((START / f"{name}.mdx").read_text())


@pytest.mark.parametrize("name,order", PAGES.items())
def test_start_pages(name, order):
    front, body = page(name)
    assert re.search(r"^title: .+", front, re.M)
    assert re.search(r"^description: .+", front, re.M)
    assert re.search(rf"^\s+order: {order}$", front, re.M)
    # TL;DR aside first (imports may precede it), at most 60 words
    m = re.search(r"^:::tip\[TL;DR\]\n(.*?)\n:::$", body, re.M | re.S)
    assert m, "TL;DR aside missing"
    assert len(re.findall(r"\w+", m.group(1))) <= 60
    before = body[: m.start()]
    assert all(l.strip() == "" or l.startswith("import ") for l in before.splitlines())
    # a diagram before the first ## heading
    first_h2 = re.search(r"^## ", body, re.M).start()
    head = body[m.end(): first_h2]
    assert "```mermaid" in head or "```text" in head
    for block in re.findall(r"```mermaid\n(.*?)```", body, re.S):
        assert block.lstrip().startswith(MERMAID)
    assert re.search(r"^## Example", body, re.M)
    assert prose_words(body) <= 900


def test_stale_md_pages_gone():
    assert sorted(p.name for p in START.glob("*.md")) == []


def test_quickstart_is_a_full_first_run():
    _, body = page("quickstart")
    assert "<Steps>" in body
    assert "/plugin marketplace add iha2/superlite" in body
    assert "/plugin install superlite@superlite-dev" in body
    assert "Let's make a react todo list" in body
    assert "inventing" in body
    assert "Python 3.12" in body
    assert "/superlite:finish" in body
    # the walkthrough follows the docs site's own run, in screens
    assert "PR #58" in body
    assert "docs/plans/2026-11-12-docs-site.review.md" in body
    assert 'frame="terminal"' in body
    assert "**You decide:**" in body
    assert "illustrative" not in body


@pytest.mark.parametrize("name", ["quickstart", "concepts", "install", "commands"])
def test_examples_are_walkthroughs(name):
    _, body = page(name)
    example = body[re.search(r"^## Example", body, re.M).start():]
    assert "<Steps>" in example
    assert 'frame="terminal"' in example or 'title="docs/plans' in example
    assert "illustrative" not in body
    for img in re.findall(r"\]\((\.\./[^)]*\.png)\)", body):
        assert (START / img).resolve().exists(), img


def test_index_walkthrough():
    text = (DOCS / "index.mdx").read_text()
    run = text[text.index("## What a run looks like"):text.index("## Where to go")]
    assert "<Steps>" in run
    assert 5 >= len(re.findall(r"^\d+\. \*\*", run, re.M)) >= 4
    assert "not exact output" not in text


def test_concepts_links_every_home():
    _, body = page("concepts")
    for link in CONCEPT_LINKS:
        assert f"]({link})" in body, link


def test_install_lists_every_harness():
    _, body = page("install")
    for h in ["agy plugin install", "codex plugin marketplace add", "/add-plugin superlite",
              "devin plugins install", "droid plugin install", "gemini extensions install",
              "copilot plugin install", "grok plugin install", "/plugins install",
              "pi install", "qwen extensions install", "hermes plugins install",
              "muse plugins approve", ".opencode/INSTALL.md"]:
        assert h in body, h
    assert "claude --plugin-dir" in body
    assert "claude-agent-map@superlite-dev" in body


def test_commands_table_has_every_command():
    _, body = page("commands")
    cmds = Path(__file__).resolve().parents[2] / "commands"
    for p in sorted(cmds.glob("*.md")):
        assert f"`/superlite:{p.stem}" in body, p.stem
    assert "```mermaid" in body


def test_index_links_concepts():
    text = (DOCS / "index.mdx").read_text()
    assert 'href="/superlite/start/concepts/"' in text
    assert "```mermaid" in text
    assert "## What a run looks like" in text
