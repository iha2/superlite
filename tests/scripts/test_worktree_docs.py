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
