"""Rollback-safe session registration for existing linked and in-place checkouts."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import claude_create  # noqa: E402
from journal import Journal  # noqa: E402
from session_registry import (
    base_row, current_branch, default_base, free_slug, git, hide_all, now_ms, same, session_line, store_path,
    upsert_row, write,
)
from worktree_home import harness_worktree  # noqa: E402


def register_linked(root: Path, reg: dict, here: Path, want: str) -> int:
    """Adopt an unregistered linked worktree without changing its Git state."""
    slug = free_slug(root, reg, want)
    cur = current_branch(here)
    head = git("rev-parse", "HEAD", cwd=here).stdout.strip()
    detached = cur == "HEAD"
    branch = None if detached else claude_create.adopt_branch(here, slug, cur)
    with Journal() as j:
        j.step("register the session")
        upsert_row(reg, {"slug": slug, "branch": branch, "worktree": here.as_posix(),
                         "store": store_path(root, slug).as_posix(), "has_stash": False, "status": "parallel",
                         "owned": False, "adopted_branch": True, "harness": harness_worktree(here, branch),
                         "run": "open", "created_at": now_ms(), "topic": want,
                         **({"detached": True, "head": head} if detached else {}),
                         **base_row(root, reg, head if detached else branch)})
        hide_all(root)
        write(root, reg)
    print(f"session: registered {slug} where it is ({here.as_posix()}) on {branch or 'detached ' + head[:7]}")
    session_line(here, slug, branch or "HEAD")
    return 0


def register_in_place(root: Path, reg: dict, host: Path, path: Path, want: str, exist: str) -> str:
    """Register a checkout already on `exist` as an open session where it is."""
    slug = free_slug(root, reg, want)
    if slug != want:
        print(f"session: slug {want} taken; using {slug}")
    with Journal() as j:
        j.step("register the session")
        hide_all(root)
        at_host = same(path, host)
        reg["base"] = reg.get("base") or default_base(root)
        upsert_row(reg, {"slug": slug, "branch": exist, "worktree": path.as_posix(),
                         "store": store_path(root, slug).as_posix(), "has_stash": False,
                         "status": "active" if at_host else "parallel", "owned": False, "adopted_branch": True,
                         "run": "open", "created_at": now_ms(), "topic": want, **base_row(root, reg, exist)})
        if at_host:
            reg["active"] = slug
            reg["host_worktree"] = path.as_posix()
        write(root, reg)
    print(f"session: registered {slug} in place on {exist} ({path.as_posix()})")
    return slug
