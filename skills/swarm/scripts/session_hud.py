#!/usr/bin/env python3
"""One-line session HUD for hooks: slug, branch, worktree, status, stash.

    session_hud.py [sl_dir_or_cwd]

Prints nothing when SUPERLITE_NO_SESSION_HUD=1 or there is no sessions registry.
Stdlib only.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import session_sandboxes  # noqa: E402
import state  # noqa: E402


def git_cwd_branch(cwd: Path) -> str | None:
    r = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True,
                       cwd=cwd)
    return r.stdout.strip() if r.returncode == 0 else None


def load_reg(d: Path) -> dict | None:  # O(file)
    f = d / state.DIR_NAME / "sessions.json"
    if not f.is_file():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("sessions"), list) else None


def hub_of(start: Path) -> Path | None:
    """Main checkout of the repository at start; session worktrees share its registry."""
    r = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], capture_output=True,
                       text=True, cwd=start)
    common = Path(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None
    return common.parent.resolve() if common and common.name == ".git" else None


def find_reg(start: Path) -> tuple[Path | None, dict | None]:  # O(depth)
    cur = start.resolve()
    hub = hub_of(cur) if cur.is_dir() else None
    if hub:
        reg = load_reg(hub)
        if reg:
            return hub, reg
    for d in (cur, *cur.parents):
        reg = load_reg(d)
        if reg:
            return d, reg
    return None, None


def short_path(p: str, root: Path | None) -> str:
    try:
        path = Path(p)
        if root and path.is_relative_to(root):
            return path.relative_to(root).as_posix()
    except (ValueError, OSError):
        pass
    parts = Path(p).parts
    return "/".join(parts[-3:]) if len(parts) > 3 else p


def row_for(cwd: Path) -> tuple[Path | None, dict | None, dict | None]:  # O(N)
    """(registry root, registry, the row this checkout is); registry is None when cwd is no session checkout, row is None in a main checkout with no active session."""
    root, reg = find_reg(cwd)
    if not reg or not reg.get("sessions"):
        return root, None, None
    active = reg.get("active")
    row = None
    # Most specific worktree containing cwd (by path components); among rows sharing it, the active one.
    here = cwd.resolve()
    best = -1
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, cwd=cwd)
    top = Path(r.stdout.strip()).resolve() if r.returncode == 0 and r.stdout.strip() else None
    at_root = root is not None and top == root.resolve()
    for s in reg["sessions"]:
        wt = s.get("worktree") or ""
        if not wt:
            continue
        p = Path(wt).resolve()
        if not here.is_relative_to(p):
            continue
        if root is not None and p == root.resolve() and not at_root:
            continue
        is_active = s.get("status") == "active" or s.get("slug") == active
        if root is not None and p == root.resolve() and not is_active:
            continue
        rank = len(p.parts) * 2 + (1 if s.get("status") == "active" or s.get("slug") == active else 0)
        if rank > best:
            best, row = rank, s
    if row is None:
        if not at_root:
            return root, None, None
    if row is None and active:
        row = next((s for s in reg["sessions"] if s.get("slug") == active), None)
    return root, reg, row


def run_of(row: dict) -> str:
    """'finished' or 'open'; a row without run is open."""
    return "finished" if row.get("run") == "finished" else "open"


def _git(root: Path, *a: str) -> str | None:
    r = subprocess.run(["git", *a], capture_output=True, text=True, cwd=root)
    return r.stdout.strip() if r.returncode == 0 else None


def ahead_of(root: Path | None, reg: dict, row: dict) -> tuple[int, str | None]:
    """(commits the base gained since the row's base_sha, current base sha); (0, None) when unknown. One rev-parse; rev-list only when shas differ."""
    base, bsha = row.get("base") or reg.get("base"), row.get("base_sha")  # the row's own base; legacy: registry
    if not root or not base or not bsha:
        return 0, None
    cur = _git(root, "rev-parse", "--verify", "-q", f"refs/heads/{base}")
    if not cur or cur == bsha:
        return 0, cur
    n = _git(root, "rev-list", "--count", f"{bsha}..{cur}")
    return (int(n) if n and n.isdigit() else 0), cur


def proto_part(row: dict) -> str:  # O(entries)
    """' | proto:<topic> <status>' per open prototype sandbox of the row; '' when none or unreadable."""
    try:
        entries = session_sandboxes.open_entries(row)
    except (OSError, ValueError, TypeError, AttributeError):
        return ""
    return "".join(f" | proto:{e.get('topic')} {e.get('status')}" for e in entries)


def moved_line(root: Path | None, reg: dict) -> str | None:  # O(N)
    """Moved-out notice for a main checkout with no session: the most recently moved row."""
    if not root:
        return None
    me = str(root.resolve())
    rows = [s for s in reg["sessions"] if s.get("moved_from") and str(Path(s["moved_from"]).resolve()) == me
            and s.get("worktree")]
    if not rows:
        return None
    s = max(rows, key=lambda r: str(r.get("moved_at") or r.get("created_at") or ""))
    return f"superlite session: {s.get('slug') or s.get('topic') or '?'} moved to {s['worktree']} \u2014 move the agent root there"


def line_for(cwd: Path) -> str | None:  # O(N)
    if os.environ.get("SUPERLITE_NO_SESSION_HUD") == "1":
        return None
    root, reg, row = row_for(cwd)
    if not reg:
        return None
    active = reg.get("active")
    if row is None:
        moved = moved_line(root, reg)
        if moved:
            return moved
        return f"superlite session: none here (main checkout) | {len(reg['sessions'])} sessions"
    slug = row.get("slug") or row.get("topic") or "?"
    branch = row.get("branch") or git_cwd_branch(cwd) or "?"
    wt = short_path(row.get("worktree") or "", root)
    status = row.get("status") or ("active" if slug == active else "?")
    stash = "stash:yes" if row.get("has_stash") else "stash:no"
    out = f"superlite session: {slug} | branch {branch} | worktree {wt} | {status} | {stash} | run:{run_of(row)}"
    out += proto_part(row)
    n, cur = ahead_of(root, reg, row)
    if n > 0:
        out += f" | main:+{n}"
    if row.get("sync") in ("conflict", "needs-sync"):
        out += f" | {row['sync']}"
    if n > 0 and row.get("sync_offered") != cur:
        out += (f"\nsuperlite sync: local {row.get('base') or reg.get('base')} moved ahead by {n} commit{'s' if n != 1 else ''} since this "
                f"session last synced; ask the user whether to bring it in (superlite-cli session sync --slug {slug}), "
                f"or record Later with superlite-cli session sync --slug {slug} --later")
    return out


def main(argv=None) -> int:  # O(N)
    argv = argv if argv is not None else sys.argv[1:]
    start = Path(argv[0]) if argv else Path.cwd()
    if start.name == state.DIR_NAME:
        start = start.parent
    text = line_for(start)
    if text:
        sys.stdout.reconfigure(encoding="utf-8")
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
