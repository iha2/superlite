"""Claude Code creates the session worktree, superlite adopts it. Stdlib only.

Under CLAUDECODE=1 a parallel session asks Claude to run `EnterWorktree name=<slug>` (the repo cuts it from the
hub's HEAD) when the hub is on the needed base; otherwise superlite cuts the worktree itself, as before.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import harness  # noqa: E402
import session_registry as R  # noqa: E402

CHOICES = "worktree | superlite | PATTERN"


def create_line(name: str, base: str) -> str:
    return f"session: create EnterWorktree name={name} base={base}"


def hub_on_base(root: Path, base: str) -> bool:
    """True when the hub's HEAD is the same revision as `base`: a worktree cut from HEAD starts on the base."""
    head = R.git("rev-parse", "--verify", "-q", "HEAD^{commit}", cwd=root, check=False).stdout.strip()
    want = R.git("rev-parse", "--verify", "-q", f"{base}^{{commit}}", cwd=root, check=False).stdout.strip()
    return bool(head) and head == want


def apply_branch(wt: Path, slug: str, pref: str) -> str:
    """Name the session branch in a Claude-created worktree; returns the branch it ends on.

    `worktree` keeps the harness branch; any other preference runs `git switch -c` (the harness branch stays,
    so ExitWorktree can still remove it). ValueError for a bad pattern; an existing target branch keeps the harness one.
    """
    cur = R.current_branch(wt)
    if pref == "worktree" or cur == "HEAD":
        return cur
    name = harness.session_branch(pref, slug, cur)
    if name == cur or R.branch_exists(wt, name):
        return cur
    R.git("switch", "-q", "-c", name, cwd=wt)
    return name


def adopt_branch(wt: Path, slug: str, branch: str | None) -> str | None:
    """On adopting a fresh Claude worktree (branch `worktree-*`), apply the saved preference; else leave it."""
    if not harness.under_claude_code() or not branch or not branch.startswith("worktree-"):
        return branch
    pref = harness.branch_pref()
    if not pref:
        return branch
    try:
        return apply_branch(wt, slug, pref)
    except ValueError as e:
        print(f"session: note branch-name preference unusable ({e}); kept {branch}")
        return branch


def _ask(slug: str) -> int:
    print(f"ASK branch-name slug={slug}: Claude creates the worktree on branch worktree-{slug}; "
          f"how should session branches be named?")
    print(f"ask: Name session branches how (worktree keeps worktree-{slug}; superlite uses superlite/sessions/{slug}; "
          f"a pattern holds {{slug}}, e.g. feat/{{slug}})? Options: worktree | superlite | pattern")
    print("on worktree: rerun with --branch-name worktree")
    print("on superlite: rerun with --branch-name superlite")
    print("on pattern: rerun with --branch-name PATTERN")
    return 4


def gate(root: Path, slug: str, base: str, a) -> int | None:
    """Before superlite cuts a parallel worktree under Claude Code: an exit code to stop with, or None to cut."""
    if not harness.under_claude_code():
        return None
    given = getattr(a, "branch_name", None)
    if given:
        try:
            harness.session_branch(given, slug, f"worktree-{slug}")
        except ValueError as e:
            print(f"session: bad --branch-name: {e}", file=sys.stderr)
            return 2
        harness.set_branch_pref(given)
    elif not harness.branch_pref():
        return _ask(slug)
    if hub_on_base(root, base):
        print(create_line(slug, base))
        return 0
    cur = R.current_branch(root)
    if cur == "HEAD":
        cur = R.git("rev-parse", "--short", "HEAD", cwd=root).stdout.strip()
    print(f"session: note hub on {cur}, not {base}; superlite cut the worktree")
    return None
