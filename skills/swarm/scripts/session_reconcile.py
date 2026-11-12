"""Reconcile sessions.json with git: worktree inventory, drift report, registry-only repair. Stdlib only.

Reads git, never changes it. `apply` returns a new registry; the caller writes it under `registry_lock`.
"""
import copy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import session_registry as sr  # noqa: E402
import ident  # noqa: E402
import state  # noqa: E402
import worktree_home  # noqa: E402

WIP_PREFIX = "refs/superlite/wip/"


def _listing(root: Path) -> list[dict]:  # O(W)
    """Every linked worktree git lists (hub excluded) whose directory exists."""
    text = sr.git("worktree", "list", "--porcelain", cwd=root, check=False).stdout
    out, cur = [], {}
    for ln in text.splitlines() + [""]:
        if ln.startswith("worktree "):
            cur = {"path": ident.git_path(ln[len("worktree "):]), "branch": None, "head": "", "detached": False}
        elif ln.startswith("HEAD ") and cur:
            cur["head"] = ln[5:]
        elif ln.startswith("branch ") and cur:
            ref = ln[7:]
            cur["branch"] = ref[11:] if ref.startswith("refs/heads/") else ref
        elif ln == "detached" and cur:
            cur["detached"] = True
        elif ln == "" and cur:
            out.append(cur)
            cur = {}
    return [w for w in out if ident.native(w["path"]).is_dir() and not sr.same(w["path"], root)]


def _row_for(reg: dict, w: dict) -> dict | None:
    rows = reg.get("sessions", [])
    return (next((r for r in rows if sr.same(r.get("worktree"), w["path"])), None)
            or next((r for r in rows if w["branch"] and r.get("branch") == w["branch"]), None))


def _kind(reg: dict, w: dict) -> tuple[str, str, str | None]:
    """(kind, owner, session slug) for a linked worktree."""
    r, br = _row_for(reg, w), w["branch"] or ""
    p = Path(w["path"]).as_posix().lower() + "/"
    harness = bool((r or {}).get("harness")) or worktree_home.harness_worktree(w["path"], br or None)
    if (r or {}).get("harness") and not (r or {}).get("owned", True):
        return "harness", "harness", r.get("slug")  # a Claude-created worktree adopted under a superlite branch
    if br.startswith("superlite/sessions/") or (r and not harness):
        return "session", "superlite", (r or {}).get("slug")
    if f"/{state.DIR_NAME}/sandboxes/" in p or br.endswith("-proto"):
        return "proto", "superlite", (r or {}).get("slug")
    if harness:  # harness hints win over "has a row": the harness owns the checkout, the row only tracks it
        return "harness", "harness", (r or {}).get("slug")
    return "unknown", "user", None


def inventory(root: Path, reg: dict) -> list[dict]:  # O(W * N)
    """One entry per linked worktree. An entry unchanged since the last inventory keeps its last_seen,
    so a second `reconcile --apply` writes the same bytes."""
    now = sr.now_ms()
    prev = {e.get("path"): e for e in reg.get("worktrees") or []}
    out = []
    for w in _listing(root):
        kind, owner, slug = _kind(reg, w)
        e = {"path": w["path"], "branch": w["branch"], "head": w["head"], "detached": w["detached"],
             "kind": kind, "owner": owner, "session": slug}
        old = prev.get(w["path"]) or {}
        same = old.get("last_seen") and all(old.get(k) == v for k, v in e.items())
        out.append({**e, "last_seen": old["last_seen"] if same else now})
    return sorted(out, key=lambda e: e["path"].lower())  # git's listing order differs between WSL and Windows


def _wip_exists(root: Path, slug: str) -> bool:
    cp = sr.git("for-each-ref", "--format=%(refname)", WIP_PREFIX + slug, cwd=root, check=False)
    return bool(cp.stdout.strip())


def _d(cls: str, slug, path, detail: str) -> dict:
    return {"class": cls, "slug": slug, "path": str(path) if path else None, "detail": detail}


def _sandbox_drift(root: Path, row: dict) -> list[dict]:
    """Row entries against the folders under <checkout>/.superlite/sandboxes/<slug>/; reads only."""
    import session_sandboxes  # lazy: optional module
    slug = row["slug"]
    out = []
    wt = row.get("worktree")
    base = ident.native(wt) if wt else root
    for e in session_sandboxes.open_entries(row) if row.get("sandboxes") else []:
        rel = e.get("path")
        if rel and not (base / rel).is_dir():
            out.append(_d("sandbox-missing", slug, rel, f"sandbox {e.get('topic')} is open but its folder is gone"))
    return out


def _sandbox_base_orphans(base: Path, known: set[str]) -> list[dict]:
    top = base / state.DIR_NAME / "sandboxes"
    out = []
    for session in sorted(top.iterdir()) if top.is_dir() else []:
        if (session / ".git").is_file():
            continue
        for folder in sorted(session.iterdir()) if session.is_dir() else []:
            if folder.is_dir() and not (folder / ".git").is_file() and str(folder.resolve()) not in known:
                out.append(_d("sandbox-orphan", session.name, folder,
                              f"sandbox folder {folder.name} has no session row entry"))
    return out


def _sandbox_orphans(root: Path, rows: list[dict]) -> list[dict]:
    bases = {root.resolve()}
    known = set()
    for row in rows:
        base = ident.native(row["worktree"]) if row.get("worktree") else root
        bases.add(base.resolve())
        entries = row.get("sandboxes")
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict) and isinstance(entry.get("path"), str):
                known.add(str((base / entry["path"]).resolve()))
    return [d for base in bases for d in _sandbox_base_orphans(base, known)]


def drift(root: Path, reg: dict) -> list[dict]:  # O(N * W)
    out = []
    rows = reg.get("sessions", [])
    at_map = sr.worktree_map(root)
    stash_oids = {o for _, o, _ in sr.stash_entries(root)}
    for row in rows:
        slug, br, wt = row["slug"], row.get("branch") or sr.branch_for(row["slug"]), row.get("worktree")
        exists, at = sr.branch_exists(root, br), at_map.get(br)
        at = at if at and ident.native(at).is_dir() else None  # git still lists a deleted worktree until prune
        wt_ok = bool(wt) and ident.native(wt).is_dir()
        if exists and at and not sr.same(at, root) and not (wt_ok and sr.same(at, wt)):
            out.append(_d("moved", slug, wt, f"branch at {at}"))
        elif wt and not wt_ok and exists and row.get("status") != "pruned":
            out.append(_d("pruned", slug, wt, f"worktree missing, branch {br} present"))
        elif not exists and not wt_ok:
            out.append(_d("gone", slug, wt, "branch and worktree missing"))
        out.extend(_sandbox_drift(root, row))
        oid = row.get("stash_oid")
        if oid and oid not in stash_oids and not _wip_exists(root, slug):
            out.append(_d("lost-wip", slug, wt, f"stash {oid[:7]} missing and no {WIP_PREFIX}{slug}"))
    out.extend(_sandbox_orphans(root, rows))
    for w in _listing(root):
        if _kind(reg, w)[0] == "session" and _row_for(reg, w) is None:
            out.append(_d("unregistered", "-", w["path"],
                          f"worktree on {w['branch'] or 'detached'} has no session row"))
    act = reg.get("active")
    if act and not any(r["slug"] == act for r in reg.get("sessions", [])):
        out.append(_d("dangling-active", act, None, "active session has no row"))
    b, dflt = reg.get("base"), sr.default_base(root)
    # a create --base used to leak one session's base into the registry; a trunk is never a superlite branch
    if b and b != dflt and not dflt.startswith("superlite/") and (
            b.startswith("superlite/") or not sr.branch_exists(root, b)):
        out.append(_d("base", "-", None, f"registry base {b} is a feature branch or missing; default is {dflt}"))
    return out


def drift_line(d: dict) -> str:
    return f"session: drift {d['class']} slug={d.get('slug') or '-'} path={d.get('path') or '-'}: {d['detail']}"


def apply(root: Path, reg: dict) -> tuple[dict, list[dict]]:  # O(N * W)
    """New registry with pruned/moved rows fixed, unrecoverable gone rows removed, `worktrees` refreshed."""
    ds = drift(root, reg)
    new = copy.deepcopy(reg)
    rows = {r["slug"]: r for r in new.get("sessions", [])}
    for d in ds:
        if d["class"] == "pruned":
            rows[d["slug"]]["status"] = "pruned"
        elif d["class"] == "moved":
            rows[d["slug"]]["worktree"] = d["detail"][len("branch at "):]
    gone = {d["slug"] for d in ds if d["class"] == "gone"}
    # a gone row stays only while a live stash or private WIP ref can still restore it
    new["sessions"] = [r for r in new.get("sessions", [])
                       if r["slug"] not in gone or sr.session_stashes(root, r) or _wip_exists(root, r["slug"])]
    if new.get("active") and not any(r["slug"] == new["active"] for r in new["sessions"]):
        new["active"] = None
    if any(d["class"] == "base" for d in ds):
        new["base"] = sr.default_base(root)
    new["worktrees"] = inventory(root, new)
    return new, ds
