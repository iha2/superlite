#!/usr/bin/env python3
"""Worktree-and-branch sessions: create, use (park/stash/switch or REDIRECT), split, discard, status.

    session.py create --slug S [--base REF] [--worktree] [--root DIR]
    session.py use --slug S [--continue] [--root DIR]
    session.py split --new S [--keep S] [--base REF] [--root DIR]
    session.py discard --slug S [--keep-branch] [--root DIR]
    session.py status [--root DIR] [--json]

Default: one host worktree (`.superlite/worktrees/sessions/host`) and many session branches
(`superlite/sessions/<slug>`). `use` parks the host's run files into `session-store/<slug>/`, stashes
dirty project files, switches branch, restores. If the branch is already checked out elsewhere,
print REDIRECT (exit 3) instead of a second checkout. `split` / `create --worktree` cut a parallel
worktree with a new branch. The registry always lives in the main checkout (the hub), whichever
worktree a command runs from. Checkout directories under `.superlite` (sandboxes/, worktrees/) never
move. Stdlib only.
"""
# Complexity notation:
#   mostly O(1) file work plus git spawns; S = size of parked run files; N = sessions in registry
import argparse  # O(1)
import json  # O(1)
import os  # O(1)
import re  # O(1)
import shutil  # O(1)
import subprocess  # O(1)
import sys  # O(1)
import tempfile  # O(1)
import time  # O(1)
from contextlib import contextmanager  # O(1)
from pathlib import Path  # O(1)

HERE = Path(__file__).resolve().parent  # O(1)
if str(HERE) not in sys.path:  # O(1)
    sys.path.insert(0, str(HERE))  # O(1)

import gitignore  # noqa: E402  | O(1)

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")  # O(1)
RESERVED = {"host"}  # O(1)
FILE = "sessions.json"  # O(1)
LOCK = "sessions.lock"  # O(1)
ORCH = "orchestrator.json"  # O(1)
STORE = "session-store"  # O(1)
SESSIONS_REL = Path(".superlite") / "worktrees" / "sessions"  # O(1)
HOST_REL = SESSIONS_REL / "host"  # O(1)
# Hub-level entries and real git checkouts: never parked, moved or wiped with run state.
STATIONARY = {"worktrees", "sandboxes", STORE, FILE, LOCK}  # O(1)
EXIT_REDIRECT = 3  # O(1)
STASH_PREFIX = "superlite-session:"  # O(1)


class GitError(Exception):  # O(1)
    pass  # O(1)


def git(*args, cwd=None, check=True):  # O(1) + process spawn
    r = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=cwd)  # O(out)
    if check and r.returncode != 0:  # O(1)
        raise GitError(f"git {' '.join(args[:2])} failed: {(r.stderr or r.stdout).strip()}")  # O(1)
    return r  # O(1)


def toplevel(path: Path) -> Path:  # O(1)
    return Path(git("rev-parse", "--show-toplevel", cwd=path).stdout.strip()).resolve()  # O(1)


def hub_root(path: Path) -> Path:  # O(1)
    """Main checkout of the repository containing `path` (where sessions.json lives)."""
    common = git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=path, check=False).stdout.strip()  # O(1)
    if common and Path(common).name == ".git":  # O(1)
        return Path(common).parent.resolve()  # O(1)
    return toplevel(path)  # O(1)


def start_dir(a) -> Path:  # O(1)
    return Path(a.root).resolve() if getattr(a, "root", None) else Path.cwd()  # O(1)


def same(a, b) -> bool:  # O(1)
    return bool(a) and bool(b) and Path(a).resolve() == Path(b).resolve()  # O(1)


def default_base(root: Path) -> str:  # O(1)
    for name in ("main", "master"):  # O(1)
        if branch_exists(root, name):  # O(1)
            return name  # O(1)
    return current_branch(root)  # O(1)


def branch_exists(root: Path, branch: str) -> bool:  # O(1)
    return git("rev-parse", "--verify", "-q", f"refs/heads/{branch}", cwd=root, check=False).returncode == 0  # O(1)


def current_branch(cwd: Path) -> str:  # O(1)
    return git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd).stdout.strip()  # O(1)


def is_dirty(cwd: Path) -> bool:  # O(tree)
    return bool(git("status", "--porcelain", cwd=cwd, check=False).stdout.strip())  # O(tree)


def validate_slug(name: str, flag: str = "--slug") -> str:  # O(1)
    if not SLUG.match(name or ""):  # O(1)
        print(f"session: {flag} must be a short slug (a-z, 0-9, -)", file=sys.stderr)  # O(1)
        sys.exit(2)  # O(1)
    if name in RESERVED:  # O(1)
        print(f"session: {flag} {name!r} is reserved", file=sys.stderr)  # O(1)
        sys.exit(2)  # O(1)
    return name  # O(1)


def branch_for(slug: str) -> str:  # O(1)
    return f"superlite/sessions/{slug}"  # O(1)


def stash_msg(slug: str) -> str:  # O(1)
    return f"{STASH_PREFIX}{slug}"  # O(1)


def slug_from_branch(branch: str) -> str:  # O(1)
    b = branch.strip()  # O(1)
    if b.startswith("superlite/sessions/"):  # O(1)
        b = b[len("superlite/sessions/"):]  # O(1)
    elif b.startswith("superlite/"):  # O(1)
        b = b[len("superlite/"):]  # O(1)
    b = re.sub(r"[^a-z0-9-]+", "-", b.lower()).strip("-")  # O(len)
    return b if SLUG.match(b or "") and b not in RESERVED else "current"  # O(1)


def hub_sl(root: Path) -> Path:  # O(1)
    return root / ".superlite"  # O(1)


def store_path(root: Path, slug: str) -> Path:  # O(1)
    return hub_sl(root) / STORE / slug  # O(1)


def host_path(root: Path, reg: dict) -> Path:  # O(1)
    return Path(reg["host_worktree"]) if reg.get("host_worktree") else root / HOST_REL  # O(1)


@contextmanager
def registry_lock(root: Path):  # O(1) + wait
    """Serialize every registry read-modify-write and the git transitions it describes."""
    d = hub_sl(root)  # O(1)
    d.mkdir(parents=True, exist_ok=True)  # O(1)
    fh = open(d / LOCK, "a+")  # O(1)
    try:  # O(1)
        if os.name == "nt":  # O(1)
            import msvcrt  # O(1)
            fh.seek(0)  # O(1)
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)  # O(1) + wait
        else:  # O(1)
            import fcntl  # O(1)
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)  # O(1) + wait
        yield  # O(1)
    finally:  # O(1)
        try:  # O(1)
            if os.name == "nt":  # O(1)
                import msvcrt  # O(1)
                fh.seek(0)  # O(1)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)  # O(1)
            else:  # O(1)
                import fcntl  # O(1)
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)  # O(1)
        finally:  # O(1)
            fh.close()  # O(1)


def read_reg(root: Path) -> dict:  # O(file)
    f = hub_sl(root) / FILE  # O(1)
    try:  # O(1)
        d = json.loads(f.read_text(encoding="utf-8"))  # O(file)
    except (OSError, ValueError):  # O(1)
        d = {}  # O(1)
    if not isinstance(d, dict):  # O(1)
        d = {}  # O(1)
    sessions = d.get("sessions")  # O(1)
    if not isinstance(sessions, list):  # O(1)
        sessions = []  # O(1)
    # migrate v1 topic/role rows
    out = []  # O(1)
    for s in sessions:  # O(N)
        if not isinstance(s, dict):  # O(1)
            continue  # O(1)
        slug = s.get("slug") or s.get("topic")  # O(1)
        if not slug:  # O(1)
            continue  # O(1)
        row = dict(s)  # O(1)
        row["slug"] = slug  # O(1)
        row.setdefault("branch", s.get("branch") or branch_for(slug))  # O(1)
        row.setdefault("status", s.get("status") or ("parallel" if s.get("role") else "parked"))  # O(1)
        out.append(row)  # O(1)
    return {"v": 2, "base": d.get("base"), "active": d.get("active"), "host_worktree": d.get("host_worktree"),
            "sessions": out}  # O(1)


def write_reg(root: Path, reg: dict) -> None:  # O(N)
    d = hub_sl(root)  # O(1)
    d.mkdir(parents=True, exist_ok=True)  # O(1)
    payload = {"v": 2, "base": reg.get("base"), "active": reg.get("active"),
               "host_worktree": reg.get("host_worktree"), "sessions": reg.get("sessions") or []}  # O(1)
    fd, tmp = tempfile.mkstemp(prefix=".sessions-", suffix=".json", dir=d)  # O(1)
    try:  # O(1)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:  # O(1)
            fh.write(json.dumps(payload, indent=1) + "\n")  # O(N)
        os.replace(tmp, d / FILE)  # O(1)
    except BaseException:  # O(1)
        Path(tmp).unlink(missing_ok=True)  # O(1)
        raise  # O(1)


def find_row(reg: dict, slug: str) -> dict | None:  # O(N)
    for s in reg["sessions"]:  # O(N)
        if s.get("slug") == slug:  # O(1)
            return s  # O(1)
    return None  # O(1)


def upsert_row(reg: dict, row: dict) -> dict:  # O(N)
    for i, s in enumerate(reg["sessions"]):  # O(N)
        if s.get("slug") == row["slug"]:  # O(1)
            reg["sessions"][i] = {**s, **row}  # O(1)
            return reg["sessions"][i]  # O(1)
    reg["sessions"].append(row)  # O(1)
    return row  # O(1)


def worktree_map(root: Path) -> dict[str, str]:  # O(W)
    """branch -> worktree path for local checkouts."""
    out = {}  # O(1)
    text = git("worktree", "list", "--porcelain", cwd=root, check=False).stdout  # O(W)
    path, branch = None, None  # O(1)
    for ln in text.splitlines() + [""]:  # O(W)
        if ln.startswith("worktree "):  # O(1)
            path = ln[len("worktree "):]  # O(1)
        elif ln.startswith("branch "):  # O(1)
            ref = ln[len("branch "):]  # O(1)
            branch = ref[11:] if ref.startswith("refs/heads/") else ref  # O(1)
        elif ln == "":  # O(1)
            if path and branch:  # O(1)
                out[branch] = path  # O(1)
            path, branch = None, None  # O(1)
    return out  # O(1)


def branch_owner(root: Path, branch: str) -> str | None:  # O(W)
    return worktree_map(root).get(branch)  # O(W)


def print_redirect(path: str, slug: str, branch: str) -> int:  # O(1)
    print(f"REDIRECT worktree={path} slug={slug} branch={branch}")  # O(1)
    print(f"session: branch {branch} is live in {path} — open/cd that worktree (state matches there)",
          file=sys.stderr)  # O(1)
    return EXIT_REDIRECT  # O(1)


def in_progress(cwd: Path) -> bool:  # O(1)
    gitdir = Path(git("rev-parse", "--git-dir", cwd=cwd).stdout.strip())  # O(1)
    if not gitdir.is_absolute():  # O(1)
        gitdir = (cwd / gitdir).resolve()  # O(1)
    return (gitdir / "MERGE_HEAD").exists() or (gitdir / "rebase-merge").exists() or (gitdir / "rebase-apply").exists()  # O(1)


def run_entries(live_sl: Path) -> list[Path]:  # O(entries)
    if not live_sl.is_dir():  # O(1)
        return []  # O(1)
    return [p for p in live_sl.iterdir() if p.name not in STATIONARY]  # O(entries)


def move_run_state(src_sl: Path, dest: Path) -> None:  # O(S)
    """Move run files (not STATIONARY checkouts/registry) from src_sl into dest."""
    dest.mkdir(parents=True, exist_ok=True)  # O(1)
    for p in run_entries(src_sl):  # O(entries)
        target = dest / p.name  # O(1)
        if target.is_dir() and not target.is_symlink():  # O(1)
            shutil.rmtree(target)  # O(S)
        elif target.exists() or target.is_symlink():  # O(1)
            target.unlink()  # O(1)
        shutil.move(str(p), str(target))  # O(S)


def clear_run_state(live_sl: Path) -> None:  # O(S)
    for p in run_entries(live_sl):  # O(entries)
        if p.is_dir() and not p.is_symlink():  # O(1)
            shutil.rmtree(p, ignore_errors=True)  # O(S)
        else:  # O(1)
            p.unlink(missing_ok=True)  # O(1)


def park_run_state(live_sl: Path, store: Path) -> None:  # O(S)
    if store.exists():  # O(1)
        shutil.rmtree(store)  # O(S)
    move_run_state(live_sl, store)  # O(S)


def restore_run_state(store: Path, live_sl: Path) -> None:  # O(S)
    clear_run_state(live_sl)  # O(S)
    live_sl.mkdir(parents=True, exist_ok=True)  # O(1)
    if store.is_dir():  # O(1)
        move_run_state(store, live_sl)  # O(S)
        shutil.rmtree(store, ignore_errors=True)  # O(1)


def stash_entries(cwd: Path) -> list[tuple[str, str, str]]:  # O(stash)
    """(ref, oid, message) for every stash, newest first."""
    out = []  # O(1)
    text = git("stash", "list", "--format=%gd%x1f%H%x1f%gs", cwd=cwd, check=False).stdout  # O(stash)
    for ln in text.splitlines():  # O(stash)
        parts = ln.split("\x1f")  # O(1)
        if len(parts) != 3:  # O(1)
            continue  # O(1)
        ref, oid, subject = parts  # O(1)
        # %gs is "On <branch>: <message>" (or "WIP on <branch>: ..." for message-less stashes)
        msg = subject.split(": ", 1)[1] if ": " in subject else subject  # O(1)
        out.append((ref, oid, msg))  # O(1)
    return out  # O(1)


def session_stashes(cwd: Path, row: dict) -> list[str]:  # O(stash)
    """Stash refs that belong to this session row: its recorded object id, else its exact message."""
    oid = row.get("stash_oid")  # O(1)
    msg = row.get("stash_message") or stash_msg(row["slug"])  # O(1)
    return [ref for ref, o, m in stash_entries(cwd) if (o == oid if oid else m == msg)]  # O(stash)


def drop_stashes(cwd: Path, refs: list[str]) -> None:  # O(k)
    def index(ref: str) -> int:  # O(1)
        m = re.search(r"\{(\d+)\}", ref)  # O(1)
        return int(m.group(1)) if m else -1  # O(1)
    for ref in sorted(refs, key=index, reverse=True):  # O(k log k)
        git("stash", "drop", "-q", ref, cwd=cwd, check=False)  # O(1)


def stash_push(cwd: Path, message: str) -> str:  # O(diff)
    git("stash", "push", "-q", "-u", "-m", message, cwd=cwd)  # O(diff)
    return git("rev-parse", "refs/stash", cwd=cwd).stdout.strip()  # O(1)


def restore_stash(cwd: Path, oid: str | None) -> bool:  # O(diff)
    """Apply the stash with object id `oid` in cwd and drop it; False (stash kept) when it does not apply."""
    if not oid:  # O(1)
        return True  # O(1)
    ref = next((r for r, o, _ in stash_entries(cwd) if o == oid), None)  # O(stash)
    if ref is None or not stash_apply(cwd, ref):  # O(diff)
        return False  # O(1)
    drop_stashes(cwd, [ref])  # O(1)
    return True  # O(1)


def stash_apply(cwd: Path, ref: str) -> bool:  # O(diff)
    """Apply with the index restored; fall back to a plain apply when the index cannot be rebuilt."""
    if git("stash", "apply", "-q", "--index", ref, cwd=cwd, check=False).returncode == 0:  # O(diff)
        return True  # O(1)
    if is_dirty(cwd):  # O(tree)
        return False  # O(1)
    return git("stash", "apply", "-q", ref, cwd=cwd, check=False).returncode == 0  # O(diff)


def unmerged(cwd: Path) -> list[str]:  # O(tree)
    return git("diff", "--name-only", "--diff-filter=U", cwd=cwd, check=False).stdout.split()  # O(tree)


def ensure_host(root: Path, reg: dict, base: str, first_branch: str) -> Path:  # O(tree)
    """Return host worktree path, creating it on first_branch if missing."""
    host = host_path(root, reg)  # O(1)
    if host.is_dir() and git("rev-parse", "--is-inside-work-tree", cwd=host, check=False).returncode == 0:  # O(1)
        reg["host_worktree"] = host.as_posix()  # O(1)
        return host  # O(1)
    git("worktree", "prune", cwd=root, check=False)  # O(W)
    host.parent.mkdir(parents=True, exist_ok=True)  # O(1)
    gitignore.ensure(str(root), [])  # O(g)
    if branch_exists(root, first_branch):  # O(1)
        git("worktree", "add", str(host), first_branch, cwd=root)  # O(tree)
    else:  # O(1)
        git("worktree", "add", "-b", first_branch, str(host), base, cwd=root)  # O(tree)
    reg["host_worktree"] = host.as_posix()  # O(1)
    return host  # O(1)


def park_active(root: Path, reg: dict, host: Path, slug: str) -> None:  # O(S + diff)
    """Stash the active session's WIP, park its run files, and record both before any branch switch."""
    row = find_row(reg, slug) or {"slug": slug, "branch": current_branch(host)}  # O(N)
    oid = stash_push(host, stash_msg(slug)) if is_dirty(host) else None  # O(diff)
    store = store_path(root, slug)  # O(1)
    park_run_state(host / ".superlite", store)  # O(S)
    upsert_row(reg, {**row, "worktree": host.as_posix(), "store": store.as_posix(),
                     "stash_message": stash_msg(slug), "stash_oid": oid, "has_stash": bool(oid),
                     "status": "parked"})  # O(N)
    reg["active"] = None  # O(1)
    write_reg(root, reg)  # O(N)


def enter_session(root: Path, reg: dict, host: Path, row: dict) -> int:  # O(S + diff)
    """Switch the host to row's branch, restore its run files and its stash; mark it active."""
    slug, branch = row["slug"], row["branch"]  # O(1)
    if not branch_exists(root, branch):  # O(1)
        git("branch", branch, reg.get("base") or default_base(root), cwd=root)  # O(1)
    if current_branch(host) != branch:  # O(1)
        git("switch", "-q", branch, cwd=host)  # O(tree)
    store = store_path(root, slug)  # O(1)
    restore_run_state(store, host / ".superlite")  # O(S)
    for s in reg["sessions"]:  # O(N)
        if s.get("slug") != slug and same(s.get("worktree"), host) and s.get("status") == "active":  # O(1)
            s["status"] = "parked"  # O(1)
    row = upsert_row(reg, {**row, "worktree": host.as_posix(), "store": store.as_posix(), "status": "active"})  # O(N)
    reg["active"] = slug  # O(1)
    reg["host_worktree"] = host.as_posix()  # O(1)

    if row.get("has_stash"):  # O(1)
        refs = session_stashes(host, row)  # O(stash)
        if not refs:  # O(1)
            print(f"session: warning: recorded stash for {slug} not found; nothing to restore", file=sys.stderr)  # O(1)
            row.update(has_stash=False, stash_oid=None)  # O(1)
        elif not stash_apply(host, refs[0]):  # O(diff)
            write_reg(root, reg)  # O(N)
            print(f"session: stash apply conflict for {slug}; resolve the files (git add them), then "
                  f"sl session use --slug {slug} --continue", file=sys.stderr)  # O(1)
            return 2  # O(1)
        else:  # O(1)
            drop_stashes(host, refs[:1])  # O(1)
            row.update(has_stash=False, stash_oid=None)  # O(1)
    write_reg(root, reg)  # O(N)
    print(f"session: active {slug} on {branch} in {host.as_posix()}")  # O(1)
    return 0  # O(1)


def cmd_status(a) -> int:  # O(N)
    root = hub_root(start_dir(a))  # O(1)
    reg = read_reg(root)  # O(file)
    if a.json:  # O(1)
        print(json.dumps(reg))  # O(N)
        return 0  # O(1)
    if not reg["sessions"]:  # O(1)
        print("session: none (no sessions in .superlite/sessions.json)")  # O(1)
        return 0  # O(1)
    print(f"session: active={reg.get('active') or '-'} host={reg.get('host_worktree') or '-'}")  # O(1)
    for s in reg["sessions"]:  # O(N)
        stash = "stash:yes" if s.get("has_stash") else "stash:no"  # O(1)
        print(f"session: {s.get('status', '?')} {s['slug']} | branch {s.get('branch')} | "
              f"worktree {s.get('worktree')} | {stash}")  # O(1)
    return 0  # O(1)


def cmd_create(a) -> int:  # O(tree)
    slug = validate_slug(a.slug)  # O(1)
    root = hub_root(start_dir(a))  # O(1)
    with registry_lock(root):  # O(1) + wait
        reg = read_reg(root)  # O(file)
        existing = find_row(reg, slug)  # O(N)
        branch = (existing or {}).get("branch") or branch_for(slug)  # O(1)
        owner = branch_owner(root, branch)  # O(W)
        host = host_path(root, reg)  # O(1)
        if owner and not same(owner, host):  # O(1)
            return print_redirect(owner, slug, branch)  # O(1)
        if existing:  # O(1)
            if reg.get("active") == slug:  # O(1)
                print(f"session: {slug} is already the active session in {host.as_posix()}")  # O(1)
                return 0  # O(1)
            print(f"session: {slug} already exists; run sl session use --slug {slug}", file=sys.stderr)  # O(1)
            return 2  # O(1)
        if branch_exists(root, branch):  # O(1)
            print(f"session: branch {branch} already exists; pick another --slug", file=sys.stderr)  # O(1)
            return 2  # O(1)
        base = a.base or reg.get("base") or default_base(root)  # O(1)
        if git("rev-parse", "--verify", "-q", f"{base}^{{commit}}", cwd=root, check=False).returncode != 0:  # O(1)
            print(f"session: unknown --base {base}", file=sys.stderr)  # O(1)
            return 2  # O(1)
        reg["base"] = base  # O(1)
        gitignore.ensure(str(root), [])  # O(g)
        now = int(time.time() * 1000)  # O(1)

        if a.worktree:  # parallel: dedicated worktree + new branch | O(tree)
            wt = root / SESSIONS_REL / slug  # O(1)
            if wt.exists():  # O(1)
                print(f"session: worktree path already exists ({wt})", file=sys.stderr)  # O(1)
                return 2  # O(1)
            wt.parent.mkdir(parents=True, exist_ok=True)  # O(1)
            git("worktree", "add", "-q", "-b", branch, str(wt), base, cwd=root)  # O(tree)
            (wt / ".superlite").mkdir(parents=True, exist_ok=True)  # O(1)
            upsert_row(reg, {"slug": slug, "branch": branch, "worktree": wt.as_posix(),
                             "store": store_path(root, slug).as_posix(), "has_stash": False,
                             "status": "parallel", "owned": True, "created_at": now})  # O(N)
            write_reg(root, reg)  # O(N)
            print(f"session: created parallel {slug} -> {wt.as_posix()} on {branch}")  # O(1)
            return 0  # O(1)

        fresh_host = not host.is_dir()  # O(1)
        host = ensure_host(root, reg, base, first_branch=branch)  # O(tree)
        (host / ".superlite").mkdir(parents=True, exist_ok=True)  # O(1)
        row = {"slug": slug, "branch": branch, "worktree": host.as_posix(), "has_stash": False,
               "created_at": now}  # O(1)
        if not fresh_host:  # O(1)
            if in_progress(host):  # O(1)
                print("session: merge/rebase in progress; finish it before create", file=sys.stderr)  # O(1)
                return 2  # O(1)
            active = reg.get("active")  # O(1)
            if active:  # O(1)
                park_active(root, reg, host, active)  # O(S)
            elif is_dirty(host):  # O(tree)
                print(f"session: host {host.as_posix()} has uncommitted changes outside any session; "
                      f"commit or stash them first", file=sys.stderr)  # O(1)
                return 2  # O(1)
            git("branch", branch, base, cwd=root)  # O(1)
        rc = enter_session(root, reg, host, row)  # O(S)
        if rc == 0:  # O(1)
            print(f"session: created {slug} -> {host.as_posix()} on {branch} (active)")  # O(1)
        return rc  # O(1)


def cmd_use(a) -> int:  # O(S)
    slug = validate_slug(a.slug)  # O(1)
    root = hub_root(start_dir(a))  # O(1)
    with registry_lock(root):  # O(1) + wait
        reg = read_reg(root)  # O(file)
        row = find_row(reg, slug)  # O(N)
        branch = (row or {}).get("branch") or branch_for(slug)  # O(1)
        host = host_path(root, reg)  # O(1)
        owner = branch_owner(root, branch)  # O(W)
        if owner and not same(owner, host):  # O(1)
            return print_redirect(owner, slug, branch)  # O(1)
        if not host.is_dir():  # O(1)
            print("session: no host worktree; run sl session create --slug <slug> first", file=sys.stderr)  # O(1)
            return 2  # O(1)

        active = reg.get("active")  # O(1)
        if a.continue_apply:  # O(1)
            return continue_apply(root, reg, host, slug)  # O(stash)
        if active == slug and current_branch(host) == branch:  # O(1)
            if row and row.get("has_stash"):  # O(1)
                print(f"session: {slug} has an unresolved stash; resolve, then "
                      f"sl session use --slug {slug} --continue", file=sys.stderr)  # O(1)
                return 2  # O(1)
            print(f"session: already active {slug} on {branch} in {host.as_posix()}")  # O(1)
            return 0  # O(1)
        if row is None and not branch_exists(root, branch):  # O(1)
            print(f"session: unknown session {slug}; run sl session create --slug {slug}", file=sys.stderr)  # O(1)
            return 2  # O(1)
        if in_progress(host):  # O(1)
            print("session: merge/rebase in progress; finish it before use", file=sys.stderr)  # O(1)
            return 2  # O(1)
        if active and active != slug:  # O(1)
            park_active(root, reg, host, active)  # O(S)
        elif is_dirty(host):  # O(tree)
            print(f"session: host {host.as_posix()} has uncommitted changes outside any session; "
                  f"commit or stash them first", file=sys.stderr)  # O(1)
            return 2  # O(1)
        return enter_session(root, reg, host, row or {"slug": slug, "branch": branch, "has_stash": False})  # O(S)


def continue_apply(root: Path, reg: dict, host: Path, slug: str) -> int:  # O(stash)
    """Finish a conflicted restore: the stash is already applied, so drop it instead of reapplying."""
    row = find_row(reg, slug)  # O(N)
    if reg.get("active") != slug or row is None:  # O(1)
        print(f"session: {slug} is not the active session; nothing to continue", file=sys.stderr)  # O(1)
        return 2  # O(1)
    left = unmerged(host)  # O(tree)
    if left:  # O(1)
        print(f"session: unresolved conflicts remain: {' '.join(left)}", file=sys.stderr)  # O(1)
        return 2  # O(1)
    if row.get("has_stash"):  # O(1)
        drop_stashes(host, session_stashes(host, row)[:1])  # O(stash)
    row.update(has_stash=False, stash_oid=None)  # O(1)
    write_reg(root, reg)  # O(N)
    print(f"session: active {slug} on {row.get('branch')} in {host.as_posix()} (restore complete)")  # O(1)
    return 0  # O(1)


def owned_worktree(root: Path, row: dict) -> bool:  # O(1)
    """Only checkouts superlite cut itself may be removed by discard; never the hub, the host, or a kept-in-place run."""
    wt = row.get("worktree")  # O(1)
    if not wt or row.get("owned") is False:  # O(1)
        return False  # O(1)
    p = Path(wt).resolve()  # O(1)
    if p in (root.resolve(), (root / HOST_REL).resolve()):  # O(1)
        return False  # O(1)
    return bool(row.get("owned")) or p.is_relative_to((root / SESSIONS_REL).resolve())  # O(1)


def cmd_discard(a) -> int:  # O(tree)
    slug = validate_slug(a.slug)  # O(1)
    root = hub_root(start_dir(a))  # O(1)
    with registry_lock(root):  # O(1) + wait
        reg = read_reg(root)  # O(file)
        row = find_row(reg, slug)  # O(N)
        if not row:  # O(1)
            print(f"session: no registered session {slug}", file=sys.stderr)  # O(1)
            return 2  # O(1)
        branch = row.get("branch") or branch_for(slug)  # O(1)
        host = host_path(root, reg)  # O(1)
        on_host = same(row.get("worktree"), host)  # O(1)

        if on_host and host.is_dir():  # O(1)
            if reg.get("active") == slug:  # O(1)
                if in_progress(host):  # O(1)
                    print("session: merge/rebase in progress; finish or abort it before discard", file=sys.stderr)  # O(1)
                    return 2  # O(1)
                git("reset", "-q", "--hard", cwd=host)  # O(tree)
                git("clean", "-fdq", cwd=host)  # O(tree)
                clear_run_state(host / ".superlite")  # O(S)
                git("switch", "-q", "--detach", cwd=host)  # O(1)
                reg["active"] = None  # O(1)
            if row.get("has_stash"):  # O(1)
                drop_stashes(host, session_stashes(host, row))  # O(stash)
        elif owned_worktree(root, row) and Path(row["worktree"]).is_dir():  # O(1)
            git("worktree", "remove", "--force", row["worktree"], cwd=root, check=False)  # O(tree)
            git("worktree", "prune", cwd=root, check=False)  # O(1)

        store = store_path(root, slug)  # O(1)
        if store.exists():  # O(1)
            shutil.rmtree(store, ignore_errors=True)  # O(S)
        if not a.keep_branch and branch_owner(root, branch) is None:  # O(W)
            git("branch", "-D", branch, cwd=root, check=False)  # O(1)

        reg["sessions"] = [s for s in reg["sessions"] if s.get("slug") != slug]  # O(N)
        write_reg(root, reg)  # O(N)
    print(f"session: discarded {slug}")  # O(1)
    return 0  # O(1)


def rebind_checkpoints(sl: Path, old_branch: str, new_branch: str) -> None:  # O(file)
    f = sl / "checkpoints.json"  # O(1)
    try:  # O(1)
        st = json.loads(f.read_text(encoding="utf-8"))  # O(file)
    except (OSError, ValueError):  # O(1)
        return  # O(1)
    if isinstance(st, dict) and st.get("branch") == f"refs/heads/{old_branch}":  # O(1)
        st["branch"] = f"refs/heads/{new_branch}"  # O(1)
        f.write_text(json.dumps(st, indent=1) + "\n", encoding="utf-8")  # O(file)


def cmd_split(a) -> int:  # O(S) + trees
    """Parallel exception: keep the open run in its own worktree; cut a new worktree+branch for --new."""
    start = start_dir(a)  # O(1)
    here = toplevel(start)  # O(1)
    root = hub_root(start)  # O(1)
    sl = here / ".superlite"  # O(1)
    if not (sl / ORCH).is_file():  # O(1)
        print("session: no open run (.superlite/orchestrator.json missing); nothing to move", file=sys.stderr)  # O(1)
        return 2  # O(1)

    cur = current_branch(here)  # O(1)
    if cur == "HEAD":  # O(1)
        print("session: detached HEAD; check out a branch before splitting", file=sys.stderr)  # O(1)
        return 2  # O(1)

    new_slug = validate_slug(a.new, "--new")  # O(1)
    keep_slug = validate_slug(a.keep or slug_from_branch(cur), "--keep")  # O(1)
    if keep_slug == new_slug:  # O(1)
        print("session: --keep and --new must differ", file=sys.stderr)  # O(1)
        return 2  # O(1)

    with registry_lock(root):  # O(1) + wait
        reg = read_reg(root)  # O(file)
        base = a.base or reg.get("base") or default_base(root)  # O(1)
        if git("rev-parse", "--verify", "-q", f"{base}^{{commit}}", cwd=root, check=False).returncode != 0:  # O(1)
            print(f"session: unknown --base {base}", file=sys.stderr)  # O(1)
            return 2  # O(1)
        base_owner = branch_owner(root, base) if branch_exists(root, base) else None  # O(W)
        # Leave the run where it is when this checkout is a linked worktree or base is busy elsewhere.
        in_place = here != root or (cur != base and base_owner is not None and not same(base_owner, here))  # O(1)
        keep_branch = cur if (cur != base or in_place) else branch_for(keep_slug)  # O(1)
        new_branch = branch_for(new_slug)  # O(1)
        wt_keep = here if in_place else root / SESSIONS_REL / keep_slug  # O(1)
        wt_new = root / SESSIONS_REL / new_slug  # O(1)
        if a.legacy_paths:  # O(1)
            if not in_place:  # O(1)
                wt_keep = root / ".superlite" / "worktrees" / keep_slug  # O(1)
                if cur == base:  # O(1)
                    keep_branch = f"superlite/{keep_slug}"  # O(1)
            wt_new = root / ".superlite" / "worktrees" / new_slug  # O(1)
            new_branch = f"superlite/{new_slug}"  # O(1)

        owner = branch_owner(root, new_branch)  # O(W)
        if owner:  # O(1)
            return print_redirect(owner, new_slug, new_branch)  # O(1)
        for slug in (keep_slug, new_slug):  # O(1)
            row = find_row(reg, slug)  # O(N)
            if row and not (slug == keep_slug and same(row.get("worktree"), wt_keep)):  # O(1)
                print(f"session: session {slug} already exists; pick another slug", file=sys.stderr)  # O(1)
                return 2  # O(1)
        if (not in_place and wt_keep.exists()) or wt_new.exists():  # O(1)
            print(f"session: worktree path already exists ({wt_new if wt_new.exists() else wt_keep})", file=sys.stderr)  # O(1)
            return 2  # O(1)
        if branch_exists(root, new_branch):  # O(1)
            print(f"session: branch {new_branch} already exists", file=sys.stderr)  # O(1)
            return 2  # O(1)
        if not in_place and cur == base and branch_exists(root, keep_branch):  # O(1)
            print(f"session: branch {keep_branch} already exists; pass --keep with another slug", file=sys.stderr)  # O(1)
            return 2  # O(1)
        if not in_place and in_progress(here):  # O(1)
            print("session: merge/rebase in progress; finish it before split", file=sys.stderr)  # O(1)
            return 2  # O(1)

        gitignore.ensure(str(root), [])  # O(g)
        undo: list = []  # O(1)
        try:  # O(1)
            if not in_place:  # O(S)
                _split_move(root, sl, cur, base, keep_branch, wt_keep, undo)  # O(S)
            wt_new.parent.mkdir(parents=True, exist_ok=True)  # O(1)
            git("worktree", "add", "-q", "-b", new_branch, str(wt_new), base, cwd=root)  # O(tree)
            undo.append(lambda: (git("worktree", "remove", "--force", str(wt_new), cwd=root, check=False),
                                 git("branch", "-D", new_branch, cwd=root, check=False)))  # O(1)

            reg["base"] = base  # O(1)
            upsert_row(reg, {"slug": keep_slug, "branch": keep_branch, "worktree": wt_keep.as_posix(),
                             "store": store_path(root, keep_slug).as_posix(), "has_stash": False,
                             "status": "parallel", "owned": not in_place, "role": "previous",
                             "topic": keep_slug})  # O(N)
            upsert_row(reg, {"slug": new_slug, "branch": new_branch, "worktree": wt_new.as_posix(),
                             "store": store_path(root, new_slug).as_posix(), "has_stash": False,
                             "status": "parallel", "owned": True, "role": "new", "topic": new_slug})  # O(N)
            write_reg(root, reg)  # O(N)
        except Exception as e:  # O(1)
            print(f"session: split failed: {e}; rolling back", file=sys.stderr)  # O(1)
            for step in reversed(undo):  # O(steps)
                try:  # O(1)
                    step()  # O(S)
                except Exception as ue:  # O(1)
                    print(f"session: rollback step failed: {ue}", file=sys.stderr)  # O(1)
            return 1  # O(1)

    print(f"session: previous {keep_slug} -> {wt_keep.as_posix()} on {keep_branch}")  # O(1)
    print(f"session: new {new_slug} -> {wt_new.as_posix()} on {new_branch}")  # O(1)
    if in_place:  # O(1)
        print(f"session: the open run stays in {wt_keep.as_posix()}; start the new request in the new worktree")  # O(1)
    else:  # O(1)
        print(f"session: main checkout on {base} has no open run; resume in the previous worktree, "
              f"start the new request in the new one")  # O(1)
    return 0  # O(1)


def _split_move(root: Path, sl: Path, cur: str, base: str, keep_branch: str, wt_keep: Path, undo: list) -> None:  # O(S)
    """Move the hub's open run and WIP into a keep worktree; every step pushes its inverse onto undo."""
    tmp = Path(tempfile.mkdtemp(prefix="superlite-session-"))  # O(1)
    held = tmp / "run"  # O(1)
    move_run_state(sl, held)  # O(S)
    undo.append(lambda: (move_run_state(held, sl), shutil.rmtree(tmp, ignore_errors=True)))  # O(1)

    wip = {"oid": stash_push(root, "superlite-session-split") if is_dirty(root) else None, "in_keep": False}  # O(diff)

    def restore_wip():  # O(diff)
        if not restore_stash(root, wip["oid"]):  # O(diff)
            print(f"session: uncommitted work kept in stash {wip['oid']}; apply it by hand", file=sys.stderr)  # O(1)
    undo.append(restore_wip)  # O(1)

    if cur == base:  # O(1)
        git("branch", keep_branch, cwd=root)  # O(1)
        undo.append(lambda: git("branch", "-D", keep_branch, cwd=root, check=False))  # O(1)
    else:  # O(1)
        git("checkout", "-q", base, cwd=root)  # O(tree)
        undo.append(lambda: git("checkout", "-q", cur, cwd=root))  # O(tree)

    wt_keep.parent.mkdir(parents=True, exist_ok=True)  # O(1)
    git("worktree", "add", "-q", str(wt_keep), keep_branch, cwd=root)  # O(tree)

    def remove_keep():  # O(S + tree)
        move_run_state(wt_keep / ".superlite", held)  # O(S)
        if wip["in_keep"] and is_dirty(wt_keep):  # O(tree)
            wip["oid"] = stash_push(wt_keep, "superlite-session-split")  # O(diff)
        git("worktree", "remove", "--force", str(wt_keep), cwd=root, check=False)  # O(tree)
    undo.append(remove_keep)  # O(1)

    (wt_keep / ".superlite").mkdir(parents=True, exist_ok=True)  # O(1)
    move_run_state(held, wt_keep / ".superlite")  # O(S)
    if cur == base:  # O(1)
        rebind_checkpoints(wt_keep / ".superlite", base, keep_branch)  # O(file)
    if wip["oid"]:  # O(1)
        if not restore_stash(wt_keep, wip["oid"]):  # O(diff)
            raise GitError("could not restore uncommitted work in the keep worktree")  # O(1)
        wip["in_keep"] = True  # O(1)
    shutil.rmtree(tmp, ignore_errors=True)  # O(1)


def main(argv=None) -> int:  # O(1)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])  # O(1)
    sp = ap.add_subparsers(dest="cmd", required=True)  # O(1)

    c = sp.add_parser("create", help="create a session branch (host worktree, or --worktree parallel)")  # O(1)
    c.add_argument("--slug", required=True)  # O(1)
    c.add_argument("--base")  # O(1)
    c.add_argument("--worktree", action="store_true", help="parallel: dedicated worktree + new branch")  # O(1)
    c.add_argument("--root")  # O(1)

    u = sp.add_parser("use", help="switch active session (park/stash) or REDIRECT if branch is live elsewhere")  # O(1)
    u.add_argument("--slug", required=True)  # O(1)
    u.add_argument("--continue", dest="continue_apply", action="store_true",
                   help="after resolving a restore conflict: drop the applied stash")  # O(1)
    u.add_argument("--root")  # O(1)

    s = sp.add_parser("split", help="parallel: keep the open run in a worktree and cut a new one")  # O(1)
    s.add_argument("--new", required=True)  # O(1)
    s.add_argument("--keep")  # O(1)
    s.add_argument("--base")  # O(1)
    s.add_argument("--root")  # O(1)
    s.add_argument("--legacy-paths", action="store_true", help=argparse.SUPPRESS)  # O(1)

    d = sp.add_parser("discard", help="drop session WIP/store/stash/branch; remove its parallel worktree")  # O(1)
    d.add_argument("--slug", required=True)  # O(1)
    d.add_argument("--keep-branch", action="store_true")  # O(1)
    d.add_argument("--root")  # O(1)

    t = sp.add_parser("status", help="list registered sessions")  # O(1)
    t.add_argument("--root")  # O(1)
    t.add_argument("--json", action="store_true")  # O(1)

    a = ap.parse_args(argv)  # O(1)
    sys.stdout.reconfigure(encoding="utf-8")  # O(1)
    try:  # O(1)
        return {"create": cmd_create, "use": cmd_use, "split": cmd_split, "discard": cmd_discard,
                "status": cmd_status}[a.cmd](a)  # O(1)
    except GitError as e:  # O(1)
        print(f"session: {e}", file=sys.stderr)  # O(1)
        return 1  # O(1)


if __name__ == "__main__":  # O(1)
    sys.exit(main())  # O(1)
