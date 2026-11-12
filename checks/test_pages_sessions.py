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
    return (DOCS / "sessions" / f"{name}.mdx").read_text()


def _body(name):
    return _text(name).split("---", 2)[2].lstrip()


def _fenced(name, lang):
    return re.findall(rf"```{lang}\n(.*?)```", _text(name), re.S)


@needs_main
def test_pages_pass_check():
    problems = check_pages.check(str(DOCS), REPO)
    assert [p for p in problems if "sessions/" in p] == []


def test_pages_are_mdx():
    assert sorted(p.name for p in (DOCS / "sessions").iterdir()) == sorted(f"{n}.mdx" for n in PAGES)


def test_tldr_first_then_diagram_before_first_heading():
    for n in PAGES:
        body = re.sub(r"^import .*\n", "", _body(n), flags=re.M).lstrip()
        assert body.startswith(":::tip[TL;DR]"), n
        before = body.split("\n## ", 1)[0]
        assert "```mermaid" in before, n
        assert re.search(r"^## Example", body, re.M), n


def test_sessions_has_gitgraph_filetree_and_two_chats():
    t = _text("sessions")
    assert "gitGraph" in t and "superlite/sessions/export" in t and "superlite/sessions/login-fix" in t
    assert "<FileTree>" in t and "session-store/" in t and "sessions.json" in t
    assert "Chat 1" in t and "Chat 2" in t
    assert 'frame="terminal"' in t and "<Steps>" in t and "**What you end up with:**" in t


def test_examples_are_framed_walkthroughs():
    for n in PAGES:
        t = _text(n)
        assert re.search(r"^## Example: ", t, re.M), n
        assert "<Steps>" in t and 'frame="terminal"' in t, n
        assert "**What you end up with:**" in t, n
        assert "illustrative" not in t.lower(), n


def test_lifecycle_walks_one_real_session():
    t = _text("lifecycle")
    assert "proto-sandbox" in t and "PR #56" in t and "/superlite:merged 56" in t
    assert "pr-56.png" in t
    assert (HERE.parent / "src" / "assets" / "screens" / "sessions" / "pr-56.png").stat().st_size < 300_000


def test_worktrees_example_is_this_docs_session():
    t = _text("worktrees")
    assert "sessions-docs-site" in t and "docs-site" in t and "sessions-per-repo-learning-int" in t


def test_sandbox_example_is_the_per_repo_learning_prototype():
    t = _text("sandbox")
    assert "per-repo-learning" in t and "PR #60" in t and "tryout.py" in t


def test_lifecycle_state_diagram_and_three_steps():
    t = _text("lifecycle")
    assert "stateDiagram-v2" in t
    for s in ("superlite-cli finish", "superlite-cli session close", "/superlite:merged"):
        assert s in t
    assert "refs/superlite/wip/<slug>" in t and "git stash list" in t


def test_lifecycle_close_matches_session_close():
    text = _text("lifecycle")
    assert "Archives the session and unregisters it" in text
    assert "Worktree Claude Code made" in text and "--untracked keep" in text
    assert "ExitWorktree" in text
    assert "shows `export` as `run:finished`" not in text
    assert "close parks it and keeps the row" not in text


def test_worktree_rules():
    t = _text("worktrees")
    for s in (".claude/worktrees/", ".superlite/worktrees/sessions/", "ASK move", "--branch", "-int",
              "EnterWorktree name=", "EnterWorktree path=", "second desk"):
        assert s in t
    assert "<FileTree>" in t and "flowchart" in t
    assert "sandboxes" in t
    # the code cuts session worktrees under .claude/worktrees/sessions-<slug>, not .superlite/
    assert "sessions-export" in t


def test_sandbox_is_not_a_worktree():
    t = _text("sandbox")
    assert ".superlite/sandboxes/<session>/<topic>/" in t
    assert "not a worktree" in t
    assert "<FileTree>" in t and "| Sandbox | Worktree |" in t
    assert "proto discard" in t and "session close" in t


def test_mermaid_types_allowed():
    for n in PAGES:
        for block in _fenced(n, "mermaid"):
            assert check_pages._mermaid_type(block) in check_pages.MERMAID_TYPES, n
