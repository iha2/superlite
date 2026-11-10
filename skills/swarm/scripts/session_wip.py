"""Session WIP on private refs: `refs/superlite/wip/<slug>` holds a snapshot of the working tree and the index.

Like `git stash create`, the snapshot is a commit whose tree is the working tree, with parents HEAD and an
index commit (the staged tree), so staged and unstaged contents round-trip separately.

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


def _patch(top, a: str, b: str) -> bytes:
    return _git(top, "-c", "diff.noprefix=false", "-c", "diff.mnemonicPrefix=false", "diff", "--binary",
                "--no-ext-diff", "--no-textconv", "--no-renames", a, b).stdout


def save(cwd: Path, slug: str, untracked: bool = True) -> str | None:
    """Snapshot the working tree (tracked changes, plus untracked files when `untracked`) and the index to the
    slug's ref and reset the tree to HEAD. Returns the snapshot oid, or None when there was nothing to save."""
    if not _dirty(cwd, untracked):
        return None
    if exists(cwd, slug):  # an earlier snapshot not yet restored is the only reference to that work
        raise RuntimeError(f"{ref_for(slug)} already holds unrestored work; apply or discard it before saving again")
    top = _out(cwd, "rev-parse", "--show-toplevel")
    head = _out(cwd, "rev-parse", "HEAD")
    staged = _out(top, "write-tree")
    icommit = _out(top, "commit-tree", staged, "-p", head, "-m", f"superlite wip index {slug}", env=IDENT)
    with tempfile.TemporaryDirectory() as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index"), **IDENT}
        _git(top, "read-tree", staged, env=env)
        _git(top, "add", "-A" if untracked else "-u", "--", ":/", env=env)
        tree = _out(top, "write-tree", env=env)
    oid = _out(top, "commit-tree", tree, "-p", head, "-p", icommit, "-m", f"superlite wip {slug}", env=IDENT)
    ref = ref_for(slug)
    _git(top, "update-ref", ref, oid)
    try:
        _git(top, "reset", "--hard", "-q")
        if untracked:
            _git(top, "clean", "-fdq")
    except RuntimeError as e:  # put tree and index back from the snapshot, then take the ref back
        try:  # non-overlay: paths deleted before save are deleted again, not left resurrected by the reset
            _git(top, "checkout", "-q", "--no-overlay", oid, "--", ":/")
            _git(top, "read-tree", icommit)
            _git(top, "update-index", "-q", "--refresh", check=False)
        except RuntimeError:
            raise RuntimeError(f"{e}; the work is kept on {ref}") from e
        _git(top, "update-ref", "-d", ref)
        raise
    return oid


def _untracked_paths(top, ref: str) -> list[str] | None:
    """Paths in a legacy `git stash -u` commit's third parent (its untracked files); None when it has none."""
    if _git(top, "rev-parse", "--verify", "-q", f"{ref}^3", check=False).returncode != 0:
        return None
    raw = _git(top, "ls-tree", "-r", "-z", "--name-only", f"{ref}^3").stdout
    return [p.decode("utf-8", "surrogateescape") for p in raw.split(b"\0") if p]


def _write_untracked(top, ref: str, paths: list[str]) -> bool:
    """Check the third parent's files out of a temp index into the tree; on failure remove what was written."""
    with tempfile.TemporaryDirectory() as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        _git(top, "read-tree", f"{ref}^3", env=env)
        if _git(top, "checkout-index", "-a", f"--prefix={Path(top).as_posix()}/", env=env, check=False).returncode == 0:
            return True
    for p in paths:
        (Path(top) / p).unlink(missing_ok=True)
    return False


def restore(cwd: Path, slug: str) -> bool:
    """Apply the saved changes (staged ones to the index too) and delete the ref. True when applied or nothing
    was saved; False (ref kept, nothing applied) when the tree is dirty, an untracked file of a legacy stash
    already exists, or the changes do not apply cleanly."""
    if not exists(cwd, slug):
        return True
    if _dirty(cwd, untracked=False):
        return False
    top = _out(cwd, "rev-parse", "--show-toplevel")
    ref = ref_for(slug)
    tree_patch = _patch(top, f"{ref}^1", ref)
    has_index = _git(top, "rev-parse", "--verify", "-q", f"{ref}^2", check=False).returncode == 0
    index_patch = _patch(top, f"{ref}^1", f"{ref}^2") if has_index else b""
    if index_patch and _git(top, "apply", "--check", "--cached", "--binary", data=index_patch,
                            check=False).returncode != 0:
        return False
    if tree_patch and _git(top, "apply", "--check", "--binary", data=tree_patch, check=False).returncode != 0:
        return False
    extra = _untracked_paths(top, ref)
    if extra and any(os.path.lexists(Path(top) / p) for p in extra):
        return False  # never overwrite an untracked file that is already there
    if extra and not _write_untracked(top, ref, extra):
        return False
    if index_patch:
        _git(top, "apply", "--cached", "--binary", data=index_patch)
    if tree_patch and _git(top, "apply", "--binary", data=tree_patch, check=False).returncode != 0:
        _git(top, "reset", "-q")  # the index matched HEAD before: take the staged half back
        for p in extra or []:
            (Path(top) / p).unlink(missing_ok=True)
        return False
    _git(top, "update-ref", "-d", ref)
    return True
