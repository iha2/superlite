"""Session WIP on private refs: `refs/superlite/wip/<slug>` holds a snapshot of the working tree.

Unlike `git stash`, the ref never touches the shared stash list (so `git stash clear` and other sessions cannot
lose or steal it) and one ref per slug means two sessions never cross. Stdlib only.

    save(cwd, slug)      snapshot the dirty tree to the ref, then reset the tree; None when nothing to save
    restore(cwd, slug)   apply the ref's changes onto a clean tree, then delete the ref
    exists(cwd, slug)    whether the ref is there
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

PREFIX = "refs/superlite/wip/"
IDENT = {"GIT_AUTHOR_NAME": "superlite", "GIT_AUTHOR_EMAIL": "superlite@localhost",
         "GIT_COMMITTER_NAME": "superlite", "GIT_COMMITTER_EMAIL": "superlite@localhost"}


def ref_for(slug: str) -> str:
    return PREFIX + slug


def _git(cwd, *args, env=None, data: bytes | None = None, check=True) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, input=data,
                       env={**os.environ, **(env or {})})
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.decode('utf-8', 'replace').strip()}")
    return r


def _out(cwd, *args, **kw) -> str:
    return _git(cwd, *args, **kw).stdout.decode("utf-8", "replace").strip()


def exists(cwd: Path, slug: str) -> bool:
    return _git(cwd, "rev-parse", "--verify", "-q", ref_for(slug), check=False).returncode == 0


def _dirty(cwd, untracked: bool) -> bool:
    mode = "--untracked-files=all" if untracked else "--untracked-files=no"
    return bool(_out(cwd, "status", "--porcelain", mode))


def save(cwd: Path, slug: str, untracked: bool = True) -> str | None:
    """Snapshot the working tree (tracked changes, plus untracked files when `untracked`) to the slug's ref and
    reset the tree to HEAD. Returns the snapshot oid, or None when there was nothing to save."""
    if not _dirty(cwd, untracked):
        return None
    top = _out(cwd, "rev-parse", "--show-toplevel")
    head = _out(cwd, "rev-parse", "HEAD")
    with tempfile.TemporaryDirectory() as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index"), **IDENT}
        _git(top, "read-tree", "HEAD", env=env)
        _git(top, "add", "-A" if untracked else "-u", "--", ":/", env=env)
        tree = _out(top, "write-tree", env=env)
    oid = _out(top, "commit-tree", tree, "-p", head, "-m", f"superlite wip {slug}", env=IDENT)
    ref = ref_for(slug)
    prev = _out(top, "rev-parse", "--verify", "-q", ref, check=False)
    _git(top, "update-ref", ref, oid)
    try:
        _git(top, "reset", "--hard", "-q")
        if untracked:
            _git(top, "clean", "-fdq")
    except RuntimeError as e:  # put the tree back from the snapshot, then take the ref back: nothing half done
        try:
            _git(top, "checkout", "-q", oid, "--", ":/")
            _git(top, "reset", "-q")
        except RuntimeError:
            raise RuntimeError(f"{e}; the work is kept on {ref}") from e
        _git(top, "update-ref", *(("-d", ref) if not prev else (ref, prev)))
        raise
    return oid


def restore(cwd: Path, slug: str) -> bool:
    """Apply the saved changes and delete the ref. True when applied or nothing was saved; False (ref kept,
    nothing applied) when the tree is dirty or the changes do not apply cleanly."""
    if not exists(cwd, slug):
        return True
    if _dirty(cwd, untracked=False):
        return False
    top = _out(cwd, "rev-parse", "--show-toplevel")
    ref = ref_for(slug)
    patch = _git(top, "-c", "diff.noprefix=false", "-c", "diff.mnemonicPrefix=false", "diff", "--binary",
                 "--no-ext-diff", "--no-textconv", "--no-renames", f"{ref}^", ref).stdout
    if patch and _git(top, "apply", "--binary", data=patch, check=False).returncode != 0:
        return False
    _git(top, "update-ref", "-d", ref)
    return True
