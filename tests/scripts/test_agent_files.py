"""agents/*.md frontmatter stays flat `key: value` lines. A nested block (an `experimental:` map with an indented `cacheTtl`) made
Claude Code ignore the file's `model:`: implementer-haiku ran on Sonnet in every run until 2026-10-21, while the same file without
the block ran on Haiku."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
AGENTS = sorted((ROOT / "agents").glob("*.md"))


def frontmatter(p: Path) -> list[str]:
    text = p.read_text(encoding="utf-8")
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert m, f"{p.name}: no frontmatter"
    return m.group(1).splitlines()


@pytest.mark.parametrize("p", AGENTS, ids=lambda p: p.name)
def test_frontmatter_is_flat_so_model_is_honoured(p):
    lines = frontmatter(p)
    assert all(re.match(r"^[a-z][\w-]*: \S", ln) for ln in lines), [ln for ln in lines if not re.match(r"^[a-z][\w-]*: \S", ln)]


@pytest.mark.parametrize("p", AGENTS, ids=lambda p: p.name)
def test_every_agent_names_its_model(p):
    keys = dict(ln.split(": ", 1) for ln in frontmatter(p))
    assert keys.get("model") in ("haiku", "sonnet", "opus") and keys.get("name") == p.stem
