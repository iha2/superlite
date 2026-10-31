#!/usr/bin/env python3
"""Bring local main into a session branch: session_sync.py --slug S [--untracked stash|keep] [--continue|--abort|--later] [--root DIR]

Exit 0 ok, 2 error, 4 ASK untracked, 5 CONFLICT (merge left in progress, row sync="conflict").
Local only: never fetch, push, rebase or force. Stdlib only.
"""
from __future__ import annotations

import argparse  # O(1)
import sys  # O(1)
from pathlib import Path  # O(1)

HERE = Path(__file__).resolve().parent  # O(1)
if str(HERE) not in sys.path:  # O(1)
    sys.path.insert(0, str(HERE))  # O(1)

import session  # noqa: E402  | O(1)
from session import git  # noqa: E402  | O(1)


def sha(cwd: Path, ref: str) -> str:  # O(1)
    return git("rev-parse", "--verify", "-q", ref + "^{commit}", cwd=cwd).stdout.strip()  # O(1)


def base_sha(root: Path, reg: dict, row: dict) -> str:  # O(1)
    """session.base_sha_of when T1 provides it, else the merge base of the branch and the registry base."""
    fn = getattr(session, "base_sha_of", None)  # O(1)
    if fn is not None:  # O(1)
        return fn(root, reg, row)  # O(1)
    base = reg.get("base") or session.default_base(root)  # O(1)
    return git("merge-base", row["branch"], base, cwd=root).stdout.strip()  # O(1)


def checkout_of(root: Path, reg: dict, row: dict) -> Path:  # O(1)
    wt = row.get("worktree")  # O(1)
    if wt and Path(wt).is_dir():  # O(1)
        return Path(wt)  # O(1)
    return session.resolve_host(root, reg)  # O(1)


def tracked_dirty(cwd: Path) -> bool:  # O(tree)
    return bool(git("status", "--porcelain", "--untracked-files=no", cwd=cwd).stdout.strip())  # O(tree)


def merging(cwd: Path) -> bool:  # O(1)
    """True only for a real merge in progress (MERGE_HEAD), not a rebase."""
    gd = Path(git("rev-parse", "--git-dir", cwd=cwd).stdout.strip())  # O(1)
    if not gd.is_absolute():  # O(1)
        gd = (cwd / gd).resolve()  # O(1)
    return (gd / "MERGE_HEAD").exists()  # O(1)


def finish(root: Path, reg: dict, row: dict, cwd: Path, target: str, stash: str | None) -> int:  # O(diff)
    """Record base_sha, clear sync, restore the stash. Returns 0, or 2 when the stash would not re-apply (kept)."""
    row["base_sha"] = target  # O(1)
    row.pop("sync", None)  # O(1)
    row.pop("sync_target", None)  # O(1)
    row.pop("sync_stash", None)  # O(1)
    ok = session.restore_stash(cwd, stash)  # O(diff)
    if not ok:  # O(1)
        row["sync_stash"] = stash  # O(1)
    session.write_reg(root, reg)  # O(N)
    if not ok:  # O(1)
        print(f"session: merge done but the stashed work did not re-apply cleanly; it is kept in git stash ({stash[:12]})",
              file=sys.stderr)  # O(1)
    return 0 if ok else 2  # O(1)


def do_later(root: Path, reg: dict, row: dict, base: str) -> int:  # O(1)
    row["sync_offered"] = sha(root, base)  # O(1)
    session.write_reg(root, reg)  # O(N)
    print(f"session: {row['slug']} sync offer dismissed until main moves again")  # O(1)
    return 0  # O(1)


def do_abort(root: Path, reg: dict, row: dict, cwd: Path) -> int:  # O(diff)
    if not merging(cwd):  # O(1)
        print("session: no merge in progress; nothing to abort", file=sys.stderr)  # O(1)
        return 2  # O(1)
    if git("merge", "--abort", cwd=cwd, check=False).returncode != 0 or merging(cwd):  # O(tree)
        print("session: git merge --abort failed; nothing changed", file=sys.stderr)  # O(1)
        return 2  # O(1)
    stash = row.pop("sync_stash", None)  # O(1)
    row.pop("sync_target", None)  # O(1)
    row["sync"] = "needs-sync"  # O(1)
    ok = session.restore_stash(cwd, stash)  # O(diff)
    if not ok:  # O(1)
        row["sync_stash"] = stash  # O(1)
    session.write_reg(root, reg)  # O(N)
    print(f"session: {row['slug']} merge aborted; branch restored, needs-sync")  # O(1)
    if not ok:  # O(1)
        print("session: stashed work did not re-apply; it is kept in git stash", file=sys.stderr)  # O(1)
    return 0 if ok else 2  # O(1)


def do_continue(root: Path, reg: dict, row: dict, cwd: Path) -> int:  # O(diff)
    if not merging(cwd):  # O(1)
        print("session: no merge in progress; nothing to continue", file=sys.stderr)  # O(1)
        return 2  # O(1)
    left = session.unmerged(cwd)  # O(tree)
    if left:  # O(1)
        print(f"session: unresolved conflicts remain: {' '.join(left)}", file=sys.stderr)  # O(1)
        return 2  # O(1)
    if git("commit", "--no-edit", cwd=cwd, check=False).returncode != 0:  # O(tree)
        print("session: could not commit the merge; fix and rerun --continue, or --abort", file=sys.stderr)  # O(1)
        return 2  # O(1)
    target = row.get("sync_target") or sha(root, reg.get("base") or session.default_base(root))  # O(1)
    rc = finish(root, reg, row, cwd, target, row.get("sync_stash"))  # O(diff)
    if rc == 0:  # O(1)
        print(f"session: {row['slug']} synced with main (merge)")  # O(1)
    return rc  # O(1)


def do_sync(root: Path, reg: dict, row: dict, cwd: Path, base: str, mode: str | None) -> int:  # O(tree + diff)
    slug = row["slug"]  # O(1)
    if session.in_progress(cwd):  # O(1)
        print(f"session: a merge or rebase is already in progress in {cwd.as_posix()}; "
              f"finish it, or use --continue / --abort", file=sys.stderr)  # O(1)
        return 2  # O(1)
    if session.current_branch(cwd) != row["branch"]:  # O(1)
        print(f"session: {cwd.as_posix()} is not on {row['branch']}; run sync from the session's checkout",
              file=sys.stderr)  # O(1)
        return 2  # O(1)
    target = sha(root, base)  # O(1)
    if git("merge-base", "--is-ancestor", target, "HEAD", cwd=cwd, check=False).returncode == 0:  # O(1)
        row["base_sha"] = target  # O(1)
        row.pop("sync", None)  # O(1)
        session.write_reg(root, reg)  # O(N)
        print(f"session: {slug} already up to date with main")  # O(1)
        return 0  # O(1)
    rc, note, keep = session.untracked_gate(cwd, slug, mode or reg.get("untracked"), base)  # O(tree)
    if rc is not None:  # O(1)
        return rc  # O(1)
    if note:  # O(1)
        print(note)  # O(1)
    stash = None  # O(1)
    try:  # O(1)
        if tracked_dirty(cwd) or (not keep and session.untracked_files(cwd)):  # O(tree)
            stash = session.stash_push(cwd, f"{session.STASH_PREFIX}sync:{slug}", untracked=not keep)  # O(diff)
        ff = git("merge-base", "--is-ancestor", "HEAD", target, cwd=cwd, check=False).returncode == 0  # O(1)
        args = ["merge", "--ff-only", target] if ff else ["merge", "--no-edit", target]  # O(1)
        r = git(*args, cwd=cwd, check=False)  # O(tree)
        if r.returncode != 0:  # O(1)
            left = session.unmerged(cwd)  # O(tree)
            if not left:  # O(1)
                if session.in_progress(cwd):  # O(1)
                    git("merge", "--abort", cwd=cwd, check=False)  # O(tree)
                if not session.restore_stash(cwd, stash):  # O(diff)
                    print(f"session: stashed work did not re-apply; it is kept in git stash ({(stash or '')[:12]})",
                          file=sys.stderr)  # O(1)
                print(f"session: merge failed: {(r.stderr or r.stdout).strip()}", file=sys.stderr)  # O(1)
                return 2  # O(1)
            row.update(sync="conflict", sync_stash=stash, sync_target=target)  # O(1)
            session.write_reg(root, reg)  # O(N)
            print(f"CONFLICT {slug}: {' '.join(left)}")  # O(1)
            print(f"session: resolve and git add them, then sl session sync --slug {slug} --continue "
                  f"(or --abort)")  # O(1)
            return 5  # O(1)
    except BaseException:  # O(1)
        if session.in_progress(cwd):  # O(1)
            git("merge", "--abort", cwd=cwd, check=False)  # O(tree)
        session.restore_stash(cwd, stash)  # O(diff)
        raise  # O(1)
    rc = finish(root, reg, row, cwd, target, stash)  # O(diff)
    print(f"session: {slug} synced with main ({'fast-forward' if ff else 'merge'})")  # O(1)
    return rc  # O(1)


def main(argv: list[str] | None = None) -> int:  # O(tree + diff)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])  # O(1)
    ap.add_argument("--slug", required=True)  # O(1)
    ap.add_argument("--root")  # O(1)
    ap.add_argument("--untracked", choices=["stash", "keep"])  # O(1)
    g = ap.add_mutually_exclusive_group()  # O(1)
    g.add_argument("--continue", dest="cont", action="store_true")  # O(1)
    g.add_argument("--abort", action="store_true")  # O(1)
    g.add_argument("--later", action="store_true")  # O(1)
    a = ap.parse_args(argv)  # O(1)
    try:  # O(1)
        root = session.hub_root(Path(a.root) if a.root else Path.cwd())  # O(1)
        with session.registry_lock(root):  # O(1) + wait
            reg = session.read_reg(root)  # O(file)
            row = session.find_row(reg, a.slug)  # O(N)
            if row is None:  # O(1)
                print(f"session: unknown session {a.slug}", file=sys.stderr)  # O(1)
                return 2  # O(1)
            base = reg.get("base") or session.default_base(root)  # O(1)
            cwd = checkout_of(root, reg, row)  # O(1)
            if a.later:  # O(1)
                return do_later(root, reg, row, base)  # O(1)
            if a.abort:  # O(1)
                return do_abort(root, reg, row, cwd)  # O(diff)
            if a.cont:  # O(1)
                return do_continue(root, reg, row, cwd)  # O(diff)
            if not row.get("base_sha"):  # O(1)
                row["base_sha"] = base_sha(root, reg, row)  # O(1)
            return do_sync(root, reg, row, cwd, base, a.untracked)  # O(tree + diff)
    except session.GitError as e:  # O(1)
        print(f"session: {e}", file=sys.stderr)  # O(1)
        return 2  # O(1)


if __name__ == "__main__":  # O(1)
    sys.exit(main())  # O(1)
