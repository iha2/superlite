"""Session registry and the git/run-state helpers every session command shares. Stdlib only.

The registry is `<hub>/.superlite/sessions.json`; the hub is the main checkout, which is also the
host of the active session. Every write goes through `update` (lock, read, change, atomic write);
`registry_lock` serializes a whole command (its git steps and its registry change) across processes.
"""
import re
import shutil
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import base  # noqa: E402
import ident  # noqa: E402
import state  # noqa: E402
from journal import Journal  # noqa: E402

GitError = base.GitError
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
RESERVED = {"host"}
FILE = "sessions.json"
LOCK = "sessions.lock"
ORCH = "orchestrator.json"
STORE = "session-store"
EXIT_REDIRECT = 3
STASH_PREFIX = "superlite-session:"
# Hub-level entries and real git checkouts: never parked, moved or wiped with run state.
STATIONARY = {"worktrees", "sandboxes", STORE, FILE, FILE + ".lock", LOCK}


def git(*args, cwd, check=True):
    """base.git, looked up at call time (one seam for every session git call)."""
    return base.git(*args, cwd=cwd, check=check)


# ---- identity of the repo and its branches

def toplevel(path: Path) -> Path:
    return Path(git("rev-parse", "--show-toplevel", cwd=path).stdout.strip()).resolve()


def hub_root(path: Path) -> Path:
    """Main checkout of the repository containing `path` (where sessions.json lives)."""
    return state.hub_root(path).resolve()


def start_dir(a) -> Path:
    return Path(a.root).resolve() if getattr(a, "root", None) else Path.cwd()


def same(a, b) -> bool:
    return bool(a) and bool(b) and ident.same_path(Path(a), Path(b))


def resolve_host(root: Path, reg: dict) -> Path:
    """The host of the active session is the main checkout."""
    return root


def branch_exists(root: Path, branch: str) -> bool:
    return git("rev-parse", "--verify", "-q", f"refs/heads/{branch}", cwd=root, check=False).returncode == 0


def current_branch(cwd: Path) -> str:
    return git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd).stdout.strip()


def default_base(root: Path) -> str:
    for name in ("main", "master"):
        if branch_exists(root, name):
            return name
    return current_branch(root)


def is_dirty(cwd: Path) -> bool:  # O(tree)
    return bool(git("status", "--porcelain", cwd=cwd, check=False).stdout.strip())


def validate_slug(name: str, flag: str = "--slug") -> str:
    if not SLUG.match(name or ""):
        print(f"session: {flag} must be a short slug (a-z, 0-9, -)", file=sys.stderr)
        sys.exit(2)
    if name in RESERVED:
        print(f"session: {flag} {name!r} is reserved", file=sys.stderr)
        sys.exit(2)
    return name


def branch_for(slug: str) -> str:
    return f"superlite/sessions/{slug}"


def stash_msg(slug: str) -> str:
    return f"{STASH_PREFIX}{slug}"


def slug_from_branch(branch: str) -> str:
    b = branch.strip()
    if b.startswith("superlite/sessions/"):
        b = b[len("superlite/sessions/"):]
    elif b.startswith("superlite/"):
        b = b[len("superlite/"):]
    b = re.sub(r"[^a-z0-9-]+", "-", b.lower()).strip("-")
    return b if SLUG.match(b or "") and b not in RESERVED else "current"


def sessions_dir(root: Path) -> Path:
    return state.path(root, "worktrees/sessions")


def store_path(root: Path, slug: str) -> Path:
    return state.path(root, f"{STORE}/{slug}")


def now_ms() -> int:
    return int(time.time() * 1000)


def worktree_map(root: Path) -> dict[str, str]:  # O(W)
    """branch -> worktree path for local checkouts."""
    out = {}
    text = git("worktree", "list", "--porcelain", cwd=root, check=False).stdout
    path, branch = None, None
    for ln in text.splitlines() + [""]:
        if ln.startswith("worktree "):
            path = ln[len("worktree "):]
        elif ln.startswith("branch "):
            ref = ln[len("branch "):]
            branch = ref[11:] if ref.startswith("refs/heads/") else ref
        elif ln == "" and path and branch:
            out[branch] = path
            path, branch = None, None
        elif ln == "":
            path, branch = None, None
    return out


def branch_owner(root: Path, branch: str) -> str | None:  # O(W)
    return worktree_map(root).get(branch)


def print_redirect(path: str, slug: str, branch: str) -> int:
    print(f"REDIRECT worktree={path} slug={slug} branch={branch}")
    print(f"session: branch {branch} is live in {path} — open/cd that worktree (state matches there)",
          file=sys.stderr)
    return EXIT_REDIRECT


def session_line(path, slug: str, branch: str) -> None:
    print(f"SESSION worktree={Path(path).as_posix()} slug={slug} branch={branch}")


def in_progress(cwd: Path) -> bool:
    gitdir = Path(git("rev-parse", "--git-dir", cwd=cwd).stdout.strip())
    if not gitdir.is_absolute():
        gitdir = (cwd / gitdir).resolve()
    return any((gitdir / n).exists() for n in ("MERGE_HEAD", "rebase-merge", "rebase-apply"))


def hide_superlite(root: Path) -> None:
    """Keep .superlite/ out of `git status` via .git/info/exclude, never by editing a tracked .gitignore."""
    common = git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=root).stdout.strip()
    f = Path(common) / "info" / "exclude"
    f.parent.mkdir(parents=True, exist_ok=True)
    text = f.read_text(encoding="utf-8") if f.is_file() else ""
    if any(ln.strip() in (f"{state.DIR_NAME}/", f"/{state.DIR_NAME}/") for ln in text.splitlines()):
        return
    with f.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(("" if not text or text.endswith("\n") else "\n") + f"{state.DIR_NAME}/\n")


# ---- the registry file

def _normalize(d) -> dict:
    d = d if isinstance(d, dict) else {}
    sessions = d.get("sessions") if isinstance(d.get("sessions"), list) else []
    out = []
    for s in sessions:
        if isinstance(s, dict) and s.get("slug"):
            out.append({**s, "branch": s.get("branch") or branch_for(s["slug"]),
                        "status": s.get("status") or "parked"})
    reg = {"v": 2, "base": d.get("base"), "active": d.get("active"), "sessions": out,
           "host_worktree": d.get("host_worktree")}
    if d.get("untracked") in ("ask", "stash", "keep"):
        reg["untracked"] = d["untracked"]
    return reg


def _path(root: Path) -> Path:
    return state.path(root, FILE)


def read(root: Path) -> dict:  # O(file)
    """The registry with every field present ({} on disk reads as no sessions)."""
    return _normalize(base.read_json(_path(root), {}))


def update(root: Path, fn) -> dict:  # O(file)
    """Apply fn(registry) -> registry under the file lock and write the result atomically."""
    def apply(raw):
        reg = _normalize(raw)
        new = fn(reg)
        return _normalize(reg if new is None else new)
    return base.locked_update(_path(root), apply)


def write(root: Path, reg: dict) -> None:
    update(root, lambda _old: reg)


@contextmanager
def registry_lock(root: Path):
    """Serialize a whole command (its git transitions and its registry change) across processes."""
    lock_path = state.path(root, LOCK)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+") as fh:
        base._lock(fh)
        try:
            yield
        finally:
            base._unlock(fh)


def find_row(reg: dict, slug: str) -> dict | None:  # O(N)
    return next((s for s in reg["sessions"] if s.get("slug") == slug), None)


def upsert_row(reg: dict, row: dict) -> dict:  # O(N)
    for i, s in enumerate(reg["sessions"]):
        if s.get("slug") == row["slug"]:
            reg["sessions"][i] = {**s, **row}
            return reg["sessions"][i]
    reg["sessions"].append(row)
    return row


def is_open(row: dict | None) -> bool:
    return bool(row) and row.get("run", "open") != "finished"


def set_run(row: dict, run: str) -> None:
    row["run"] = run
    if run == "finished":
        row["finished_at"] = now_ms()
    else:
        row.pop("finished_at", None)


def row_at(reg: dict, here: Path, host: Path) -> dict | None:  # O(N)
    """The session row whose checkout is `here` (for the host: the active one)."""
    if same(here, host):
        return find_row(reg, reg["active"]) if reg.get("active") else None
    return next((s for s in reg["sessions"] if same(s.get("worktree"), here)), None)


def mark_run(cwd: Path, run: str) -> str | None:  # O(N)
    """Set run open|finished on the session row for the checkout containing cwd; slug, or None. Never raises."""
    try:
        here = toplevel(Path(cwd))
        root = hub_root(here)
        if not _path(root).is_file():
            return None
        with registry_lock(root):
            reg = read(root)
            row = row_at(reg, here, resolve_host(root, reg))
            if row is None:
                return None
            slug = row["slug"]
            update(root, lambda r: set_run(find_row(r, slug), run))
            return slug
    except GitError:  # not a repo (or no git): there is no session to mark
        return None
    except (OSError, ValueError, KeyError, TypeError) as e:  # an unreadable or malformed registry must not fail finish
        print(f"session: mark_run skipped: {e!r}", file=sys.stderr)
        return None


def free_slug(root: Path, reg: dict, slug: str) -> str:  # O(N * k)
    cand, n, stem = slug, 1, slug
    while find_row(reg, cand) or branch_exists(root, branch_for(cand)) or (sessions_dir(root) / cand).exists():
        n += 1
        stem = slug[:41 - len(str(n)) - 1].rstrip("-") or slug[:1]  # keep stem-N within the slug limit
        cand = f"{stem}-{n}"
    return cand


# ---- base commits

def sha_of(root: Path, ref: str | None) -> str:
    """Commit sha of ref, or empty text when it does not resolve."""
    if not ref:
        return ""
    cp = git("rev-parse", "--verify", "-q", f"{ref}^{{commit}}", cwd=root, check=False)
    return cp.stdout.strip() if cp.returncode == 0 else ""


def row_base(root: Path, reg: dict, row: dict) -> str:
    """The base branch the row was cut from; rows without one fall back to the registry base."""
    return row.get("base") or reg.get("base") or default_base(root)


def base_now(root: Path, reg: dict) -> dict:
    """Row fields recording the registry base the row is cut from and its current sha."""
    name = reg.get("base") or default_base(root)
    sha = sha_of(root, name)
    return {"base": name, "base_sha": sha} if sha else {"base": name}


def base_row(root: Path, reg: dict, existing: str | None = None) -> dict:
    """Row base fields: merge-base(existing branch, base) for an adopted branch, else the current base sha."""
    if not existing:
        return base_now(root, reg)
    name = reg.get("base") or default_base(root)
    cp = git("merge-base", existing, name, cwd=root, check=False)
    sha = cp.stdout.strip() if cp.returncode == 0 else ""
    return {"base": name, "base_sha": sha} if sha else base_now(root, reg)


def base_sha_of(root: Path, reg: dict, row: dict) -> str:
    """The row's recorded base commit; a missing one is filled from `git merge-base <branch> <base>` and stored
    on the row (the caller holds the registry lock and writes it)."""
    if row.get("base_sha"):
        return row["base_sha"]
    cp = git("merge-base", row.get("branch") or branch_for(row["slug"]), row_base(root, reg, row),
             cwd=root, check=False)
    sha = cp.stdout.strip() if cp.returncode == 0 else ""
    if sha:
        row["base_sha"] = sha
    return sha


def behind_base(root: Path, reg: dict, row: dict, cur: str | None = None) -> int:
    """Commits the local base gained since the row's base_sha; 0 when unknown."""
    name, bsha = row.get("base") or reg.get("base"), row.get("base_sha")
    if not name or not bsha:
        return 0
    cur = sha_of(root, f"refs/heads/{name}") if cur is None else cur
    if not cur or cur == bsha:
        return 0
    n = git("rev-list", "--count", f"{bsha}..{cur}", cwd=root, check=False).stdout.strip()
    return int(n) if n.isdigit() else 0


# ---- run files (the live .superlite folder of a checkout)

def run_entries(live_sl: Path) -> list[Path]:  # O(entries)
    if not live_sl.is_dir():
        return []
    return [p for p in live_sl.iterdir() if p.name not in STATIONARY]


def move_run_state(src_sl: Path, dest: Path, moved: list | None = None) -> None:  # O(S)
    """Move run files (not STATIONARY checkouts/registry) from src_sl into dest."""
    dest.mkdir(parents=True, exist_ok=True)
    for p in run_entries(src_sl):
        target = dest / p.name
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()
        shutil.move(str(p), str(target))
        if moved is not None:
            moved.append((p, target))


def clear_run_state(live_sl: Path) -> None:  # O(S)
    for p in run_entries(live_sl):
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)


def park_run_state(live_sl: Path, store: Path) -> None:  # O(S)
    if store.exists():
        shutil.rmtree(store)
    move_run_state(live_sl, store)


def restore_run_state(store: Path, live_sl: Path) -> None:  # O(S)
    clear_run_state(live_sl)
    live_sl.mkdir(parents=True, exist_ok=True)
    if not store.is_dir():
        return
    for p in run_entries(store):
        target = live_sl / p.name
        if p.is_dir() and not p.is_symlink():
            shutil.copytree(p, target, symlinks=True)
        else:
            shutil.copy2(p, target, follow_symlinks=False)
    shutil.rmtree(store, ignore_errors=True)


# ---- stashes (matched by object id, else by exact message)

def stash_entries(cwd: Path) -> list[tuple[str, str, str]]:  # O(stash)
    """(ref, oid, message) for every stash, newest first."""
    out = []
    text = git("stash", "list", "--format=%gd%x1f%H%x1f%gs", cwd=cwd, check=False).stdout
    for ln in text.splitlines():
        parts = ln.split("\x1f")
        if len(parts) != 3:
            continue
        ref, oid, subject = parts
        # %gs is "On <branch>: <message>" (or "WIP on <branch>: ..." for message-less stashes)
        out.append((ref, oid, subject.split(": ", 1)[1] if ": " in subject else subject))
    return out


def session_stashes(cwd: Path, row: dict) -> list[str]:  # O(stash)
    """Stash refs that belong to this session row: its recorded object id, else its exact message."""
    oid = row.get("stash_oid")
    msg = row.get("stash_message") or stash_msg(row["slug"])
    return [ref for ref, o, m in stash_entries(cwd) if (o == oid if oid else m == msg)]


def drop_stashes(cwd: Path, refs: list[str]) -> None:  # O(k)
    def index(ref: str) -> int:
        m = re.search(r"\{(\d+)\}", ref)
        return int(m.group(1)) if m else -1
    for ref in sorted(refs, key=index, reverse=True):
        git("stash", "drop", "-q", ref, cwd=cwd, check=False)


def stash_push(cwd: Path, message: str, untracked: bool = True) -> str:  # O(diff)
    git("stash", "push", "-q", *(["-u"] if untracked else []), "-m", message, cwd=cwd)
    return git("rev-parse", "refs/stash", cwd=cwd).stdout.strip()


def stash_apply(cwd: Path, ref: str) -> bool:  # O(diff)
    """Apply with the index restored; fall back to a plain apply when the index cannot be rebuilt."""
    if git("stash", "apply", "-q", "--index", ref, cwd=cwd, check=False).returncode == 0:
        return True
    if is_dirty(cwd):
        return False
    return git("stash", "apply", "-q", ref, cwd=cwd, check=False).returncode == 0


def restore_stash(cwd: Path, oid: str | None) -> bool:  # O(diff)
    """Apply the stash with object id `oid` in cwd and drop it; False (stash kept) when it does not apply."""
    if not oid:
        return True
    ref = next((r for r, o, _ in stash_entries(cwd) if o == oid), None)
    if ref is None or not stash_apply(cwd, ref):
        return False
    drop_stashes(cwd, [ref])
    return True


def unmerged(cwd: Path) -> list[str]:  # O(tree)
    return git("diff", "--name-only", "--diff-filter=U", cwd=cwd, check=False).stdout.split()


def owned_worktree(root: Path, row: dict) -> bool:
    """Only checkouts superlite cut itself may be removed; never the hub or a kept-in-place run."""
    wt = row.get("worktree")
    if not wt or row.get("owned") is False or same(wt, root):
        return False
    return bool(row.get("owned")) or ident.within(Path(wt), sessions_dir(root))


# ---- untracked files: ask once, before any change

def untracked_files(host: Path) -> list[str]:  # O(tree)
    return git("ls-files", "--others", "--exclude-standard", cwd=host, check=False).stdout.splitlines()


def paths_text(files: list[str]) -> str:
    more = len(files) - 5
    return ", ".join(files[:5]) + (f" (+{more} more)" if more > 0 else "")


def blocked_by(host: Path, files: list[str], target: str) -> list[str]:  # O(tree)
    """Untracked files git would refuse to overwrite when switching to `target` (same path, or file/dir clash)."""
    tree = git("ls-tree", "-r", "--name-only", target, cwd=host, check=False).stdout.splitlines()
    paths, dirs = set(tree), set()
    for t in tree:
        parts = t.split("/")
        dirs.update("/".join(parts[:k]) for k in range(1, len(parts)))
    return [f for f in files
            if f in paths or f in dirs or any("/".join(f.split("/")[:k]) in paths for k in range(1, len(f.split("/"))))]


def untracked_gate(host: Path, slug: str, mode: str | None, target: str) -> tuple[int | None, str | None, bool]:  # O(tree)
    """Before a park: (stop_rc, note line, keep). Nothing is changed here; ask/keep-conflict stop the command."""
    files = untracked_files(host)
    if not files:
        return None, None, False
    mode = mode or "ask"
    if mode == "ask":
        print(f"ASK untracked={len(files)} slug={slug}: {paths_text(files)}")
        print(f"ask: {len(files)} untracked file(s) would be parked with session {slug}: stash them with the "
              f"session, or keep them in place? Options: stash | keep")
        print("session: rerun with --untracked stash|keep, or set it with superlite-cli session untracked stash|keep")
        return 4, None, False
    if mode == "stash":
        return None, f"session: stashed {len(files)} untracked file(s) with {slug}: {paths_text(files)}", False
    clash = blocked_by(host, files, target)
    if clash:
        print(f"session: untracked file(s) would be overwritten by {target}: {paths_text(clash)}; "
              f"nothing changed", file=sys.stderr)
        return 2, None, False
    return None, f"session: kept {len(files)} untracked file(s) in place: {paths_text(files)}", True


# ---- moving the open run out of the main checkout (also used by split)

def rebind_checkpoints(live: Path, old_branch: str, new_branch: str) -> None:  # O(file)
    f = live / "checkpoints.json"
    st = base.read_json(f, None)
    if isinstance(st, dict) and st.get("branch") == f"refs/heads/{old_branch}":
        base.write_json(f, {**st, "branch": f"refs/heads/{new_branch}"})


def split_move(root: Path, sl: Path, cur: str, base: str, keep_branch: str, wt_keep: Path, j: Journal,
               keep_untracked: bool = False) -> None:
    """Move the hub's open run and WIP into a keep worktree; every step registers its inverse on `j`."""
    tmp = Path(tempfile.mkdtemp(prefix="superlite-session-"))
    held, moved = tmp / "run", []

    def undo_run_files():
        if moved or run_entries(held):
            for source, target in reversed(moved):
                if target.exists() or target.is_symlink():
                    shutil.move(str(target), str(source))
            move_run_state(held, sl)
        shutil.rmtree(tmp, ignore_errors=True)

    j.undo(undo_run_files, "run files")
    j.step("move the run files aside")
    move_run_state(sl, held, moved)

    j.step("stash the uncommitted work")
    dirty = bool(git("status", "--porcelain", "--untracked-files=no", cwd=root, check=False).stdout.strip()) \
        if keep_untracked else is_dirty(root)
    wip = {"oid": stash_push(root, "superlite-session-split", untracked=not keep_untracked) if dirty else None,
           "in_keep": False}

    def restore_wip():  # O(diff)
        if not restore_stash(root, wip["oid"]):
            print(f"session: uncommitted work kept in stash {wip['oid']}; apply it by hand", file=sys.stderr)
    j.undo(restore_wip, "stash")

    if cur == base:
        j.step(f"cut {keep_branch}")
        git("branch", keep_branch, cwd=root)
        j.undo(lambda: git("branch", "-D", keep_branch, cwd=root, check=False), "branch")
    else:
        j.step(f"switch the main checkout to {base}")
        git("checkout", "-q", base, cwd=root)
        j.undo(lambda: git("checkout", "-q", cur, cwd=root), "checkout")

    j.step("create the keep worktree")
    wt_keep.parent.mkdir(parents=True, exist_ok=True)
    git("worktree", "add", "-q", str(wt_keep), keep_branch, cwd=root)
    keep_sl = state.run_dir(wt_keep)

    def remove_keep():  # O(S + tree)
        move_run_state(keep_sl, held)
        if wip["in_keep"] and is_dirty(wt_keep):
            wip["oid"] = stash_push(wt_keep, "superlite-session-split")
        git("worktree", "remove", "--force", str(wt_keep), cwd=root, check=False)
    j.undo(remove_keep, "keep worktree")

    j.step("move the run files into the keep worktree")
    keep_sl.mkdir(parents=True, exist_ok=True)
    move_run_state(held, keep_sl)
    if cur == base:
        checkpoint_file = keep_sl / "checkpoints.json"
        if checkpoint_file.is_file():
            original = checkpoint_file.read_bytes()
            j.undo(lambda: checkpoint_file.write_bytes(original), "checkpoints")
        rebind_checkpoints(keep_sl, base, keep_branch)
    if wip["oid"]:
        j.step("restore the uncommitted work in the keep worktree")
        if not restore_stash(wt_keep, wip["oid"]):
            raise GitError(("stash apply",), "could not restore uncommitted work in the keep worktree")
        wip["in_keep"] = True
    shutil.rmtree(tmp, ignore_errors=True)
