"""Host harness facts: are we running under Claude Code, and the line that tells the agent to enter a worktree."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import agent_specs  # noqa: E402
import ident  # noqa: E402

PREF_KEY = "worktree_branch"
_BAD = re.compile(r"[\x00-\x20\x7f~^:?*\[\\]|@\{|\.\.|//")


def under_claude_code(env: dict | None = None) -> bool:
    """Claude Code sets CLAUDECODE=1 in every process it spawns (there is no worktree-specific variable)."""
    return (os.environ if env is None else env).get("CLAUDECODE") == "1"


def enter_line(path: Path, cwd: Path) -> str | None:
    """`session: enter EnterWorktree path=<path>` when path is a worktree other than cwd, else None."""
    if ident.same_path(Path(path), Path(cwd)):
        return None
    return f"session: enter EnterWorktree path={Path(path).as_posix()}"


def valid_branch(name: str) -> bool:
    """Stdlib equivalent of `git check-ref-format --branch` for plain names (no `-` start, no `@`)."""
    if not name or name == "@" or name.startswith(("-", "/")) or name.endswith(("/", ".")) or _BAD.search(name):
        return False
    return all(p and not p.startswith(".") and not p.endswith(".lock") for p in name.split("/"))


def branch_pref() -> str | None:
    """The saved worktree branch-name preference (`worktree`, `superlite` or a pattern), None if never answered."""
    v = agent_specs._read(agent_specs.preferences_path()).get(PREF_KEY)
    return v if isinstance(v, str) and v else None


def set_branch_pref(value: str) -> None:
    """Save the preference in preferences.json, keeping every other key. ValueError on a bad pattern."""
    if value not in ("worktree", "superlite"):
        session_branch(value, "a", "worktree-a")  # validates
    path = agent_specs.preferences_path()
    data = agent_specs._read(path)
    data[PREF_KEY] = value
    agent_specs._write(path, data)


def session_branch(pref: str, slug: str, harness_branch: str) -> str:
    """Branch a Claude-created worktree should carry: harness name, superlite/sessions/<slug>, or a {slug} pattern."""
    if pref == "worktree":
        return harness_branch
    if pref == "superlite":
        return f"superlite/sessions/{slug}"
    if "{slug}" not in pref:
        raise ValueError(f"branch-name pattern needs {{slug}}: {pref!r}")
    name = pref.replace("{slug}", slug)
    if not valid_branch(name):
        raise ValueError(f"not a valid branch name: {name!r}")
    return name
