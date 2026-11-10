"""Session switching: use, discard, split, status. Stdlib only.

Every check runs before the first change. The changing steps of `use` and `split` run in a Journal, so a
failure undoes them newest first and the registry is written last; `discard` removes the registry row
last, so a failed discard can be run again. Stashes are found by the object id recorded in the row.
"""
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import gitignore  # noqa: E402
import session_archive as A  # noqa: E402
import session_registry as R  # noqa: E402
import session_wip as W  # noqa: E402
import state  # noqa: E402
import worktree_home  # noqa: E402
from journal import Journal  # noqa: E402
from session_ensure import archived_row, enter_session, guarded, park_active, restore_archived  # noqa: E402
from session_registry import (  # noqa: E402
    ORCH, base_now, behind_base, branch_exists, branch_for, branch_owner, clear_run_state, current_branch,
    default_base, drop_stashes, find_row, git, hub_root, in_progress, is_dirty, owned_worktree, print_redirect,
    registry_lock, resolve_host, same, session_line, session_stashes, sessions_dir, sha_of, slug_from_branch, split_move,
    start_dir, store_path, toplevel, unmerged, untracked_gate, upsert_row, validate_slug)

read_reg = R.read
write_reg = R.write


def cmd_status(a) -> int:  # O(N)
    root = hub_root(start_dir(a))
    reg = read_reg(root)
    if a.json:
        print(json.dumps(reg))
        return 0
    if not reg["sessions"]:
        print(f"session: none (no sessions in {state.DIR_NAME}/sessions.json)")
        return 0
    print(f"session: active={reg.get('active') or '-'} host={reg.get('host_worktree') or '-'}")
    tips: dict = {}  # one lookup per base branch, not per row
    for s in reg["sessions"]:
        stash = "stash:yes" if s.get("has_stash") else "stash:no"
        name = s.get("base") or reg.get("base")
        if name and name not in tips:
            tips[name] = sha_of(root, f"refs/heads/{name}")
        behind = behind_base(root, reg, s, tips.get(name, ""))
        print(f"session: {s.get('status', '?')} {s['slug']} | branch {s.get('branch')} | "
              f"worktree {s.get('worktree') or '(removed)'} | {stash} | run:{s.get('run') or 'open'}"
              + (" | harness" if s.get("harness") else " | adopted" if s.get("adopted_branch") else "")
              + (f" | {name}:+{behind}" if behind else ""))
    return 0


def cmd_use(a) -> int:  # O(S)
    slug = validate_slug(a.slug)
    root = hub_root(start_dir(a))
    with registry_lock(root):
        return guarded("use", lambda: _use(root, slug, a))


def _use(root: Path, slug: str, a) -> int:
    reg = read_reg(root)
    row = find_row(reg, slug)
    archived = archived_row(root, reg, slug)
    known = row or archived or {}
    branch = known.get("branch") if known.get("adopted_branch") else (known.get("branch") or branch_for(slug))
    host = resolve_host(root, reg)
    if archived and _in_place(archived, start_dir(a)):  # an adopted worktree, used from inside itself
        with Journal() as j:
            j.step("re-register in place")
            return restore_archived(root, reg, host, archived, j)
    if archived and archived.get("detached") and not _in_place(archived, start_dir(a)):
        print(f"session: {slug} was a detached worktree; run ensure inside it to restore", file=sys.stderr)
        return 2
    if row and row.get("detached"):  # no branch to look up or switch to: its own checkout is the session
        return _use_detached(row, slug, start_dir(a))
    owner = branch_owner(root, branch) if branch else None
    if owner and not same(owner, host):
        return print_redirect(owner, slug, branch)
    if archived and archived.get("owned") and archived.get("worktree") and not a.continue_apply \
            and not same(archived["worktree"], host):  # its own checkout comes back: the hub session is untouched
        with Journal() as j:
            j.step("recreate the worktree")
            return restore_archived(root, reg, host, archived, j)
    active = reg.get("active")
    if a.continue_apply:
        return continue_apply(root, reg, host, slug)
    if active == slug and current_branch(host) == branch:
        if row and row.get("has_stash"):
            print(f"session: {slug} has an unresolved stash; resolve, then "
                  f"superlite-cli session use --slug {slug} --continue", file=sys.stderr)
            return 2
        print(f"session: already active {slug} on {branch} in {host.as_posix()}")
        return 0
    if row is None and not archived and not (branch and branch_exists(root, branch)):
        print(f"session: unknown session {slug}; run superlite-cli session create --slug {slug}", file=sys.stderr)
        return 2
    if in_progress(host):
        print("session: merge/rebase in progress; finish it before use", file=sys.stderr)
        return 2
    parking = bool(active) and active != slug
    if not parking and is_dirty(host):
        print(f"session: host {host.as_posix()} has uncommitted changes outside any session; "
              f"commit or stash them first", file=sys.stderr)
        return 2
    note, keep = None, False
    if parking:
        target = branch if branch and branch_exists(root, branch) else (reg.get("base") or default_base(root))
        stop, note, keep = untracked_gate(host, active, getattr(a, "untracked", None) or reg.get("untracked"),
                                          target)
        if stop is not None:
            return stop
    with Journal() as j:
        if parking:
            j.step("park the active session")
            park_active(root, reg, host, active, keep_untracked=keep, j=j, write_reg=False)
            if note:
                print(note)
        j.step("enter the session")
        if archived:
            return restore_archived(root, reg, host, archived, j)
        return enter_session(root, reg, host, row or {"slug": slug, "branch": branch, "has_stash": False}, j)


def _use_detached(row: dict, slug: str, start: Path) -> int:
    """A registered detached worktree: report it in place or redirect to it; refuse when the checkout is gone."""
    wt = row.get("worktree")
    if not wt or not Path(wt).is_dir():
        print(f"session: {slug} was a detached worktree that is gone; nothing to restore", file=sys.stderr)
        return 2
    if same(wt, toplevel(start)):
        session_line(wt, slug, "HEAD")
        return 0
    return print_redirect(wt, slug, "HEAD")


def _in_place(archived: dict, start: Path) -> bool:
    """The archived row is an adopted worktree that still exists and is the checkout the command runs in."""
    wt = archived.get("worktree")
    return bool(wt and not archived.get("owned") and archived.get("adopted_branch") and Path(wt).is_dir()
                and same(wt, toplevel(start)))


def continue_apply(root: Path, reg: dict, host: Path, slug: str) -> int:  # O(stash)
    """Finish a conflicted restore: the stash is already applied, so drop it instead of reapplying."""
    row = find_row(reg, slug)
    if reg.get("active") != slug or row is None:
        print(f"session: {slug} is not the active session; nothing to continue", file=sys.stderr)
        return 2
    left = unmerged(host)
    if left:
        print(f"session: unresolved conflicts remain: {' '.join(left)}", file=sys.stderr)
        return 2
    if row.get("has_stash"):
        drop_stashes(host, session_stashes(host, row)[:1])
    R.update(root, lambda r: find_row(r, slug).update(has_stash=False, stash_oid=None))
    print(f"session: active {slug} on {row.get('branch')} in {host.as_posix()} (restore complete)")
    return 0


def cmd_discard(a) -> int:  # O(tree)
    slug = validate_slug(a.slug)
    root = hub_root(start_dir(a))
    with registry_lock(root):
        reg = read_reg(root)
        row = find_row(reg, slug)
        if not row:
            m = A.read(root, slug)
            if m is None:
                print(f"session: no registered session {slug}", file=sys.stderr)
                return 2
            git("update-ref", "-d", W.ref_for(slug), cwd=root, check=False)  # archived: ref, manifest, branch
            shutil.rmtree(store_path(root, slug), ignore_errors=True)
            branch = m.get("branch") or branch_for(slug)
            if not a.keep_branch and not m.get("adopted_branch") and branch_owner(root, branch) is None:
                git("branch", "-D", branch, cwd=root, check=False)
            print(f"session: discarded {slug}")
            _exit_hint(slug, m)
            return 0
        host = resolve_host(root, reg)
        if same(row.get("worktree"), host) and reg.get("active") == slug and in_progress(host):
            print("session: merge/rebase in progress; finish or abort it before discard", file=sys.stderr)
            return 2
        _discard(root, reg, row, slug, a.keep_branch)
    print(f"session: discarded {slug}")
    _exit_hint(slug, row)
    return 0


def _exit_hint(slug: str, row: dict) -> None:
    """An adopted harness worktree is never removed here; the user leaves it with ExitWorktree."""
    if row.get("harness"):
        print(f"session: {slug} lives in a harness worktree; leave it with ExitWorktree")


def _discard(root: Path, reg: dict, row: dict, slug: str, keep_branch: bool) -> None:
    """Drop the session's work, then its registry row last: a failure leaves the row, so discard can be run again."""
    branch = row.get("branch") or branch_for(slug)
    host = resolve_host(root, reg)
    if same(row.get("worktree"), host):
        _discard_on_host(reg, row, slug, host)
    elif owned_worktree(root, row) and Path(row["worktree"]).is_dir():
        git("worktree", "remove", "--force", row["worktree"], cwd=root)
        git("worktree", "prune", cwd=root, check=False)
    shutil.rmtree(store_path(root, slug), ignore_errors=True)
    git("update-ref", "-d", W.ref_for(slug), cwd=root, check=False)  # a failed archive restore may leave it
    if not keep_branch and not row.get("adopted_branch") and row.get("branch") and branch_owner(root, branch) is None:
        git("branch", "-D", branch, cwd=root, check=False)

    def drop(r):
        r["sessions"] = [s for s in r["sessions"] if s.get("slug") != slug]
        if r.get("active") == slug:
            r["active"] = None
    R.update(root, drop)


def _discard_on_host(reg: dict, row: dict, slug: str, host: Path) -> None:
    if reg.get("active") == slug:
        git("reset", "-q", "--hard", cwd=host)
        git("clean", "-fdq", cwd=host)
        clear_run_state(state.run_dir(host))
        git("switch", "-q", "--detach", cwd=host)
    if row.get("has_stash"):
        drop_stashes(host, session_stashes(host, row))


def cmd_split(a) -> int:  # O(S) + trees
    """Parallel exception: keep the open run in its own worktree; cut a new worktree+branch for --new."""
    start = start_dir(a)
    here, root = toplevel(start), hub_root(start)
    if not state.path(here, ORCH).is_file():
        print(f"session: no open run ({state.DIR_NAME}/orchestrator.json missing); nothing to move", file=sys.stderr)
        return 2
    cur = current_branch(here)
    if cur == "HEAD":
        print("session: detached HEAD; check out a branch before splitting", file=sys.stderr)
        return 2
    new_slug = validate_slug(a.new, "--new")
    keep_slug = validate_slug(a.keep or slug_from_branch(cur), "--keep")
    if keep_slug == new_slug:
        print("session: --keep and --new must differ", file=sys.stderr)
        return 2
    with registry_lock(root):
        return guarded("split", lambda: _split(a, root, here, cur, new_slug, keep_slug))


def _split_checks(reg: dict, root: Path, here: Path, a, cur: str, new_slug: str, keep_slug: str) -> dict | int:
    """Every check before the first change: the plan as a dict, or the exit code that stops the command."""
    base = a.base or reg.get("base") or default_base(root)
    if git("rev-parse", "--verify", "-q", f"{base}^{{commit}}", cwd=root, check=False).returncode != 0:
        print(f"session: unknown --base {base}", file=sys.stderr)
        return 2
    base_owner = branch_owner(root, base) if branch_exists(root, base) else None
    # Leave the run where it is when this checkout is a linked worktree or base is busy elsewhere.
    in_place = not same(here, root) or (cur != base and base_owner is not None and not same(base_owner, here))
    keep_branch = cur if (cur != base or in_place) else branch_for(keep_slug)
    new_branch = branch_for(new_slug)
    wt_keep = here if in_place else worktree_home.session_dir(root, keep_slug)
    wt_new = worktree_home.session_dir(root, new_slug)
    owner = branch_owner(root, new_branch)
    if owner:
        return print_redirect(owner, new_slug, new_branch)
    for slug in (keep_slug, new_slug):
        row = find_row(reg, slug)
        if row and not (slug == keep_slug and row.get("branch") == keep_branch
                        and same(row.get("worktree"), wt_keep)):
            print(f"session: session {slug} already exists; pick another slug", file=sys.stderr)
            return 2
    if (not in_place and wt_keep.exists()) or wt_new.exists():
        print(f"session: worktree path already exists ({wt_new if wt_new.exists() else wt_keep})", file=sys.stderr)
        return 2
    if branch_exists(root, new_branch):
        print(f"session: branch {new_branch} already exists", file=sys.stderr)
        return 2
    if not in_place and cur == base and branch_exists(root, keep_branch):
        print(f"session: branch {keep_branch} already exists; pass --keep with another slug", file=sys.stderr)
        return 2
    if not in_place and in_progress(here):
        print("session: merge/rebase in progress; finish it before split", file=sys.stderr)
        return 2
    return {"base": base, "in_place": in_place, "keep_branch": keep_branch, "new_branch": new_branch,
            "wt_keep": wt_keep, "wt_new": wt_new}


def _split(a, root: Path, here: Path, cur: str, new_slug: str, keep_slug: str) -> int:
    reg = read_reg(root)
    plan = _split_checks(reg, root, here, a, cur, new_slug, keep_slug)
    if isinstance(plan, int):
        return plan
    base, in_place, keep_branch, new_branch = plan["base"], plan["in_place"], plan["keep_branch"], plan["new_branch"]
    wt_keep, wt_new = plan["wt_keep"], plan["wt_new"]
    with Journal() as j:
        gi = root / ".gitignore"
        original = gi.read_bytes() if gi.is_file() else None
        j.step("update .gitignore")
        gitignore.ensure(str(root), [])

        def restore_gitignore():
            if original is None:
                gi.unlink(missing_ok=True)
            else:
                gi.write_bytes(original)
        j.undo(restore_gitignore, ".gitignore")
        if not in_place:
            split_move(root, state.run_dir(here), cur, base, keep_branch, wt_keep, j)
        j.step("create the new worktree")
        worktree_home.hide_all(root)
        wt_new.parent.mkdir(parents=True, exist_ok=True)
        git("worktree", "add", "-q", "-b", new_branch, str(wt_new), base, cwd=root)
        j.undo(lambda: (git("worktree", "remove", "--force", str(wt_new), cwd=root, check=False),
                        git("branch", "-D", new_branch, cwd=root, check=False)), "worktree and branch")
        j.step("write the registry")
        def record(r):
            r["base"] = r.get("base") or base
            prior = find_row(r, keep_slug)
            keep_owned = bool(prior and prior.get("owned")) if in_place else True
            for slug, branch, wt, owned, role in ((keep_slug, keep_branch, wt_keep, keep_owned, "previous"),
                                                  (new_slug, new_branch, wt_new, True, "new")):
                upsert_row(r, {"slug": slug, "branch": branch, "worktree": wt.as_posix(),
                               "store": store_path(root, slug).as_posix(), "has_stash": False,
                               "status": "parallel", "owned": owned, "role": role, "topic": slug,
                               **base_now(root, {**r, "base": base})})
        R.update(root, record)
    print(f"session: previous {keep_slug} -> {wt_keep.as_posix()} on {keep_branch}")
    print(f"session: new {new_slug} -> {wt_new.as_posix()} on {new_branch}")
    if in_place:
        print(f"session: the open run stays in {wt_keep.as_posix()}; start the new request in the new worktree")
    else:
        print(f"session: main checkout on {base} has no open run; resume in the previous worktree, "
              f"start the new request in the new one")
    return 0


