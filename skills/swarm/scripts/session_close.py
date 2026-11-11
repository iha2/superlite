"""Session close: archive the session (WIP on a private ref, run files, manifest), then drop its row. Stdlib only.

Home A (hub session) and home C (owned worktree) archive and unregister inside a Journal, so a failure undoes
the steps newest first and the registry is written last. Home B (an adopted harness or user worktree) is never
touched: only its run files and row are archived.
"""
import copy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import session_archive as A  # noqa: E402
import run_archive  # noqa: E402
import session_wip as W  # noqa: E402
import state  # noqa: E402
from journal import Journal, JournalError  # noqa: E402
from session_registry import (  # noqa: E402
    branch_for, branch_owner, current_branch, drop_stashes, find_row, git, hub_root, in_progress, now_ms,
    owned_worktree, park_run_state, paths_text, read, registry_lock, restore_run_state, row_base, run_entries,
    same, session_stashes, set_run, start_dir, store_path, untracked_files, untracked_gate, validate_slug, write)

def cmd_close(a) -> int:  # O(tree)
    slug = validate_slug(a.slug)
    root = hub_root(start_dir(a))
    from session_ensure import guarded  # lazy: session_ensure re-exports this module's cmd_close
    with registry_lock(root):
        return guarded("close", lambda: _close(root, slug, a))


def _close(root: Path, slug: str, a) -> int:
    reg = read(root)
    row = find_row(reg, slug)
    if not row:
        print(f"session: no registered session {slug}", file=sys.stderr)
        return 2
    set_run(row, "finished")  # in memory until the registry is written
    host = root
    if reg.get("active") == slug and same(row.get("worktree"), host):
        return _close_host(root, reg, row, slug, host, a)
    if owned_worktree(root, row) and row.get("worktree") and Path(row["worktree"]).is_dir():
        return _close_owned(root, reg, row, slug, a)
    if not row.get("owned") and row.get("adopted_branch") and row.get("worktree") and not same(row["worktree"], root):
        return _close_adopted(root, reg, row, slug)
    return _close_stored(root, reg, row, slug)


def _discard_sandbox(live: Path, row: dict, root: Path, checkout: Path | None = None) -> None:
    """An open prototype sandbox goes first: its run is archived with the session, its files are dropped."""
    try:
        from proto_discard import discard_open  # lazy: the prototype modules are optional here
        res = discard_open(live, "session close", checkout=checkout or live.parent)
    except (ImportError, OSError, ValueError, KeyError, TypeError, IndexError) as e:
        print(f"session: could not discard the open sandbox: {e}", file=sys.stderr)
        return
    if res:
        updated = find_row(read(root), row["slug"])
        if updated is not None:
            row["sandboxes"] = copy.deepcopy(updated.get("sandboxes", []))
        print(f"session: discarded the open prototype sandbox ({res.get('status')})")


def _save_wip(cwd: Path, slug: str, keep_untracked: bool, j: Journal) -> str | None:
    """Park the dirty tree on the slug's private ref; undone through `j` by restoring it."""
    oid = W.save(cwd, slug, untracked=not keep_untracked)
    if oid:
        def undo():
            if not W.restore(cwd, slug):
                print(f"session: uncommitted work kept on {W.ref_for(slug)}; apply it by hand", file=sys.stderr)
        j.undo(undo, "wip")
    return oid


def _archive(root: Path, reg: dict, row: dict, slug: str, wip: str | None, j: Journal) -> None:
    """Manifest first (after the run files are parked into the same folder), then the row leaves the registry."""
    done = {k: v for k, v in row.items() if k not in ("stash_oid", "stash_message")}
    done.update(status="parked", has_stash=False, run="finished")
    j.step("write the archive manifest")
    path = A.write(root, done, W.ref_for(slug) if wip else None, now_ms())
    j.undo(lambda: path.unlink(missing_ok=True), "manifest")
    reg["sessions"] = [s for s in reg["sessions"] if s.get("slug") != slug]
    if reg.get("active") == slug:
        reg["active"] = None


def _close_host(root: Path, reg: dict, row: dict, slug: str, host: Path, a) -> int:
    if in_progress(host):
        print("session: merge/rebase in progress; finish or abort it before close", file=sys.stderr)
        return 2
    base = row_base(root, reg, row)  # back to the base this session was cut from
    owner = branch_owner(root, base) if base else None
    if owner and not same(owner, host):  # base is checked out elsewhere: refuse before any change
        print(f"session: {base} is checked out in {owner}; switch that worktree away from {base} "
              f"before closing {slug}; nothing changed", file=sys.stderr)
        return 2
    stop, note, keep = untracked_gate(host, slug, getattr(a, "untracked", None) or reg.get("untracked"), base)
    if stop is not None:
        return stop
    store, live = store_path(root, slug), state.run_dir(host)
    before = copy.deepcopy(reg)
    wip = None
    _discard_sandbox(live, row, root)
    try:
        with Journal() as j:
            j.step("save the work")
            wip = _save_wip(host, slug, keep, j)
            j.step("park the run files")
            park_run_state(live, store)
            j.undo(lambda: restore_run_state(store, live), "run files")
            if base and current_branch(host) != base:
                j.step(f"switch {host.as_posix()} to {base}")
                prev = current_branch(host)
                git("switch", "-q", base, cwd=host)
                j.undo(lambda: git("switch", "-q", prev, cwd=host, check=False), "switch back")
            _archive(root, reg, row, slug, wip, j)
            write(root, reg)
    except JournalError as e:  # everything is undone; the session stays active and the registry is untouched
        reg.clear()
        reg.update(before)
        print(f"session: could not close {slug}: {e}; {slug} stays active", file=sys.stderr)
        return 2
    if note:
        print(note)
    print(f"session: closed {slug}; archived to {store.as_posix()}" + (f" (wip {W.ref_for(slug)})" if wip else "")
          + f"; {host.as_posix()} is back on {base}")
    return 0


def _close_owned(root: Path, reg: dict, row: dict, slug: str, a) -> int:
    wt = Path(row["worktree"])
    if in_progress(wt):
        print("session: merge/rebase in progress; finish or abort it before close", file=sys.stderr)
        return 2
    mode = getattr(a, "untracked", None) or reg.get("untracked")
    files = untracked_files(wt)
    if files and mode == "keep":  # untracked files stay: leave the worktree exactly as it is
        row["status"] = "parallel"
        write(root, reg)
        print(f"session: kept {len(files)} untracked file(s) in place: {paths_text(files)}")
        print(f"session: closed {slug}; worktree {wt.as_posix()} left in place")
        return 0
    note = None
    if files:  # ask when unset; save otherwise
        stop, note, _ = untracked_gate(wt, slug, mode, row_base(root, reg, row))
        if stop is not None:
            return stop
    store, live = store_path(root, slug), state.run_dir(wt)
    had_run = any(p.name != run_archive.CURRENT_RUN for p in run_entries(live))
    before = read(root)  # as on disk: the undo must not persist the in-memory "finished" mark
    wip = None
    _discard_sandbox(live, row, root)
    try:
        with Journal() as j:
            j.step("save the work")
            wip = _save_wip(wt, slug, False, j)
            j.step("park the run files")
            park_run_state(live, store)
            j.undo(lambda: restore_run_state(store, live), "run files")
            _archive(root, reg, row, slug, wip, j)
            j.step("write the registry")
            write(root, reg)
            j.undo(lambda: write(root, before), "registry")
            j.step(f"remove {wt.as_posix()}")
            git("worktree", "remove", str(wt), cwd=root)  # clean now, so no --force
    except JournalError as e:  # put back; nothing is lost
        reg.clear()
        reg.update(before)
        print(f"session: could not remove {wt.as_posix()}: {e}; nothing changed", file=sys.stderr)
        return 2
    git("worktree", "prune", cwd=root, check=False)
    branch = row.get("branch") or branch_for(slug)
    if not a.keep_branch and not row.get("adopted_branch") and not wip and not had_run \
            and branch_owner(root, branch) is None:
        deleted = git("branch", "-d", branch, cwd=root, check=False)
        if deleted.returncode != 0:
            print(f"session: kept {branch}: {(deleted.stderr or deleted.stdout).strip()}", file=sys.stderr)
    print(f"session: closed {slug}; archived to {store.as_posix()}" + (f" (wip {W.ref_for(slug)})" if wip else "")
          + "; removed its worktree")
    if note:
        print(note)
    return 0


def _close_stored(root: Path, reg: dict, row: dict, slug: str) -> int:
    """A parked host session or an owned one whose worktree is gone: nothing live to park. Archive its store and
    saved work (stash moved to the private ref) and unregister it; the active checkout is never touched."""
    oid = row.get("stash_oid") if row.get("has_stash") else None
    if oid and git("cat-file", "-e", f"{oid}^{{commit}}", cwd=root, check=False).returncode != 0:
        oid = None  # the recorded stash is gone: nothing to carry
    refs = session_stashes(root, row) if oid else []
    before = copy.deepcopy(reg)
    _discard_sandbox(store_path(root, slug), row, root, Path(row.get("worktree") or root))
    try:
        with Journal() as j:
            if oid and not W.exists(root, slug):
                j.step("save the work on the private ref")
                git("update-ref", W.ref_for(slug), oid, cwd=root)
                j.undo(lambda: git("update-ref", "-d", W.ref_for(slug), cwd=root, check=False), "wip ref")
            _archive(root, reg, row, slug, W.ref_for(slug) if W.exists(root, slug) else None, j)
            write(root, reg)
    except JournalError as e:
        reg.clear()
        reg.update(before)
        print(f"session: could not close {slug}: {e}; nothing changed", file=sys.stderr)
        return 2
    if refs:
        drop_stashes(root, refs[:1])  # the work now lives on the ref; the shared stash list is cleared of it
    print(f"session: closed {slug}; archived to {store_path(root, slug).as_posix()}"
          + (f" (wip {W.ref_for(slug)})" if W.exists(root, slug) else ""))
    return 0


def _close_adopted(root: Path, reg: dict, row: dict, slug: str) -> int:
    """Home B: a worktree superlite did not cut. Archive the run files and drop the row; never touch the
    worktree, its dirty files or its branch, and keep no WIP ref."""
    store, live = store_path(root, slug), state.run_dir(Path(row["worktree"]))
    before = copy.deepcopy(reg)
    _discard_sandbox(live, row, root)
    try:
        with Journal() as j:
            j.step("park the run files")
            park_run_state(live, store)
            j.undo(lambda: restore_run_state(store, live), "run files")
            _archive(root, reg, row, slug, None, j)
            write(root, reg)
    except JournalError as e:
        reg.clear()
        reg.update(before)
        print(f"session: could not close {slug}: {e}; nothing changed", file=sys.stderr)
        return 2
    print(f"session: closed {slug}; archived to {store.as_posix()}")
    print(f"session: {slug} lives in a harness worktree; leave it with ExitWorktree")
    return 0
