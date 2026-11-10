"""Docs send Claude Code agents into worktrees with EnterWorktree; git worktree add is a labelled fallback."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OWNED = ["AGENTS.md", "CLAUDE.md", "commands/feature.md", "commands/mvp.md", "commands/debug.md",
         "commands/small.md", "commands/start.md", "commands/review.md", "commands/research.md", "docs/sessions.md",
         "docs/feature-flow.md", "skills/shipping/SKILL.md", "skills/swarm/SKILL.md",
         ".cursor/rules/feature-worktrees.mdc", "skills/using-superlite/references/claude-code-tools.md"]


def text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_no_unlabelled_git_worktree_add():
    bad = []
    for rel in OWNED:
        heading = ""
        for n, line in enumerate(text(rel).splitlines(), 1):
            if line.startswith("#"):
                heading = line
            if re.search(r"git worktree (add|remove)", line):
                if "fallback" not in heading.lower() and "fallback" not in line.lower():
                    bad.append(f"{rel}:{n}")
    assert not bad, bad


def test_no_move_the_agent_root():
    bad = [r for r in OWNED if re.search(r"move the agent root", text(r), re.I)]
    assert not bad, bad


def test_agents_md_authorises_enter_worktree():
    for rel in ("AGENTS.md", "CLAUDE.md"):
        t = text(rel)
        assert "EnterWorktree" in t and "ExitWorktree" in t, rel
    assert "Worktrees under Claude Code" in text("AGENTS.md")


def test_claude_code_tools_maps_enter_and_exit():
    t = text("skills/using-superlite/references/claude-code-tools.md")
    assert "EnterWorktree" in t and "ExitWorktree" in t and "session: enter" in t


def test_lf_and_untouched_cap():
    for rel in OWNED:
        assert b"\r" not in (ROOT / rel).read_bytes(), rel
    assert len(text("skills/using-superlite/SKILL.md")) <= 2000


def test_commands_document_create_line():
    for rel in ("feature", "mvp", "debug", "small", "start", "research", "review"):
        t = text(f"commands/{rel}.md")
        assert "session: create EnterWorktree name=" in t, rel
        assert "session: enter EnterWorktree path=" in t, rel
        assert "ASK branch-name" in t and "--branch-name" in t, rel


def test_shipping_exitworktree_for_claude_created():
    t = text("skills/shipping/SKILL.md")
    assert "ExitWorktree" in t and "remove" in t and "Claude-created" in t
    assert "archive" in t and "session close" in t


def test_agents_md_authorises_name_and_path():
    t = text("AGENTS.md")
    assert "EnterWorktree name=" in t and "EnterWorktree path=" in t and "ExitWorktree" in t
    assert "--branch-name" in t and "session branch-name" in t and "baseRef" in t
    assert "EnterWorktree name=" in text("CLAUDE.md") and "EnterWorktree path=" in text("CLAUDE.md")
    assert "worktree.baseRef" in text("docs/sessions.md") and "next" in text("docs/sessions.md")
    assert "proto: create EnterWorktree" in text("docs/feature-flow.md")
    assert "EnterWorktree name=" in text("skills/using-superlite/references/claude-code-tools.md")
    assert "session: create" in text("skills/swarm/SKILL.md")
    assert len(text("skills/swarm/SKILL.md")) <= 12500
