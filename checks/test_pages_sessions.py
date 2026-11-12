import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import check_pages  # noqa: E402

REPO = str(HERE.parent.parent)
DOCS = HERE.parent / "src" / "content" / "docs"
PAGES = ["sessions", "lifecycle", "worktrees", "sandbox"]


def _text(name):
    return (DOCS / "sessions" / f"{name}.md").read_text()


def test_pages_pass_check():
    problems = check_pages.check(str(DOCS), REPO)
    assert [p for p in problems if "sessions/" in p] == []


def test_diagram_first_and_sections_in_order():
    for n in PAGES:
        body = _text(n).split("---", 2)[2].lstrip()
        assert body.startswith("```mermaid"), n
        heads = re.findall(r"^## .*$", body, re.M)
        assert heads == ["## What you do", "## What superlite does", "## Example"], n


def test_worktree_rules():
    t = _text("worktrees")
    for s in (".claude/worktrees/", ".superlite/worktrees/sessions/", "ASK move", "--branch", "Feature integration"):
        assert s in t
    assert "sandboxes" in t


def test_sandbox_is_not_a_worktree():
    t = _text("sandbox")
    assert ".superlite/sandboxes/<session>/<topic>/" in t
    assert "not a worktree" in t
