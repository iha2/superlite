"""Untracked files before a session park: list them, find the ones a switch would overwrite, ask once. Stdlib only.

Split from session_registry (which re-exports these names); nothing here changes the checkout."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import base  # noqa: E402


def untracked_files(host: Path) -> list[str]:  # O(tree)
    return base.git("ls-files", "--others", "--exclude-standard", cwd=host, check=False).stdout.splitlines()


def paths_text(files: list[str]) -> str:
    more = len(files) - 5
    return ", ".join(files[:5]) + (f" (+{more} more)" if more > 0 else "")


def blocked_by(host: Path, files: list[str], target: str) -> list[str]:  # O(tree)
    """Untracked files git would refuse to overwrite when switching to `target` (same path, or file/dir clash)."""
    tree = base.git("ls-tree", "-r", "--name-only", target, cwd=host, check=False).stdout.splitlines()
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
        print("on stash: rerun with --untracked stash")
        print("on keep: rerun with --untracked keep")
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
