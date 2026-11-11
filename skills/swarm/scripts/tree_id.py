"""Working-tree fingerprint: sha256 over HEAD, the diff against HEAD, and each untracked not-ignored file's path and bytes.

`tree_hash(root)` returns 16 lowercase hex chars, or "" outside a repo or on any git error. Stdlib only.
"""
from __future__ import annotations

import hashlib
import os
import subprocess


def _git(root: str, *args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, check=True, timeout=120).stdout


def tree_hash(root: str) -> str:  # O(F + B): untracked files and their bytes
    try:
        h = hashlib.sha256()
        h.update(_git(root, "rev-parse", "HEAD"))
        h.update(_git(root, "diff", "HEAD", "--binary"))
        names = sorted(p for p in _git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if p)
        for name in names:
            h.update(len(name).to_bytes(8, "big"))
            h.update(name)
            with open(os.path.join(root, os.fsdecode(name)), "rb") as fh:
                data = fh.read()
            h.update(len(data).to_bytes(8, "big"))
            h.update(data)
        return h.hexdigest()[:16]
    except (OSError, subprocess.SubprocessError):
        return ""
