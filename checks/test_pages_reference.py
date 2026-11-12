import re
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parent.parent / "src" / "content" / "docs"
MAIN = Path(__file__).resolve().parent.parent.parent
PATHS = [
    "reference/skills.mdx",
    "reference/agents.mdx",
    "reference/hooks.mdx",
    "reference/gates.mdx",
    "reference/cli.mdx",
    "reference/config.mdx",
    "reference/language.mdx",
    "reference/harnesses.mdx",
    "results/benchmark.mdx",
]
MERMAID = ("flowchart", "sequenceDiagram", "stateDiagram-v2", "gitGraph")


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


def _tldr(body):
    lines = [l for l in body.strip("\n").split("\n") if not l.startswith("import ")]
    lines = [l for l in lines if l.strip()]
    assert lines[0] == ":::tip[TL;DR]", "TL;DR aside must come first"
    end = lines.index(":::", 1)
    return " ".join(lines[1:end]), "\n".join(lines[end + 1:])


@pytest.mark.parametrize("rel", PATHS)
def test_reference_pages(rel):
    front, body = _parts(rel)
    assert re.search(r"^title: \S", front, re.M)
    assert re.search(r"^description: \S", front, re.M)
    assert "order:" in front
    assert _prose_words(body) <= 900


@pytest.mark.parametrize("rel", PATHS)
def test_tldr_diagram_and_example(rel):
    _, body = _parts(rel)
    tldr, rest = _tldr(body)
    assert len(tldr.split()) <= 60
    blocks = re.findall(r"```mermaid\n(.*?)```", body, re.S)
    assert blocks, "one mermaid diagram expected"
    for b in blocks:
        assert b.strip().startswith(MERMAID)
    assert re.search(r"^## Example", rest, re.M)
    # the diagram sits before the first section heading
    assert rest.find("```mermaid") < rest.find("\n## ")


@pytest.mark.parametrize("rel", PATHS)
def test_table_cells_are_one_sentence(rel):
    _, body = _parts(rel)
    for line in body.split("\n"):
        if line.startswith("|") and not line.startswith("|--"):
            for cell in line.strip("|").split("|"):
                assert not re.search(r"\.\s+[A-Z]", cell), f"{rel}: two sentences in {cell!r}"


def test_skill_and_agent_tables_match_repo():
    for rel, folder, pat in (
        ("reference/skills.mdx", "skills", None),
        ("reference/agents.mdx", "agents", ".md"),
    ):
        _, body = _parts(rel)
        names = set(re.findall(r"^\| `([a-z0-9-]+)` \|", body, re.M))
        if pat:
            real = {p.stem for p in (MAIN / folder).glob("*.md")}
        else:
            real = {p.name for p in (MAIN / folder).iterdir() if p.is_dir() and p.name != "__pycache__"}
        assert names == real, (rel, names ^ real)


def test_benchmark_numbers_from_the_report():
    _, body = _parts("results/benchmark.mdx")
    for figure in ("34.4 min", "8.8 min", "$8.15", "$3.28", "89 / 89", "16.1 min", "177 / 177"):
        assert figure in body
    assert "one run per arm" in body.lower()


@pytest.mark.parametrize("rel", PATHS)
def test_example_is_a_walkthrough(rel):
    _, body = _parts(rel)
    ex = body.split("\n## Example", 1)[1]
    assert "<Steps>" in ex, "walkthrough steps expected"
    assert "What you end up with" in ex
    assert re.search(r"!\[[^\]]*\]\([^)]*assets/screens/[^)]*\.png\)", ex) or 'title="' in ex, "a screen expected"


def test_config_lists_every_overlay_key():
    _, body = _parts("reference/config.mdx")
    for key in ("cap", "gitignore", "hosts", "testprobe", "models", "compact_tokens"):
        assert f"| `{key}` |" in body
