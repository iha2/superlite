"""Session start and end: ensure, create, close, untracked. Stdlib only.

Every check (git repo, branch free, worktree path free, not mid-merge, untracked choice, move
confirmed) runs before the first change; the changing steps run in a Journal, so a failure undoes
them newest first and the registry is written last.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import run_archive  # noqa: E402
import session_archive as A  # noqa: E402
import session_registry as R  # noqa: E402
import session_wip as W  # noqa: E402
import claude_create  # noqa: E402
import host_harness as harness  # noqa: E402
import session_registration as registration  # noqa: E402
import state  # noqa: E402
from worktree_home import harness_worktree, session_dir  # noqa: E402,F401  (harness_worktree re-exported)
from journal import Journal, JournalError  # noqa: E402
from session_registry import (
    split_move, untracked_gate,  # noqa: E402
    EXIT_REDIRECT, GitError, base_now, base_row, branch_exists, branch_for, branch_owner, current_branch,
    default_base, drop_stashes, find_row, free_slug, git, hide_all, hub_root, in_progress, is_dirty,
    is_open, move_run_state, now_ms, owned_worktree, park_run_state, print_redirect, read, registry_lock,
    restore_run_state, row_at, row_base, run_entries, same, session_line, session_stashes, sessions_dir,
    set_run, stash_apply, stash_msg, stash_push, start_dir, store_path, toplevel, upsert_row, validate_slug,
    write)


def guarded(name: str, fn) -> int:
    """Run a command body; a failed Journal step has already been undone, so say so and exit 1."""
    try:
        return fn()
    except JournalError as e:
        print(f"session: {name} failed: {e}; rolling back (nothing changed)", file=sys.stderr)
        return 1


def _undo_stash(cwd: Path, oid: str):
    def undo():
        if not R.restore_stash(cwd, oid):
            print(f"session: uncommitted work kept in stash {oid}; apply it by hand", file=sys.stderr)
    return undo


def _restore_held(live: Path, held: Path, moved: list) -> None:
    for source, target in reversed(moved):
        if target.exists() or target.is_symlink():
            shutil.move(str(target), str(source))
    shutil.rmtree(held, ignore_errors=True)


# ---- park and enter

def park_active(root: Path, reg: dict, host: Path, slug: str, keep_untracked: bool = False,
                j: Journal | None = None, write_reg: bool = True) -> None:
    """Stash the active session's WIP and park its run files; undone through `j` when given."""
    row = find_row(reg, slug) or {"slug": slug, "branch": current_branch(host)}
    dirty = bool(git("status", "--porcelain", "--untracked-files=no", cwd=host, check=False).stdout.strip()) \
        if keep_untracked else is_dirty(host)
    oid = stash_push(host, stash_msg(slug), untracked=not keep_untracked) if dirty else None
    if oid and j:
        j.undo(_undo_stash(host, oid), "stash")
    store, live = store_path(root, slug), state.run_dir(host)
    park_run_state(live, store)
    if j:
        j.undo(lambda: restore_run_state(store, live), "run files")
    upsert_row(reg, {**row, "worktree": host.as_posix(), "store": store.as_posix(),
                     "stash_message": stash_msg(slug), "stash_oid": oid, "has_stash": bool(oid),
                     "status": "parked"})
    reg["active"] = None
    if write_reg:
        write(root, reg)


def enter_session(root: Path, reg: dict, host: Path, row: dict, j: Journal | None = None) -> int:  # O(S + diff)
    """Switch the host to row's branch, restore its run files and its stash; mark it active."""
    slug, branch = row["slug"], row["branch"]
    if not branch_exists(root, branch):
        git("branch", branch, row.get("base") or reg.get("base") or default_base(root), cwd=root)
    if current_branch(host) != branch:
        prev = git("rev-parse", "HEAD", cwd=host).stdout.strip()
        prev_branch = current_branch(host)
        git("switch", "-q", branch, cwd=host)
        if j:
            back = ["switch", "-q", prev_branch] if prev_branch != "HEAD" else ["switch", "-q", "--detach", prev]
            j.undo(lambda: git(*back, cwd=host, check=False), "switch back")
    store = store_path(root, slug)
    restore_run_state(store, state.run_dir(host))
    if j:  # restoring empties the store, so a later failure must park the run files again
        j.undo(lambda: park_run_state(state.run_dir(host), store), "run files")
    for s in reg["sessions"]:
        if s.get("slug") != slug and same(s.get("worktree"), host) and s.get("status") == "active":
            s["status"] = "parked"
    row = upsert_row(reg, {**row, "worktree": host.as_posix(), "store": store.as_posix(), "status": "active"})
    reg["active"] = slug
    reg["host_worktree"] = host.as_posix()
    if row.get("has_stash") and not _restore_session_stash(root, reg, host, row):
        return 2
    write(root, reg)
    print(f"session: active {slug} on {branch} in {host.as_posix()}")
    return 0


def _restore_session_stash(root: Path, reg: dict, host: Path, row: dict) -> bool:
    """Apply the session's recorded stash; False (registry written, message printed) on a conflict."""
    slug = row["slug"]
    refs = session_stashes(host, row)
    if not refs:
        print(f"session: warning: recorded stash for {slug} not found; nothing to restore", file=sys.stderr)
    elif not stash_apply(host, refs[0]):
        write(root, reg)
        print(f"session: stash apply conflict for {slug}; resolve the files (git add them), then "
              f"superlite-cli session use --slug {slug} --continue", file=sys.stderr)
        return False
    else:
        drop_stashes(host, refs[:1])
    row.update(has_stash=False, stash_oid=None)
    return True


def fresh_base(root: Path, reg: dict, host: Path, avoid: str | None = None) -> str | None:
    """With no active session the host's current branch is the base; None when the recorded base applies."""
    if reg.get("active"):
        return None
    cur = current_branch(host)
    if cur not in ("HEAD", "", avoid) and not cur.startswith("superlite/sessions/"):
        return cur
    rb = reg.get("base")
    if rb and branch_exists(root, rb):
        return None
    names = git("for-each-ref", "--format=%(refname:short)", "refs/heads", cwd=root, check=False).stdout.split()
    pool = [n for n in names if n != avoid and not n.startswith("superlite/sessions/")]
    return next((n for n in ("main", "master") if n in pool), pool[0] if pool else None)


# ---- ensure

def cmd_ensure(a) -> int:  # O(tree)
    if os.environ.get("SUPERLITE_NO_AUTO_SESSION") == "1":
        print("session: auto-start off")
        return 0
    want = validate_slug(a.slug)
    start = start_dir(a)
    if git("rev-parse", "--is-inside-work-tree", cwd=start, check=False).stdout.strip() != "true":
        print("session: no git repository here; continuing without a session")
        session_line(start, want, "-")
        return 0
    if git("rev-parse", "--verify", "-q", "HEAD", cwd=start, check=False).returncode != 0:
        print("session: repository has no commits yet; continuing without a session")
        session_line(start, want, "-")
        return 0
    here, root = toplevel(start), hub_root(start)
    with registry_lock(root):
        return guarded("ensure", lambda: _ensure(root, here, want, a))


def _say_archived(root: Path, dest: Path | None) -> None:
    if dest is not None:
        shown = dest.relative_to(root) if dest.is_relative_to(root) else dest
        print(f"run: archived leftovers to {shown.as_posix()}")


def _ensure(root: Path, here: Path, want: str, a) -> int:
    reg = read(root)
    host = root
    gone = None if getattr(a, "branch", None) else archived_row(root, reg, want)
    if gone and not same(here, host) and same(gone.get("worktree"), here):  # its own adopted worktree: in place
        with Journal() as j:
            return restore_archived(root, reg, host, gone, j)
    if gone:  # a closed session: bring it back
        from types import SimpleNamespace
        import session_switch
        return session_switch._use(root, want, SimpleNamespace(**{**vars(a), "continue_apply": False}))
    row = row_at(reg, here, host)
    exist = getattr(a, "branch", None)
    if exist:
        rc = _ensure_branch(root, reg, here, row, want, exist)
        if rc is not None:
            return rc
    if is_open(row) and (not exist or row.get("branch") == exist) and (
            row["slug"] == want or row.get("topic") == want or not same(here, host)):
        print(f"session: {row['slug']} already open here")
        session_line(row.get("worktree") or here, row["slug"],
                     row.get("branch") or ("HEAD" if row.get("detached") else branch_for(row["slug"])))
        return 0
    if row is None and not same(here, host) and not exist:
        return registration.register_linked(root, reg, here, want)
    active_row = find_row(reg, reg["active"]) if reg.get("active") else None
    slug = free_slug(root, reg, want)
    if slug != want:
        print(f"session: slug {want} taken; using {slug}")
    side = bool(exist) and not same(here, host)  # a linked checkout asking for a branch never takes the main checkout
    if is_open(active_row) or side:
        return _go_parallel(root, reg, host, want, slug, exist, active_row, side, a)
    cur = current_branch(host)
    base = fresh_base(root, reg, host, exist) or reg.get("base") or (cur if cur != "HEAD" else default_base(root))
    rc = start_in_host(root, reg, host, slug, base, getattr(a, "untracked", None) or reg.get("untracked"),
                       existing=exist, topic=want)
    if rc == 0:
        session_line(host, slug, exist or branch_for(slug))
    return rc


def _ensure_branch(root: Path, reg: dict, here: Path, row: dict | None, want: str, exist: str) -> int | None:
    """--branch: an existing branch to work on. A rc when it is settled here; None to carry on."""
    host = root
    if not branch_exists(root, exist):
        print(f"session: branch {exist} does not exist", file=sys.stderr)
        return 2
    owner = branch_owner(root, exist)
    if not owner:
        return None
    if not same(owner, here):
        return _redirect_to_owner(root, reg, host, owner, want, exist)
    if is_open(row):
        print(f"session: {row['slug']} already open here")
        session_line(row.get("worktree") or here, row["slug"], row.get("branch") or exist)
        return 0
    if row is not None and row.get("branch") == exist:  # its own finished branch: reopen, WIP stays
        set_run(row, "open")
        write(root, reg)
        print(f"session: reopened {row['slug']} on {exist}; its uncommitted work stays in place")
        session_line(here, row["slug"], exist)
        return 0
    if row is None if not same(here, host) else not reg.get("active"):
        slug = registration.register_in_place(root, reg, host, here, want, exist)
        session_line(here, slug, exist)
        return 0
    return None


def _redirect_to_owner(root: Path, reg: dict, host: Path, owner: str, want: str, exist: str) -> int:
    there = Path(owner)
    at_base = same(there, host) and exist == (reg.get("base") or default_base(root))  # an idle main checkout
    held = row_at(reg, there, host)
    if held is None and not at_base and (not same(there, host) or not reg.get("active")):
        slug = registration.register_in_place(root, reg, host, there, want, exist)  # a session before the redirect
        return print_redirect(owner, slug, exist)
    return print_redirect(owner, held["slug"] if held else want, exist)


def _move_gate(root: Path, reg: dict, host: Path, old_slug: str, wt_old: Path, base: str, a):
    """Every check before the open session leaves the main checkout: (rc, note, keep); rc None means go."""
    if getattr(a, "move", None) != "yes":
        print(f"ASK move slug={old_slug}: {old_slug} moves from the main checkout to {wt_old.as_posix()}; "
              f"its chat must be idle")
        print(f"ask: Move session {old_slug} out of the main checkout into {wt_old.as_posix()}? Its chat must "
              f"be idle. Options: yes | no")
        print("on yes: rerun with --move yes")
        print("on no: stop; nothing changed, the session stays in the main checkout")
        return 4, None, False
    rc, note, keep = untracked_gate(host, old_slug, getattr(a, "untracked", None) or reg.get("untracked"), base)
    if rc is not None:
        return rc, None, False
    if in_progress(host):
        print("session: merge/rebase in progress; finish it before ensure", file=sys.stderr)
        return 2, None, False
    if wt_old.exists():
        print(f"session: worktree path already exists ({wt_old})", file=sys.stderr)
        return 2, None, False
    owner = branch_owner(root, base)
    if owner and not same(owner, host):
        print(f"session: {base} is checked out in {owner}; switch that worktree away from {base} first; "
              f"nothing changed", file=sys.stderr)
        return 2, None, False
    return None, note, keep


def _go_parallel(root: Path, reg: dict, host: Path, want: str, slug: str, exist: str | None,
                 active_row: dict | None, side: bool, a) -> int:
    """The new session gets its own worktree; the open one first leaves the main checkout when it is in the way."""
    base = reg.get("base") or default_base(root)
    reg["base"] = base
    branch = exist or branch_for(slug)
    move = (not side and same(active_row.get("worktree"), host)
            and current_branch(host) not in ("HEAD", base))  # a session on the base branch stays: base lives here
    old_slug, old_branch = (active_row["slug"], current_branch(host)) if move else ("", "")
    wt_old, wt = session_dir(root, old_slug), session_dir(root, slug)
    rc = None if exist else claude_create.gate(root, slug, base, a)  # Claude cuts it when the hub is on the base
    if rc is not None:
        return rc
    note, keep = None, False
    if move:
        rc, note, keep = _move_gate(root, reg, host, old_slug, wt_old, base, a)
        if rc is not None:
            return rc
    if exist and branch_owner(root, exist):
        print(f"session: branch {exist} is checked out in {branch_owner(root, exist)}; nothing changed",
              file=sys.stderr)
        return 2
    hide_all(root)
    with Journal() as j:
        if move:
            j.step("move the open session out of the main checkout")
            split_move(root, state.run_dir(host), old_branch, base, old_branch, wt_old, j, keep_untracked=keep)
        j.step("create the worktree")
        wt.parent.mkdir(parents=True, exist_ok=True)
        if exist:
            git("worktree", "add", "-q", str(wt), exist, cwd=root)
            j.undo(lambda: git("worktree", "remove", "--force", str(wt), cwd=root, check=False), "worktree")
        else:
            git("worktree", "add", "-q", "-b", branch, str(wt), base, cwd=root)
            j.undo(lambda: (git("worktree", "remove", "--force", str(wt), cwd=root, check=False),
                            git("branch", "-D", branch, cwd=root, check=False)), "worktree and branch")
        state.run_dir(wt).mkdir(parents=True, exist_ok=True)
        j.step("write the registry")
        if move:
            R.base_sha_of(root, reg, active_row)
            upsert_row(reg, {**active_row, "worktree": wt_old.as_posix(), "store": store_path(root, old_slug).as_posix(),
                             "has_stash": False, "status": "parallel", "owned": True,
                             "moved_from": host.as_posix(), "moved_at": now_ms()})
            reg["active"] = None
        upsert_row(reg, {"slug": slug, "branch": branch, "worktree": wt.as_posix(),
                         "store": store_path(root, slug).as_posix(), "has_stash": False, "status": "parallel",
                         "owned": True, "run": "open", "created_at": now_ms(), "topic": want,
                         **base_row(root, reg, exist), **({"adopted_branch": True} if exist else {})})
        write(root, reg)
    if move:
        print(f"session: moved {old_slug} -> {wt_old.as_posix()}")
        if note:
            print(note)
    print(f"session: created parallel {slug} -> {wt.as_posix()} on {branch}")
    session_line(wt, slug, branch)
    return 0


def start_in_host(root: Path, reg: dict, host: Path, slug: str, base: str, mode: str | None = None,
                  existing: str | None = None, topic: str | None = None) -> int:
    """Park a finished active session (WIP stashed), else carry WIP along; cut the branch (or adopt `existing`) and enter it."""
    branch = existing or branch_for(slug)
    note, keep = None, False
    if reg.get("active"):
        stop, note, keep = untracked_gate(host, reg["active"], mode, existing or base)
        if stop is not None:
            return stop
    if in_progress(host):
        print("session: merge/rebase in progress; finish it before ensure", file=sys.stderr)
        return 2
    hide_all(root)
    reg["base"] = reg.get("base") or base  # the registry base is set once; each row keeps its own
    live = state.run_dir(host)
    held, archived = None, []
    row = {"slug": slug, "branch": branch, "worktree": host.as_posix(), "has_stash": False, "run": "open",
           "created_at": now_ms(), **({"topic": topic} if topic else {}), **base_row(root, {**reg, "base": base}, existing),
           **({"adopted_branch": True} if existing else {})}
    try:
        with Journal() as j:
            if reg.get("active"):
                _park_first(root, reg, host, keep, note, j)
            elif run_entries(live):  # leftovers of an unregistered run: archive them, the new run starts empty
                held = _archive_unregistered(live, j, archived)
            if not branch_exists(root, branch):
                j.step("create the branch")
                git("branch", branch, base, cwd=root)
                j.undo(lambda: git("branch", "-D", branch, cwd=root, check=False), "branch")
            live.mkdir(parents=True, exist_ok=True)
            j.step("enter the session")
            rc = enter_session(root, reg, host, row, j)
        for dest in archived:  # the session did not start: put the leftovers back, say nothing
            _say_archived(root, dest) if rc == 0 else run_archive.restore(dest, live)
        return rc
    finally:
        if held is not None:
            move_run_state(held, live)
            shutil.rmtree(held, ignore_errors=True)


def _archive_unregistered(live: Path, j: Journal, archived: list) -> Path | None:
    """Archive an unregistered run's leftovers; return the folder holding config files set aside, if any."""
    j.step("archive the leftovers")
    dest = run_archive.archive(live, None, unfinished=True)
    if dest is not None:
        archived.append(dest)
        j.undo(lambda: run_archive.restore(dest, live), "archive")
    held = Path(tempfile.mkdtemp(prefix="superlite-ensure-")) if run_entries(live) else None
    if held:  # config files stay live: set them aside, enter wipes the folder
        moved = []
        j.undo(lambda: _restore_held(live, held, moved), "preserved run state")
        move_run_state(live, held, moved)
    return held


def _park_first(root: Path, reg: dict, host: Path, keep: bool, note: str | None, j: Journal) -> None:
    j.step("park the active session")
    park_active(root, reg, host, reg["active"], keep_untracked=keep, j=j, write_reg=False)
    if note:
        print(note)


# ---- create

def cmd_create(a) -> int:  # O(tree)
    slug = validate_slug(a.slug)
    root = hub_root(start_dir(a))
    with registry_lock(root):
        return guarded("create", lambda: _create(root, slug, a))


def _create(root: Path, slug: str, a) -> int:
    reg, host = read(root), root
    existing = find_row(reg, slug)
    branch = (existing or {}).get("branch") or branch_for(slug)
    owner = branch_owner(root, branch)
    if owner and not same(owner, host):
        return print_redirect(owner, slug, branch)
    if existing and reg.get("active") == slug:
        print(f"session: {slug} is already the active session in {host.as_posix()}")
        return 0
    if existing:
        print(f"session: {slug} already exists; run superlite-cli session use --slug {slug}", file=sys.stderr)
        return 2
    here = toplevel(start_dir(a))
    if a.worktree and harness.under_claude_code() and not same(here, host):  # the rerun inside Claude's worktree
        return registration.register_linked(root, reg, here, slug)
    if branch_exists(root, branch):
        print(f"session: branch {branch} already exists; pick another --slug", file=sys.stderr)
        return 2
    base = a.base or fresh_base(root, reg, host) or reg.get("base") or default_base(root)
    if git("rev-parse", "--verify", "-q", f"{base}^{{commit}}", cwd=root, check=False).returncode != 0:
        print(f"session: unknown --base {base}", file=sys.stderr)
        return 2
    if a.worktree:
        rc = claude_create.gate(root, slug, base, a)
        return rc if rc is not None else _create_parallel(root, reg, slug, branch, base)
    return _create_in_host(root, reg, host, slug, branch, base, a)


def _create_parallel(root: Path, reg: dict, slug: str, branch: str, base: str) -> int:
    wt = session_dir(root, slug)
    if wt.exists():
        print(f"session: worktree path already exists ({wt})", file=sys.stderr)
        return 2
    reg["base"] = reg.get("base") or base
    hide_all(root)
    with Journal() as j:
        j.step("create the worktree")
        wt.parent.mkdir(parents=True, exist_ok=True)
        git("worktree", "add", "-q", "-b", branch, str(wt), base, cwd=root)
        j.undo(lambda: (git("worktree", "remove", "--force", str(wt), cwd=root, check=False),
                        git("branch", "-D", branch, cwd=root, check=False)), "worktree and branch")
        state.run_dir(wt).mkdir(parents=True, exist_ok=True)
        j.step("write the registry")
        upsert_row(reg, {"slug": slug, "branch": branch, "worktree": wt.as_posix(),
                         "store": store_path(root, slug).as_posix(), "has_stash": False,
                         "status": "parallel", "owned": True, "created_at": now_ms(), "run": "open",
                         **base_now(root, {**reg, "base": base})})
        write(root, reg)
    print(f"session: created parallel {slug} -> {wt.as_posix()} on {branch}")
    return 0


def _create_in_host(root: Path, reg: dict, host: Path, slug: str, branch: str, base: str, a) -> int:
    note, keep = None, False
    if reg.get("active"):  # a park is coming: ask first
        stop, note, keep = untracked_gate(host, reg["active"], getattr(a, "untracked", None) or reg.get("untracked"),
                                          base)
        if stop is not None:
            return stop
    if in_progress(host):
        print("session: merge/rebase in progress; finish it before create", file=sys.stderr)
        return 2
    if not reg.get("active") and is_dirty(host):
        print(f"session: host {host.as_posix()} has uncommitted changes outside any session; "
              f"commit or stash them first", file=sys.stderr)
        return 2
    reg["base"] = reg.get("base") or base
    hide_all(root)
    row = {"slug": slug, "branch": branch, "worktree": host.as_posix(), "has_stash": False,
           "created_at": now_ms(), "run": "open", **base_now(root, {**reg, "base": base})}
    state.run_dir(host).mkdir(parents=True, exist_ok=True)
    with Journal() as j:
        if reg.get("active"):
            j.step("park the active session")
            park_active(root, reg, host, reg["active"], keep_untracked=keep, j=j, write_reg=False)
            if note:
                print(note)
        j.step("create the branch")
        git("branch", branch, base, cwd=root)
        j.undo(lambda: git("branch", "-D", branch, cwd=root, check=False), "branch")
        j.step("enter the session")
        rc = enter_session(root, reg, host, row, j)
    if rc == 0:
        print(f"session: created {slug} -> {host.as_posix()} on {branch} (active)")
    return rc


def cmd_untracked(a) -> int:
    root = hub_root(start_dir(a))
    if a.value is None:
        print(f"session: untracked {read(root).get('untracked') or 'ask'}")
        return 0
    with registry_lock(root):
        R.update(root, lambda reg: {**reg, "untracked": a.value})
    print(f"session: untracked set to {a.value}")
    return 0


# ---- restore an archived session

def archived_row(root: Path, reg: dict, slug: str) -> dict | None:
    """The row of a closed session from its manifest; None when the slug is registered or not archived."""
    m = None if find_row(reg, slug) else A.read(root, slug)
    return {k: v for k, v in m.items() if k not in ("closed_at", "wip_ref")} if m else None


def restore_archived(root: Path, reg: dict, host: Path, row: dict, j: Journal) -> int:
    """Re-insert an archived row. A session that lived in an owned worktree gets that worktree back and a
    REDIRECT; otherwise it is entered in the host. The manifest leaves the store with the run files."""
    slug = row["slug"]
    branch = row.get("branch") or branch_for(slug)
    wt_in = Path(row["worktree"]) if row.get("worktree") else None
    if row.get("detached") and not (wt_in and wt_in.is_dir() and not same(wt_in, host)):
        print(f"session: {slug} was a detached worktree that is gone; nothing to restore", file=sys.stderr)
        return 2
    manifest = store_path(root, slug) / "session.json"
    text = manifest.read_text(encoding="utf-8") if manifest.is_file() else None
    manifest.unlink(missing_ok=True)  # it is not a run file
    if text is not None:
        j.undo(lambda: (manifest.parent.mkdir(parents=True, exist_ok=True),
                        manifest.write_text(text, encoding="utf-8", newline="\n")), "manifest")
    wt0 = Path(row["worktree"]) if row.get("worktree") else None
    if not row.get("owned") and row.get("adopted_branch") and wt0 and wt0.is_dir() and not same(wt0, host):
        j.step("re-register in place")  # an adopted worktree still exists: no new checkout
        restore_run_state(store_path(root, slug), state.run_dir(wt0))
        upsert_row(reg, {**row, "status": "parallel", "run": "open", "has_stash": False,
                         "store": store_path(root, slug).as_posix()})
        write(root, reg)
        print(f"session: registered {slug} where it is ({wt0.as_posix()})")
        session_line(wt0, slug, row.get("branch") or "HEAD")
        return 0
    if row.get("owned") and row.get("worktree"):
        wt = Path(row["worktree"])
        j.step("recreate the worktree")
        wt.parent.mkdir(parents=True, exist_ok=True)
        if branch_exists(root, branch):
            git("worktree", "add", "-q", str(wt), branch, cwd=root)
        else:
            git("worktree", "add", "-q", "-b", branch, str(wt),
                row.get("base") or reg.get("base") or default_base(root), cwd=root)
        j.undo(lambda: git("worktree", "remove", "--force", str(wt), cwd=root, check=False), "worktree")
        restore_run_state(store_path(root, slug), state.run_dir(wt))
        j.undo(lambda: park_run_state(state.run_dir(wt), store_path(root, slug)), "run files")
        upsert_row(reg, {**row, "worktree": wt.as_posix(), "status": "parallel", "run": "open",
                         "has_stash": False, "store": store_path(root, slug).as_posix()})
        write(root, reg)
        if not W.restore(wt, slug):
            print(f"session: could not apply {W.ref_for(slug)} in {wt.as_posix()}; it stays on the ref",
                  file=sys.stderr)
        return print_redirect(wt.as_posix(), slug, branch)
    rc = enter_session(root, reg, host, {**row, "branch": branch, "run": "open", "has_stash": False}, j)
    if rc == 0 and not W.restore(host, slug):
        print(f"session: could not apply {W.ref_for(slug)} in {host.as_posix()}; it stays on the ref",
              file=sys.stderr)
        return 2
    if rc == 0:
        session_line(host, slug, branch)
    return rc


from session_close import cmd_close  # noqa: E402,F401 | re-export; last, because session_close imports this module lazily
