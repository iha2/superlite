"""The flow docs describe the prototype as a sandbox folder, not a branch or worktree."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ["commands/feature.md", "docs/feature-flow.md", "docs/tools.md", "docs/sessions.md",
        "skills/shipping/SKILL.md", "AGENTS.md", "CLAUDE.md", "assets/help.txt",
        ".cursor/rules/feature-worktrees.mdc", "skills/using-superlite/references/claude-code-tools.md"]
BANNED = [r"<topic>-proto", r"superlite/<topic>-proto", r"proto: create", r"--adopt", r"proto discard`? \(?`?--keep-branch",
          r"proto-pending\.json", r"proto start[^\n]*--(?:path|base)\b"]


def read(p):
    return (ROOT / p).read_text(encoding="utf-8")


def test_no_proto_branch_in_docs():
    for p in DOCS:
        for pat in BANNED:
            assert not re.search(pat, read(p)), (p, pat)


def test_feature_md_sandbox():
    step = next(ln for ln in read("commands/feature.md").splitlines() if ln.startswith("4. "))
    assert "`.superlite/sandboxes/<session>/<topic>/`" in step and "proto start" in step
    assert "run_tests --fast <sandbox>/tests" in step


def test_discard_and_close_documented():
    flow = read("docs/feature-flow.md")
    assert "archives its run files into `.superlite/runs/`" in flow and "`session close` discards an open sandbox" in flow
    assert "`.superlite/sandboxes/<session>/<topic>/`" in read("docs/sessions.md")


def test_claude_md_keeps_session_enter_line():
    t = read("CLAUDE.md")
    assert "`session: create EnterWorktree name=<n>`" in t and "`session: enter EnterWorktree path=<abs>`" in t
    assert "proto: create" not in t
