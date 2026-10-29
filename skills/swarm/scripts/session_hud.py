#!/usr/bin/env python3
"""One-line session HUD for hooks: slug, branch, worktree, status, stash.

    session_hud.py [sl_dir_or_cwd]

Prints nothing when SUPERLITE_NO_SESSION_HUD=1 or there is no sessions registry.
Stdlib only.
"""
# Complexity notation: O(N) registry size
import json  # O(1)
import os  # O(1)
import subprocess  # O(1)
import sys  # O(1)
from pathlib import Path  # O(1)


def git_cwd_branch(cwd: Path) -> str | None:  # O(1)
    r = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True,
                       cwd=cwd)  # O(1)
    return r.stdout.strip() if r.returncode == 0 else None  # O(1)


def load_reg(d: Path) -> dict | None:  # O(file)
    f = d / ".superlite" / "sessions.json"  # O(1)
    if not f.is_file():  # O(1)
        return None  # O(1)
    try:  # O(1)
        data = json.loads(f.read_text(encoding="utf-8"))  # O(file)
    except (OSError, ValueError):  # O(1)
        return None  # O(1)
    return data if isinstance(data, dict) and isinstance(data.get("sessions"), list) else None  # O(1)


def hub_of(start: Path) -> Path | None:  # O(1)
    """Main checkout of the repository at start; session worktrees share its registry."""
    r = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], capture_output=True,
                       text=True, cwd=start)  # O(1)
    common = Path(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None  # O(1)
    return common.parent.resolve() if common and common.name == ".git" else None  # O(1)


def find_reg(start: Path) -> tuple[Path | None, dict | None]:  # O(depth)
    cur = start.resolve()  # O(1)
    hub = hub_of(cur) if cur.is_dir() else None  # O(1)
    if hub:  # O(1)
        reg = load_reg(hub)  # O(file)
        if reg:  # O(1)
            return hub, reg  # O(1)
    for d in (cur, *cur.parents):  # O(depth)
        reg = load_reg(d)  # O(file)
        if reg:  # O(1)
            return d, reg  # O(1)
    return None, None  # O(1)


def short_path(p: str, root: Path | None) -> str:  # O(1)
    try:  # O(1)
        path = Path(p)  # O(1)
        if root and path.is_relative_to(root):  # O(1)
            return path.relative_to(root).as_posix()  # O(1)
    except (ValueError, OSError):  # O(1)
        pass  # O(1)
    parts = Path(p).parts  # O(1)
    return "/".join(parts[-3:]) if len(parts) > 3 else p  # O(1)


def line_for(cwd: Path) -> str | None:  # O(N)
    if os.environ.get("SUPERLITE_NO_SESSION_HUD") == "1":  # O(1)
        return None  # O(1)
    root, reg = find_reg(cwd)  # O(depth)
    if not reg or not reg.get("sessions"):  # O(1)
        return None  # O(1)
    active = reg.get("active")  # O(1)
    row = None  # O(1)
    # Most specific worktree containing cwd (by path components); among rows sharing it, the active one.
    here = cwd.resolve()  # O(1)
    best = -1  # O(1)
    for s in reg["sessions"]:  # O(N)
        wt = s.get("worktree") or ""  # O(1)
        if not wt:  # O(1)
            continue  # O(1)
        p = Path(wt).resolve()  # O(1)
        if not here.is_relative_to(p):  # O(1)
            continue  # O(1)
        rank = len(p.parts) * 2 + (1 if s.get("status") == "active" or s.get("slug") == active else 0)  # O(1)
        if rank > best:  # O(1)
            best, row = rank, s  # O(1)
    if row is None and active:  # O(1)
        row = next((s for s in reg["sessions"] if s.get("slug") == active), None)  # O(N)
    if row is None:  # O(1)
        row = reg["sessions"][0]  # O(1)
    slug = row.get("slug") or row.get("topic") or "?"  # O(1)
    branch = row.get("branch") or git_cwd_branch(cwd) or "?"  # O(1)
    wt = short_path(row.get("worktree") or "", root)  # O(1)
    status = row.get("status") or ("active" if slug == active else "?")  # O(1)
    stash = "stash:yes" if row.get("has_stash") else "stash:no"  # O(1)
    return f"superlite session: {slug} | branch {branch} | worktree {wt} | {status} | {stash}"  # O(1)


def main(argv=None) -> int:  # O(N)
    argv = argv if argv is not None else sys.argv[1:]  # O(1)
    start = Path(argv[0]) if argv else Path.cwd()  # O(1)
    # cost-report used to pass proj/.superlite; accept both forms
    if start.name == ".superlite":  # O(1)
        start = start.parent  # O(1)
    text = line_for(start)  # O(N)
    if text:  # O(1)
        sys.stdout.reconfigure(encoding="utf-8")  # O(1)
        print(text)  # O(1)
    return 0  # O(1)


if __name__ == "__main__":  # O(1)
    sys.exit(main())  # O(1)
