import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import check_pages  # noqa: E402

REPO = str(check_pages.resolve_main())
needs_main = pytest.mark.skipif(
    not check_pages.is_main(REPO),
    reason=f"no main checkout at {REPO}: pass SUPERLITE_MAIN or run `git worktree add _main main`",
)
DOCS = HERE.parent / "src" / "content" / "docs"
PAGES = ["sessions", "lifecycle", "worktrees", "sandbox"]


def _text(name):
    return (DOCS / "sessions" / f"{name}.md").read_text()


@needs_main
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


def test_lifecycle_close_matches_session_close():
    text = _text("lifecycle")
    assert "archives the session and unregisters it" in text
    assert "refs/superlite/wip/<slug>" in text
    assert "Adopted worktree" in text and "--untracked keep" in text
    assert "shows `export` as `run:finished`" not in text
    assert "keeps the row and branch" not in text
    assert "close parks it and keeps the row" not in text
