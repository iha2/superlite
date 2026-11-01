#!/usr/bin/env python3
"""Fast-forward a session branch to local main: session_sync.py --slug S [--untracked stash|keep] [--later] [--root DIR]

Exit 0 ok, 2 error (including `session: needs a merge commit; not synced`, branch untouched), 4 ASK untracked.
Local only: never fetch, push, rebase, force or make a merge commit. Stdlib only.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import session  # noqa: E402
import session_registry as R  # noqa: E402
from session import git  # noqa: E402


def sha(cwd: Path, ref: str) -> str:
    return git("rev-parse", "--verify", "-q", ref + "^{commit}", cwd=cwd).stdout.strip()


def save(root: Path, row: dict) -> None:
    """Write the changed row back through the registry's locked update."""
    def apply(reg):
        target = R.find_row(reg, row["slug"])
        target.clear()
        target.update(row)
    R.update(root, apply)


def checkout_of(root: Path, reg: dict, row: dict) -> Path:
    wt = row.get("worktree")
    if wt and Path(wt).is_dir():
        return Path(wt)
    return session.resolve_host(root, reg)


def tracked_dirty(cwd: Path) -> bool:  # O(tree)
    return bool(git("status", "--porcelain", "--untracked-files=no", cwd=cwd).stdout.strip())


def finish(root: Path, row: dict, cwd: Path, target: str, stash: str | None) -> int:  # O(diff)
    """Record base_sha, clear sync, restore the stash. Returns 0, or 2 when the stash would not re-apply (kept)."""
    row["base_sha"] = target
    row.pop("sync", None)
    row.pop("sync_target", None)
    row.pop("sync_stash", None)
    ok = session.restore_stash(cwd, stash)
    if not ok:
        row["sync_stash"] = stash
    save(root, row)
    if not ok:
        print(f"session: sync done but the stashed work did not re-apply cleanly; it is kept in git stash ({stash[:12]})",
              file=sys.stderr)
    return 0 if ok else 2


def do_later(root: Path, row: dict, base: str) -> int:
    row["sync_offered"] = sha(root, base)
    save(root, row)
    print(f"session: {row['slug']} sync offer dismissed until main moves again")
    return 0


def do_sync(root: Path, reg: dict, row: dict, cwd: Path, base: str, mode: str | None) -> int:  # O(tree + diff)
    slug = row["slug"]
    if session.in_progress(cwd):
        print(f"session: a merge or rebase is already in progress in {cwd.as_posix()}; finish it first",
              file=sys.stderr)
        return 2
    if session.current_branch(cwd) != row["branch"]:
        print(f"session: {cwd.as_posix()} is not on {row['branch']}; run sync from the session's checkout",
              file=sys.stderr)
        return 2
    target = sha(root, base)
    if git("merge-base", "--is-ancestor", target, "HEAD", cwd=cwd, check=False).returncode == 0:
        row["base_sha"] = target
        row.pop("sync", None)
        save(root, row)
        print(f"session: {slug} already up to date with main")
        return 0
    if git("merge-base", "--is-ancestor", "HEAD", target, cwd=cwd, check=False).returncode != 0:
        print("session: needs a merge commit; not synced", file=sys.stderr)
        return 2
    rc, note, keep = session.untracked_gate(cwd, slug, mode or reg.get("untracked"), base)
    if rc is not None:
        return rc
    if note:
        print(note)
    stash = None
    merged = False
    try:
        if tracked_dirty(cwd) or (not keep and session.untracked_files(cwd)):
            stash = session.stash_push(cwd, f"{session.STASH_PREFIX}sync:{slug}", untracked=not keep)
        r = git("merge", "--ff-only", target, cwd=cwd, check=False)
        if r.returncode != 0:
            print(f"session: merge failed: {(r.stderr or r.stdout).strip()}", file=sys.stderr)
            return 2
        merged = True
    finally:
        if not merged:
            session.restore_stash(cwd, stash)
    rc = finish(root, row, cwd, target, stash)
    print(f"session: {slug} synced with main (fast-forward)")
    return rc


def main(argv: list[str] | None = None) -> int:  # O(tree + diff)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--slug", required=True)
    ap.add_argument("--root")
    ap.add_argument("--untracked", choices=["stash", "keep"])
    ap.add_argument("--later", action="store_true")
    a = ap.parse_args(argv)
    try:
        root = session.hub_root(Path(a.root) if a.root else Path.cwd())
        with session.registry_lock(root):
            reg = R.read(root)
            row = session.find_row(reg, a.slug)
            if row is None:
                print(f"session: unknown session {a.slug}", file=sys.stderr)
                return 2
            base = session.row_base(root, reg, row)  # the base this session was cut from
            cwd = checkout_of(root, reg, row)
            if a.later:
                return do_later(root, row, base)
            if not row.get("base_sha"):
                row["base_sha"] = session.base_sha_of(root, reg, row)
            return do_sync(root, reg, row, cwd, base, a.untracked)
    except session.GitError as e:
        print(f"session: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
