#!/usr/bin/env python3
"""Worktree-and-branch sessions: create, use (park/stash/switch or REDIRECT), split, discard, status.

    session.py create --slug S [--base REF] [--worktree] [--root DIR]
    session.py use --slug S [--continue] [--root DIR]
    session.py split --new S [--keep S] [--base REF] [--root DIR]
    session.py discard --slug S [--keep-branch] [--root DIR]
    session.py status [--root DIR] [--json]

Default: one host worktree (`.superlite/worktrees/sessions/host`) and many session branches
(`superlite/sessions/<slug>`). `use` parks live `.superlite` into `session-store/<slug>/`, stashes
dirty project files, switches branch, restores. If the branch is already checked out elsewhere,
print REDIRECT (exit 3) instead of a second checkout. `split` / `create --worktree` cut a parallel
worktree with a new branch. Stdlib only.
"""
# Complexity notation:
#   mostly O(1) file work plus git spawns; S = size of parked .superlite; N = sessions in registry
import argparse  # O(1)
import json  # O(1)
import re  # O(1)
import shutil  # O(1)
import subprocess  # O(1)
import sys  # O(1)
import time  # O(1)
from pathlib import Path  # O(1)

HERE = Path(__file__).resolve().parent  # O(1)
if str(HERE) not in sys.path:  # O(1)
    sys.path.insert(0, str(HERE))  # O(1)

import gitignore  # noqa: E402  | O(1)

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")  # O(1)
FILE = "sessions.json"  # O(1)
ORCH = "orchestrator.json"  # O(1)
STORE = "session-store"  # O(1)
HOST_REL = Path(".superlite") / "worktrees" / "sessions" / "host"  # O(1)
EXIT_REDIRECT = 3  # O(1)
STASH_PREFIX = "superlite-session:"  # O(1)


def git(*args, cwd=None, check=True):  # O(1) + process spawn
    r = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=cwd)  # O(out)
    if check and r.returncode != 0:  # O(1)
        sys.exit(f"session: git {' '.join(args[:2])} failed: {(r.stderr or r.stdout).strip()}")  # O(1)
    return r  # O(1)


def root_of(path: Path) -> Path:  # O(1)
    return Path(git("rev-parse", "--show-toplevel", cwd=path).stdout.strip())  # O(1)


def default_base(root: Path) -> str:  # O(1)
    for name in ("main", "master"):  # O(1)
        if git("rev-parse", "--verify", "-q", f"refs/heads/{name}", cwd=root, check=False).returncode == 0:  # O(1)
            return name  # O(1)
    return git("rev-parse", "--abbrev-ref", "HEAD", cwd=root).stdout.strip()  # O(1)


def validate_slug(name: str, flag: str = "--slug") -> str:  # O(1)
    if not SLUG.match(name or ""):  # O(1)
        print(f"session: {flag} must be a short slug (a-z, 0-9, -)", file=sys.stderr)  # O(1)
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
    return b if SLUG.match(b or "") else "current"  # O(1)


def hub_sl(root: Path) -> Path:  # O(1)
    return root / ".superlite"  # O(1)


def store_path(root: Path, slug: str) -> Path:  # O(1)
    return hub_sl(root) / STORE / slug  # O(1)


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
    (d / FILE).write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")  # O(N)


def find_row(reg: dict, slug: str) -> dict | None:  # O(N)
    for s in reg["sessions"]:  # O(N)
        if s.get("slug") == slug:  # O(1)
            return s  # O(1)
    return None  # O(1)


def upsert_row(reg: dict, row: dict) -> None:  # O(N)
    for i, s in enumerate(reg["sessions"]):  # O(N)
        if s.get("slug") == row["slug"]:  # O(1)
            reg["sessions"][i] = {**s, **row}  # O(1)
            return  # O(1)
    reg["sessions"].append(row)  # O(1)


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
        elif ln == "" and path and branch:  # O(1)
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


def in_progress(root: Path, cwd: Path) -> bool:  # O(1)
    gitdir = Path(git("rev-parse", "--git-dir", cwd=cwd).stdout.strip())  # O(1)
    if not gitdir.is_absolute():  # O(1)
        gitdir = (cwd / gitdir).resolve()  # O(1)
    return (gitdir / "MERGE_HEAD").exists() or (gitdir / "rebase-merge").exists() or (gitdir / "rebase-apply").exists()  # O(1)


def park_run_state(live_sl: Path, store: Path) -> None:  # O(S)
    store.parent.mkdir(parents=True, exist_ok=True)  # O(1)
    if store.exists():  # O(1)
        shutil.rmtree(store)  # O(S)
    if not live_sl.exists():  # O(1)
        store.mkdir(parents=True, exist_ok=True)  # O(1)
        return  # O(1)
    # Keep worktrees/, session-store/, sessions.json on hub; only park run files from a host live tree.
    shutil.move(str(live_sl), str(store))  # O(S)


def restore_run_state(store: Path, live_sl: Path) -> None:  # O(S)
    if live_sl.exists():  # O(1)
        shutil.rmtree(live_sl)  # O(S)
    if store.exists() and any(store.iterdir()):  # O(1)
        shutil.move(str(store), str(live_sl))  # O(S)
    else:  # O(1)
        live_sl.mkdir(parents=True, exist_ok=True)  # O(1)
        if store.exists():  # O(1)
            shutil.rmtree(store, ignore_errors=True)  # O(1)


def host_ready(host: Path) -> bool:  # O(1)
    if not host.is_dir():  # O(1)
        return False  # O(1)
    return git("rev-parse", "--is-inside-work-tree", cwd=host, check=False).returncode == 0  # O(1)


def ensure_host(root: Path, reg: dict, base: str, first_branch: str) -> Path:  # O(tree)
    """Return host worktree path, creating it on first_branch if missing."""
    host = Path(reg["host_worktree"]) if reg.get("host_worktree") else root / HOST_REL  # O(1)
    if host_ready(host):  # O(1)
        reg["host_worktree"] = host.as_posix()  # O(1)
        return host  # O(1)
    host.parent.mkdir(parents=True, exist_ok=True)  # O(1)
    gitignore.ensure(str(root), [])  # O(g)
    if git("rev-parse", "--verify", "-q", f"refs/heads/{first_branch}", cwd=root, check=False).returncode == 0:  # O(1)
        owner = branch_owner(root, first_branch)  # O(W)
        if owner:  # O(1)
            reg["host_worktree"] = str(Path(owner))  # O(1)
            return Path(owner)  # O(1)
        git("worktree", "add", str(host), first_branch, cwd=root)  # O(tree)
    else:  # O(1)
        git("worktree", "add", "-b", first_branch, str(host), base, cwd=root)  # O(tree)
    reg["host_worktree"] = host.as_posix()  # O(1)
    return host  # O(1)


def cmd_status(a) -> int:  # O(N)
    root = Path(a.root).resolve() if a.root else root_of(Path.cwd())  # O(1)
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
    root = Path(a.root).resolve() if a.root else root_of(Path.cwd())  # O(1)
    slug = validate_slug(a.slug)  # O(1)
    base = a.base or default_base(root)  # O(1)
    branch = branch_for(slug)  # O(1)
    reg = read_reg(root)  # O(file)
    reg["base"] = base  # O(1)
    gitignore.ensure(str(root), [])  # O(g)

    owner = branch_owner(root, branch)  # O(W)
    if owner:  # O(1)
        return print_redirect(owner, slug, branch)  # O(1)

    if a.worktree:  # parallel: dedicated worktree + new branch | O(tree)
        wt = root / ".superlite" / "worktrees" / "sessions" / slug  # O(1)
        if wt.exists():  # O(1)
            print(f"session: worktree path already exists ({wt})", file=sys.stderr)  # O(1)
            return 2  # O(1)
        wt.parent.mkdir(parents=True, exist_ok=True)  # O(1)
        git("worktree", "add", "-b", branch, str(wt), base, cwd=root)  # O(tree)
        (wt / ".superlite").mkdir(parents=True, exist_ok=True)  # O(1)
        upsert_row(reg, {"slug": slug, "branch": branch, "worktree": wt.as_posix(),
                         "store": store_path(root, slug).as_posix(), "has_stash": False,
                         "status": "parallel", "created_at": int(time.time() * 1000)})  # O(N)
        write_reg(root, reg)  # O(N)
        print(f"session: created parallel {slug} -> {wt.as_posix()} on {branch}")  # O(1)
        return 0  # O(1)

    # Serial path: one host worktree, switch/create branch there
    host = ensure_host(root, reg, base, first_branch=branch)  # O(tree)
    (host / ".superlite").mkdir(parents=True, exist_ok=True)  # O(1)
    cur = git("rev-parse", "--abbrev-ref", "HEAD", cwd=host).stdout.strip()  # O(1)
    active = reg.get("active")  # O(1)
    if cur != branch:  # O(1)
        if git("rev-parse", "--verify", "-q", f"refs/heads/{branch}", cwd=root, check=False).returncode != 0:  # O(1)
            git("branch", branch, base, cwd=root)  # O(1)
        if active and active != slug:  # O(1)
            rc = _park_and_switch(root, reg, host, active, slug, branch, create_branch=False)  # O(S)
            if rc:  # O(1)
                return rc  # O(1)
            print(f"session: created {slug} -> {host.as_posix()} on {branch} (active)")  # O(1)
            return 0  # O(1)
        if in_progress(root, host):  # O(1)
            print("session: merge/rebase in progress; finish it before create", file=sys.stderr)  # O(1)
            return 2  # O(1)
        git("switch", "-q", branch, cwd=host)  # O(1)
        (host / ".superlite").mkdir(parents=True, exist_ok=True)  # O(1)

    upsert_row(reg, {"slug": slug, "branch": branch, "worktree": host.as_posix(),
                     "store": store_path(root, slug).as_posix(), "has_stash": False,
                     "status": "active", "created_at": int(time.time() * 1000)})  # O(N)
    for s in reg["sessions"]:  # O(N)
        if s["slug"] != slug and s.get("worktree") == host.as_posix() and s.get("status") == "active":  # O(1)
            s["status"] = "parked"  # O(1)
    reg["active"] = slug  # O(1)
    reg["host_worktree"] = host.as_posix()  # O(1)
    write_reg(root, reg)  # O(N)
    print(f"session: created {slug} -> {host.as_posix()} on {branch} (active)")  # O(1)
    return 0  # O(1)


def _park_and_switch(root: Path, reg: dict, host: Path, from_slug: str, to_slug: str, to_branch: str,
                     create_branch: bool, continue_apply: bool = False) -> int:  # O(S)
    if in_progress(root, host) and not continue_apply:  # O(1)
        print("session: merge/rebase in progress; finish it before use", file=sys.stderr)  # O(1)
        return 2  # O(1)

    owner = branch_owner(root, to_branch)  # O(W)
    if owner and Path(owner).resolve() != host.resolve():  # O(1)
        return print_redirect(owner, to_slug, to_branch)  # O(1)

    live = host / ".superlite"  # O(1)
    # Don't park hub registry into session store: if live contains sessions.json at hub-only layout,
    # host's .superlite is the run dir (may also hold nested worktrees only on hub).
    from_store = store_path(root, from_slug)  # O(1)
    to_store = store_path(root, to_slug)  # O(1)

    if not continue_apply:  # O(1)
        # Park only run-ish files: move entire live .superlite on host into store (host has no sessions.json usually)
        # If sessions.json lives on hub (root), host .superlite is purely the run — safe to move.
        if live.exists():  # O(1)
            park_run_state(live, from_store)  # O(S)
        else:  # O(1)
            from_store.mkdir(parents=True, exist_ok=True)  # O(1)

        dirty = bool(git("status", "--porcelain", cwd=host, check=False).stdout.strip())  # O(1)
        has_stash = False  # O(1)
        if dirty:  # O(1)
            git("stash", "push", "-u", "-m", stash_msg(from_slug), cwd=host)  # O(diff)
            has_stash = True  # O(1)
        upsert_row(reg, {"slug": from_slug, "branch": branch_for(from_slug) if find_row(reg, from_slug) is None
                         else find_row(reg, from_slug).get("branch", branch_for(from_slug)),
                         "worktree": host.as_posix(), "store": from_store.as_posix(),
                         "stash_message": stash_msg(from_slug), "has_stash": has_stash, "status": "parked"})  # O(N)

        if create_branch and git("rev-parse", "--verify", "-q", f"refs/heads/{to_branch}", cwd=root,
                                 check=False).returncode != 0:  # O(1)
            git("branch", to_branch, reg.get("base") or default_base(root), cwd=root)  # O(1)
        git("switch", "-q", to_branch, cwd=host)  # O(1)
        restore_run_state(to_store, live)  # O(S)

    row = find_row(reg, to_slug) or {"slug": to_slug, "branch": to_branch, "worktree": host.as_posix(),
                                     "store": to_store.as_posix(), "has_stash": False}  # O(N)
    if row.get("has_stash") or continue_apply:  # O(1)
        # find stash by message
        st = git("stash", "list", cwd=host, check=False).stdout  # O(stash)
        idx = None  # O(1)
        for ln in st.splitlines():  # O(stash)
            if stash_msg(to_slug) in ln:  # O(1)
                # stash@{N}: ...
                idx = ln.split(":", 1)[0].strip()  # O(1)
                break  # O(1)
        if idx:  # O(1)
            ap = git("stash", "apply", idx, cwd=host, check=False)  # O(diff)
            if ap.returncode != 0:  # O(1)
                upsert_row(reg, {**row, "status": "active", "has_stash": True})  # O(N)
                reg["active"] = to_slug  # O(1)
                write_reg(root, reg)  # O(N)
                print(f"session: stash apply conflict for {to_slug}; resolve, then "
                      f"sl session use --slug {to_slug} --continue", file=sys.stderr)  # O(1)
                return 2  # O(1)
            git("stash", "drop", idx, cwd=host, check=False)  # O(1)
            row["has_stash"] = False  # O(1)

    for s in reg["sessions"]:  # O(N)
        if s.get("worktree") == host.as_posix() and s.get("slug") != to_slug and s.get("status") == "active":  # O(1)
            s["status"] = "parked"  # O(1)
    upsert_row(reg, {**row, "slug": to_slug, "branch": to_branch, "worktree": host.as_posix(),
                     "store": to_store.as_posix(), "status": "active"})  # O(N)
    reg["active"] = to_slug  # O(1)
    write_reg(root, reg)  # O(N)
    print(f"session: active {to_slug} on {to_branch} in {host.as_posix()}")  # O(1)
    return 0  # O(1)


def cmd_use(a) -> int:  # O(S)
    root = Path(a.root).resolve() if a.root else root_of(Path.cwd())  # O(1)
    slug = validate_slug(a.slug)  # O(1)
    branch = branch_for(slug)  # O(1)
    reg = read_reg(root)  # O(file)
    owner = branch_owner(root, branch)  # O(W)
    host = Path(reg["host_worktree"]) if reg.get("host_worktree") else root / HOST_REL  # O(1)

    if owner and (not host.exists() or Path(owner).resolve() != host.resolve()):  # O(1)
        # Branch live in a parallel (or other) worktree — always redirect, never checkout here
        if not host.exists() or Path(owner).resolve() != Path(cwd_host(root, reg)).resolve():  # O(1)
            return print_redirect(owner, slug, branch)  # O(1)

    if not host.is_dir():  # O(1)
        print("session: no host worktree; run sl session create --slug <slug> first", file=sys.stderr)  # O(1)
        return 2  # O(1)

    # If target branch is owned by this host, switch with park. If owned elsewhere, redirected above.
    if owner and Path(owner).resolve() != host.resolve():  # O(1)
        return print_redirect(owner, slug, branch)  # O(1)

    active = reg.get("active")  # O(1)
    if a.continue_apply:  # O(1)
        return _park_and_switch(root, reg, host, active or slug, slug, branch, False, continue_apply=True)  # O(S)

    if active == slug and git("rev-parse", "--abbrev-ref", "HEAD", cwd=host).stdout.strip() == branch:  # O(1)
        print(f"session: already active {slug} on {branch} in {host.as_posix()}")  # O(1)
        return 0  # O(1)

    if not active:  # O(1)
        # nothing to park
        if git("rev-parse", "--verify", "-q", f"refs/heads/{branch}", cwd=root, check=False).returncode != 0:  # O(1)
            print(f"session: unknown session {slug}; run sl session create --slug {slug}", file=sys.stderr)  # O(1)
            return 2  # O(1)
        if in_progress(root, host):  # O(1)
            print("session: merge/rebase in progress; finish it before use", file=sys.stderr)  # O(1)
            return 2  # O(1)
        git("switch", "-q", branch, cwd=host)  # O(1)
        restore_run_state(store_path(root, slug), host / ".superlite")  # O(S)
        upsert_row(reg, {"slug": slug, "branch": branch, "worktree": host.as_posix(),
                         "store": store_path(root, slug).as_posix(), "has_stash": False, "status": "active"})  # O(N)
        reg["active"] = slug  # O(1)
        write_reg(root, reg)  # O(N)
        print(f"session: active {slug} on {branch} in {host.as_posix()}")  # O(1)
        return 0  # O(1)

    return _park_and_switch(root, reg, host, active, slug, branch,
                            create_branch=find_row(reg, slug) is None)  # O(S)


def cwd_host(root: Path, reg: dict) -> Path:  # O(1)
    return Path(reg["host_worktree"]) if reg.get("host_worktree") else root / HOST_REL  # O(1)


def cmd_discard(a) -> int:  # O(tree)
    root = Path(a.root).resolve() if a.root else root_of(Path.cwd())  # O(1)
    slug = validate_slug(a.slug)  # O(1)
    reg = read_reg(root)  # O(file)
    row = find_row(reg, slug)  # O(N)
    if not row:  # O(1)
        print(f"session: no registered session {slug}", file=sys.stderr)  # O(1)
        return 2  # O(1)
    branch = row.get("branch") or branch_for(slug)  # O(1)
    wt = Path(row["worktree"]) if row.get("worktree") else None  # O(1)
    host = Path(reg["host_worktree"]) if reg.get("host_worktree") else root / HOST_REL  # O(1)
    store = store_path(root, slug)  # O(1)

    if reg.get("active") == slug and wt and wt.resolve() == host.resolve() and host.is_dir():  # O(1)
        # park/clear live state before dropping
        live = host / ".superlite"  # O(1)
        if live.exists():  # O(1)
            shutil.rmtree(live, ignore_errors=True)  # O(S)
            live.mkdir(parents=True, exist_ok=True)  # O(1)
        reg["active"] = None  # O(1)

    if store.exists():  # O(1)
        shutil.rmtree(store, ignore_errors=True)  # O(S)

    # drop stash if present in host
    if host.is_dir():  # O(1)
        st = git("stash", "list", cwd=host, check=False).stdout  # O(stash)
        for ln in st.splitlines():  # O(stash)
            if stash_msg(slug) in ln:  # O(1)
                git("stash", "drop", ln.split(":", 1)[0].strip(), cwd=host, check=False)  # O(1)

    parallel = row.get("status") == "parallel" or (wt and host.is_dir() and wt.resolve() != host.resolve())  # O(1)
    if parallel and wt and wt.is_dir():  # O(1)
        git("worktree", "remove", "--force", str(wt), cwd=root, check=False)  # O(tree)
        git("worktree", "prune", cwd=root, check=False)  # O(1)

    if not a.keep_branch:  # O(1)
        # only delete branch if not checked out
        if branch_owner(root, branch) is None:  # O(W)
            git("branch", "-D", branch, cwd=root, check=False)  # O(1)

    reg["sessions"] = [s for s in reg["sessions"] if s.get("slug") != slug]  # O(N)
    write_reg(root, reg)  # O(N)
    print(f"session: discarded {slug}")  # O(1)
    return 0  # O(1)


def cmd_split(a) -> int:  # O(S) + trees
    """Parallel exception: move open run into keep worktree; create new worktree+branch for --new."""
    root = Path(a.root).resolve() if a.root else root_of(Path.cwd())  # O(1)
    sl = root / ".superlite"  # O(1)
    if not (sl / ORCH).is_file():  # O(1)
        print("session: no open run (.superlite/orchestrator.json missing); nothing to move", file=sys.stderr)  # O(1)
        return 2  # O(1)

    cur = git("rev-parse", "--abbrev-ref", "HEAD", cwd=root).stdout.strip()  # O(1)
    if cur == "HEAD":  # O(1)
        print("session: detached HEAD; check out a branch before splitting", file=sys.stderr)  # O(1)
        return 2  # O(1)

    new_slug = validate_slug(a.new, "--new")  # O(1)
    keep_slug = validate_slug(a.keep or slug_from_branch(cur), "--keep")  # O(1)
    if keep_slug == new_slug:  # O(1)
        print("session: --keep and --new must differ", file=sys.stderr)  # O(1)
        return 2  # O(1)

    base = a.base or default_base(root)  # O(1)
    keep_branch = cur if cur != base else branch_for(keep_slug)  # O(1)
    new_branch = branch_for(new_slug)  # O(1)
    # Prefer namespaced parallel paths; fall back to legacy worktrees/<slug> if tests/old callers expect it
    wt_keep = sl / "worktrees" / "sessions" / keep_slug  # O(1)
    wt_new = sl / "worktrees" / "sessions" / new_slug  # O(1)
    # Compatibility: if caller used old layout via env
    if a.legacy_paths:  # O(1)
        wt_keep = sl / "worktrees" / keep_slug  # O(1)
        wt_new = sl / "worktrees" / new_slug  # O(1)
        if keep_branch == branch_for(keep_slug) and cur == base:  # O(1)
            keep_branch = f"superlite/{keep_slug}"  # O(1)
        if new_branch == branch_for(new_slug):  # O(1)
            new_branch = f"superlite/{new_slug}"  # O(1)

    for br, label in ((new_branch, "new"),):  # O(1)
        owner = branch_owner(root, br)  # O(W)
        if owner:  # O(1)
            return print_redirect(owner, new_slug if label == "new" else keep_slug, br)  # O(1)

    if wt_keep.exists() or wt_new.exists():  # O(1)
        print(f"session: worktree path already exists ({wt_keep if wt_keep.exists() else wt_new})", file=sys.stderr)  # O(1)
        return 2  # O(1)
    if git("rev-parse", "--verify", "-q", f"refs/heads/{new_branch}", cwd=root, check=False).returncode == 0:  # O(1)
        print(f"session: branch {new_branch} already exists", file=sys.stderr)  # O(1)
        return 2  # O(1)

    gitignore.ensure(str(root), [])  # O(g)
    import tempfile  # O(1)
    tmp = Path(tempfile.mkdtemp(prefix="superlite-session-"))  # O(1)
    sl_tmp = tmp / "superlite"  # O(1)
    # Preserve existing registry if any
    old_reg = read_reg(root)  # O(file)
    shutil.move(str(sl), str(sl_tmp))  # O(S)
    stashed = False  # O(1)
    try:  # O(1)
        dirty = bool(git("status", "--porcelain", cwd=root, check=False).stdout.strip())  # O(1)
        if dirty:  # O(1)
            git("stash", "push", "-u", "-m", "superlite-session-split", cwd=root)  # O(diff)
            stashed = True  # O(1)
        if cur == base:  # O(1)
            if git("rev-parse", "--verify", "-q", f"refs/heads/{keep_branch}", cwd=root, check=False).returncode == 0:  # O(1)
                print(f"session: branch {keep_branch} already exists; pass --keep with another slug", file=sys.stderr)  # O(1)
                return _abort_split(root, sl_tmp, stashed, 2)  # O(S)
            git("branch", keep_branch, cwd=root)  # O(1)
        else:  # O(1)
            git("checkout", "-q", base, cwd=root)  # O(1)
        wt_keep.parent.mkdir(parents=True, exist_ok=True)  # O(1)
        git("worktree", "add", str(wt_keep), keep_branch, cwd=root)  # O(tree)
        shutil.move(str(sl_tmp), str(wt_keep / ".superlite"))  # O(S)
        sl_tmp = None  # O(1)
        if stashed:  # O(1)
            pop = git("stash", "pop", cwd=wt_keep, check=False)  # O(diff)
            if pop.returncode != 0:  # O(1)
                print(f"session: warning: stash pop in keep worktree failed: {(pop.stderr or pop.stdout).strip()}",
                      file=sys.stderr)  # O(1)
            stashed = False  # O(1)
        git("worktree", "add", "-b", new_branch, str(wt_new), base, cwd=root)  # O(tree)

        # Rebuild hub .superlite with registry (append, do not drop prior rows)
        hub_sl(root).mkdir(parents=True, exist_ok=True)  # O(1)
        reg = old_reg  # O(1)
        reg["base"] = base  # O(1)
        upsert_row(reg, {"slug": keep_slug, "branch": keep_branch, "worktree": wt_keep.as_posix(),
                         "store": store_path(root, keep_slug).as_posix(), "has_stash": False,
                         "status": "parallel", "role": "previous", "topic": keep_slug})  # O(N)
        upsert_row(reg, {"slug": new_slug, "branch": new_branch, "worktree": wt_new.as_posix(),
                         "store": store_path(root, new_slug).as_posix(), "has_stash": False,
                         "status": "parallel", "role": "new", "topic": new_slug})  # O(N)
        write_reg(root, reg)  # O(N)
    except SystemExit:  # O(1)
        raise  # O(1)
    except Exception as e:  # O(1)
        print(f"session: split failed: {e}", file=sys.stderr)  # O(1)
        return _abort_split(root, sl_tmp, stashed, 1)  # O(S)
    finally:  # O(1)
        if tmp.exists() and not any(tmp.iterdir()):  # O(1)
            tmp.rmdir()  # O(1)

    print(f"session: previous {keep_slug} -> {wt_keep.as_posix()} on {keep_branch}")  # O(1)
    print(f"session: new {new_slug} -> {wt_new.as_posix()} on {new_branch}")  # O(1)
    print(f"session: main checkout on {base} has no open run; resume in the previous worktree, "
          f"start the new request in the new one")  # O(1)
    return 0  # O(1)


def _abort_split(root: Path, sl_tmp: Path | None, stashed: bool, code: int) -> int:  # O(S)
    if sl_tmp and sl_tmp.is_dir() and not (root / ".superlite" / ORCH).exists():  # O(1)
        dest = root / ".superlite"  # O(1)
        if dest.exists():  # O(1)
            shutil.rmtree(dest, ignore_errors=True)  # O(S)
        shutil.move(str(sl_tmp), str(dest))  # O(S)
    if stashed:  # O(1)
        git("stash", "pop", cwd=root, check=False)  # O(diff)
    return code  # O(1)


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
    u.add_argument("--continue", dest="continue_apply", action="store_true")  # O(1)
    u.add_argument("--root")  # O(1)

    s = sp.add_parser("split", help="parallel: move open run to a worktree and cut a new one")  # O(1)
    s.add_argument("--new", required=True)  # O(1)
    s.add_argument("--keep")  # O(1)
    s.add_argument("--base")  # O(1)
    s.add_argument("--root")  # O(1)
    s.add_argument("--legacy-paths", action="store_true", help=argparse.SUPPRESS)  # O(1)

    d = sp.add_parser("discard", help="drop session store/stash/branch; remove parallel worktree")  # O(1)
    d.add_argument("--slug", required=True)  # O(1)
    d.add_argument("--keep-branch", action="store_true")  # O(1)
    d.add_argument("--root")  # O(1)

    t = sp.add_parser("status", help="list registered sessions")  # O(1)
    t.add_argument("--root")  # O(1)
    t.add_argument("--json", action="store_true")  # O(1)

    a = ap.parse_args(argv)  # O(1)
    sys.stdout.reconfigure(encoding="utf-8")  # O(1)
    return {"create": cmd_create, "use": cmd_use, "split": cmd_split, "discard": cmd_discard,
            "status": cmd_status}[a.cmd](a)  # O(1)


if __name__ == "__main__":  # O(1)
    sys.exit(main())  # O(1)
