"""session rename --slug S --to T: give a session a new slug, branch and store folder. Stdlib only.

`git branch -m` never touches the working tree, so a dirty tree keeps its changes. Works from the host or
from a session worktree (the registry lives in the main checkout).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = str(Path(__file__).resolve().parent)
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _refuse(msg: str) -> int:
    print(f"session: rename refused: {msg}", file=sys.stderr)
    return 2


def new_branch(branch: str, old: str, new: str) -> str:
    """The branch with its trailing old slug replaced; a branch that does not end in the slug is kept."""
    if branch == old:
        return new
    if branch.endswith("/" + old):
        return branch[: -len(old)] + new
    return branch


def _on_origin(root, branch: str) -> bool:
    import session_registry as R
    return R.git("rev-parse", "--verify", "-q", f"refs/remotes/origin/{branch}", cwd=root, check=False).returncode == 0


def cmd_rename(a: argparse.Namespace) -> int:
    from session_ensure import guarded
    return guarded("rename", lambda: _rename(a))


def _rename(a: argparse.Namespace) -> int:
    import session_registry as R
    from journal import Journal
    old = R.validate_slug(a.slug)
    new = R.validate_slug(a.to, "--to")
    root = R.hub_root(R.start_dir(a))
    with R.registry_lock(root):
        reg = R.read(root)
        row = R.find_row(reg, old)
        if row is None:
            return _refuse(f"no session {old}")
        branch = row.get("branch") or R.branch_for(old)
        target = new_branch(branch, old, new)
        if R.find_row(reg, new) is not None or (target != branch and (R.branch_exists(root, target) or _on_origin(root, target))) \
                or R.store_path(root, new).exists():
            return _refuse(f"{new} is taken")
        with Journal() as j:  # a failure after the branch moved must not leave the registry on a branch that is gone
            if target != branch:
                j.step("rename branch")
                R.git("branch", "-m", branch, target, cwd=root)
                j.undo(lambda: R.git("branch", "-m", target, branch, cwd=root), "branch")
            store = R.store_path(root, old)
            if store.exists():
                j.step("move store")
                dest = R.store_path(root, new)
                store.rename(dest)
                j.undo(lambda: dest.rename(store), "store")
            j.step("write registry")
            row["slug"] = new
            if row.get("branch"):
                row["branch"] = target
            if reg.get("active") == old:
                reg["active"] = new
            R.write(root, reg)
    print(f"session: renamed {old} to {new} (branch {target})")
    return 0
