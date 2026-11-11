#!/usr/bin/env python3
"""Worktree-and-branch sessions: ensure, create, close, use, split, discard, status, sync.

    session.py ensure --slug S [--branch B] [--move yes] [--untracked stash|keep] [--root DIR]
    session.py create --slug S [--base REF] [--worktree] [--root DIR]
    session.py close --slug S [--keep-branch] [--untracked stash|keep] [--root DIR]
    session.py use --slug S [--continue] [--root DIR]
    session.py split --new S [--keep S] [--base REF] [--root DIR]
    session.py discard --slug S [--keep-branch] [--root DIR]
    session.py status [--root DIR] [--json]
    session.py untracked [ask|stash|keep] [--root DIR]
    session.py branch-name [status|worktree|superlite|PATTERN]   (branch for Claude-created worktrees; PATTERN holds {slug})
    session.py reconcile [--apply] [--root DIR]   (report drift from git; --apply rewrites sessions.json only)
    session.py sync ...       (fast-forward only; see session_sync.py)
  (a park asks first: exit 4 with an ASK line and an `ask:` line holding the question to relay)

The main checkout is the host of the active session; other sessions get a worktree under
`.superlite/worktrees/sessions/<slug>`. `use` parks the active session's run files into
`session-store/<slug>/`, stashes dirty project files, switches branch, restores. If the branch is
already checked out elsewhere, print REDIRECT (exit 3). The registry lives in the main checkout
whichever worktree a command runs from. This module is the entry and re-exports the helpers other
scripts import; the work lives in session_registry (registry, git helpers) and session_ensure
(ensure, create, close); session_switch holds use, discard, split and status. Stdlib only.
"""
import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import host_harness as harness  # noqa: E402
import session_registry as R  # noqa: E402
from session_ensure import (  # noqa: E402,F401
    cmd_close, cmd_create, cmd_ensure, cmd_untracked, enter_session, guarded, park_active, split_move,
    start_in_host, untracked_gate)
from session_switch import cmd_discard, cmd_split, cmd_status, cmd_use, continue_apply  # noqa: E402
from session_registry import (  # noqa: E402,F401
    EXIT_REDIRECT, FILE, ORCH, RESERVED, SLUG, STASH_PREFIX, STORE, GitError, base_now, base_row, base_sha_of,
    behind_base, branch_exists, branch_for, branch_owner, clear_run_state, current_branch, default_base,
    drop_stashes, find_row, free_slug, git, hide_superlite, hub_root, in_progress, is_dirty, is_open,
    mark_run, owned_worktree, print_redirect, registry_lock, resolve_host, restore_stash, row_at, row_base,
    same, session_line, session_stashes, sessions_dir, sha_of, slug_from_branch, stash_apply, stash_msg,
    stash_push, start_dir, store_path, toplevel, unmerged, untracked_files, upsert_row, validate_slug)

read_reg = R.read
write_reg = R.write


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sp = ap.add_subparsers(dest="cmd", required=True)
    branch_help = "branch for a Claude-created worktree; saved as the preference"
    untracked = dict(choices=["stash", "keep"], help="park untracked files: stash with the session, or keep in place")

    e = sp.add_parser("ensure", help="idempotent: make sure this run has a session (main checkout is the host)")
    e.add_argument("--slug", required=True)
    e.add_argument("--root")
    e.add_argument("--branch", help="an existing branch the session works on instead of a new session branch")
    e.add_argument("--move", choices=["yes"], help="confirm moving the open session out of the main checkout")
    e.add_argument("--untracked", **untracked)
    e.add_argument("--branch-name", dest="branch_name", default=None, metavar="worktree|superlite|PATTERN", help=branch_help)

    c = sp.add_parser("create", help="create a session branch (main checkout, or --worktree parallel)")
    c.add_argument("--slug", required=True)
    c.add_argument("--base")
    c.add_argument("--worktree", action="store_true", help="parallel: dedicated worktree + new branch")
    c.add_argument("--untracked", choices=["stash", "keep"])
    c.add_argument("--root")
    c.add_argument("--branch-name", dest="branch_name", default=None, metavar="worktree|superlite|PATTERN", help=branch_help)

    k = sp.add_parser("close", help="end of flow: mark finished, park/switch the host back, remove an owned worktree")
    k.add_argument("--slug", required=True)
    k.add_argument("--keep-branch", action="store_true")
    k.add_argument("--untracked", choices=["stash", "keep"])
    k.add_argument("--root")

    u = sp.add_parser("use", help="switch active session (park/stash) or REDIRECT if branch is live elsewhere")
    u.add_argument("--slug", required=True)
    u.add_argument("--continue", dest="continue_apply", action="store_true",
                   help="after resolving a restore conflict: drop the applied stash")
    u.add_argument("--untracked", choices=["stash", "keep"])
    u.add_argument("--root")

    s = sp.add_parser("split", help="parallel: keep the open run in a worktree and cut a new one")
    s.add_argument("--new", required=True)
    s.add_argument("--keep")
    s.add_argument("--base")
    s.add_argument("--root")

    d = sp.add_parser("discard", help="drop session WIP/store/stash/branch; remove its parallel worktree")
    d.add_argument("--slug", required=True)
    d.add_argument("--keep-branch", action="store_true")
    d.add_argument("--root")

    n = sp.add_parser("untracked", help="show or set how a park treats untracked files (ask|stash|keep)")
    n.add_argument("value", nargs="?", choices=["ask", "stash", "keep"])
    n.add_argument("--root")

    t = sp.add_parser("status", help="list registered sessions")
    t.add_argument("--root")
    t.add_argument("--json", action="store_true")

    r = sp.add_parser("reconcile", help="report drift between sessions.json and git; --apply fixes the registry only")
    r.add_argument("--apply", action="store_true", help="rewrite sessions.json (never changes git)")
    r.add_argument("--root")
    r.add_argument("--legacy-only", action="store_true", help=argparse.SUPPRESS)

    b = sp.add_parser("branch-name", help="branch-name preference for Claude-created worktrees")
    b.add_argument("value", nargs="?", default="status", help="status | worktree | superlite | PATTERN with {slug}")

    sp.add_parser("sync", help="fast-forward a session to the local base; see superlite-cli session sync --help")
    return ap


def _skip(what: str, e: Exception) -> None:
    print(f"session: {what} skipped: {type(e).__name__}: {e}", file=sys.stderr)


def precheck(a) -> None:
    """Report-only drift lines before ensure/use/status. Never blocks: any failure is one stderr line."""
    try:
        import session_reconcile as RC
        root = hub_root(start_dir(a))
        for d in RC.drift(root, read_reg(root)):
            print(RC.drift_line(d))
    except (GitError, ImportError, OSError, RuntimeError, ValueError, KeyError, TypeError) as e:  # a pre-check must never change the command's outcome
        _skip("drift check", e)


def cmd_status_inventory(a) -> int:
    rc = cmd_status(a)
    if getattr(a, "json", False):
        return rc
    try:
        import session_reconcile as RC
        root = hub_root(start_dir(a))
        for w in RC.inventory(root, read_reg(root)):
            where = w.get("branch") if w.get("branch") and not w.get("detached") else f"detached {(w.get('head') or '')[:7]}"
            print(f"session: worktree {w.get('kind')} {w.get('owner')} {w.get('path')} {where}")
    except (GitError, ImportError, OSError, RuntimeError, ValueError, KeyError, TypeError) as e:  # inventory is extra output
        _skip("worktree inventory", e)
    return rc


def cmd_reconcile(a) -> int:
    import session_reconcile as RC
    root = hub_root(start_dir(a))
    upgrades = getattr(RC, "upgrade_lines", lambda _: [])(root)
    for line in upgrades:
        print(line)
    if a.legacy_only:
        return 0
    if not a.apply:
        reg = read_reg(root)
        found = RC.drift(root, reg)
        for d in found:
            print(RC.drift_line(d))
        if not found:
            print(f"session: reconcile ok (0 drift, {len(RC.inventory(root, reg))} worktrees)")
        else:
            print(f"session: reconcile {len(found)} drift (report only; rerun with --apply)")
        return 0
    with registry_lock(root):
        new_reg, found = RC.apply(root, read_reg(root))
        write_reg(root, new_reg)
    for d in found:
        print(RC.drift_line(d))
    print(f"session: reconcile applied {len(found)} {'fix' if len(found) == 1 else 'fixes'}, "
          f"{len(new_reg.get('worktrees') or [])} worktrees recorded")
    return 0


def cmd_branch_name(a) -> int:
    if a.value == "status":
        pref = harness.branch_pref()
        print(f"session: branch-name {pref} (preferences)" if pref else "session: branch-name unset")
        return 0
    try:
        harness.set_branch_pref(a.value)
    except ValueError as e:
        print(f"session: branch-name {e}", file=sys.stderr)
        return 2
    print(f"session: branch-name {a.value} (saved)")
    return 0


COMMANDS = {"ensure": cmd_ensure, "create": cmd_create, "close": cmd_close, "use": cmd_use, "split": cmd_split,
            "discard": cmd_discard, "status": cmd_status_inventory, "untracked": cmd_untracked,
            "reconcile": cmd_reconcile, "branch-name": cmd_branch_name}
PRECHECKED = ("ensure", "use", "status")
ENTERING = ("ensure", "create", "use")


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] == "sync":  # the remaining argv belongs to session_sync
        return _sync(argv[1:])
    a = build_parser().parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    if a.cmd in PRECHECKED and not getattr(a, "json", False):
        precheck(a)
    try:
        if a.cmd in ENTERING and not getattr(a, "json", False) and harness.under_claude_code():
            return _with_enter_line(a)
        return COMMANDS[a.cmd](a)
    except GitError as e:  # entry point: report any git failure once, as exit 1
        print(f"session: {e}", file=sys.stderr)
        return 1


class _Tee:
    def __init__(self, out):
        self.out, self.text = out, []

    def write(self, s):
        self.text.append(s)
        return self.out.write(s)

    def __getattr__(self, name):
        return getattr(self.out, name)


def _with_enter_line(a) -> int:
    """Run the command, then (under Claude Code) point at the worktree its SESSION/REDIRECT line named."""
    real = sys.stdout
    sys.stdout = tee = _Tee(real)
    try:
        rc = COMMANDS[a.cmd](a)
    finally:
        sys.stdout = real
    m = re.search(r"^(?:(?:SESSION|REDIRECT) worktree=(.+) slug=|session: created parallel \S+ -> (.+) on \S+$)",
                  "".join(tee.text), re.M)  # create --worktree prints no SESSION line, only "created parallel"
    line = harness.enter_line(Path(m.group(1) or m.group(2)), Path.cwd()) if m else None
    if line:
        print(line)
    return rc


def _sync(rest: list) -> int:
    try:
        import session_sync
    except ImportError:
        print("session: sync unavailable", file=sys.stderr)
        return 2
    try:
        return session_sync.main(rest)
    except GitError as e:
        print(f"session: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
