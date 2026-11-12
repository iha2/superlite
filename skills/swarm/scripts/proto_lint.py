"""Stay-inside lint: a `.proto.md` plan may only write inside the open prototype sandbox. Stdlib only."""
from glob import has_magic
import os
import posixpath
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sandbox  # noqa: E402


def _norm(path: str) -> str | None:
    """Repo-relative posix form of an Owns path; None for absolute paths and ones that climb out of the root."""
    p = path.strip().replace("\\", "/")
    if not p or p.startswith("/") or (len(p) > 1 and p[1] == ":"):
        return None
    n = posixpath.normpath(p)
    return None if n == ".." or n.startswith("../") else n


def _has_symlink(root: Path, path: str) -> bool:
    """Whether an existing literal or glob-matched component of a repo-relative path is a symlink."""
    candidates = [root]
    for part in path.split("/"):
        found = []
        for parent in candidates:
            matches, linked = _expand_component(parent, part)
            if linked:
                return True
            found.extend(matches)
        candidates = [p for p in found if p.is_dir()]
    return False


def _expand_component(parent: Path, part: str) -> tuple[list[Path], bool]:
    if part == "**":
        paths = _recursive_dirs(parent)
        return ([], True) if paths is None else (paths, False)
    if has_magic(part):
        try:
            found = list(parent.glob(part))
        except OSError:
            return [], False
    else:
        child = parent / part
        found = [child] if child.exists() or child.is_symlink() else []
    return found, any(p.is_symlink() for p in found)


def _recursive_dirs(root: Path) -> list[Path] | None:
    """Directories below a `**` component, or None when any scanned component is a symlink."""
    out = []
    for path, dirs, files in os.walk(root, followlinks=False):
        here = Path(path)
        if any((here / name).is_symlink() for name in dirs + files):
            return None
        out.append(here)
    return out


def _resolves_inside(sb_rel: str, path: str, root: Path) -> bool:
    """Owns claims are repo-relative, sandbox-prefixed paths without symlink components."""
    n = _norm(path)
    return bool(n and not Path(sb_rel).is_absolute() and sandbox.inside(sb_rel, n) and not _has_symlink(root, n))


def check(plan_path: str, owns: dict[str, list[str]], sl: Path, wave0_id: str | None = None) -> list[str]:  # O(owns)
    """SANDBOX issues for Owns paths outside the open sandbox; [] for a non-proto plan or no open sandbox."""
    if not Path(plan_path).name.endswith(".proto.md"):
        return []
    sb_rel = sandbox.open_sandbox(sl)
    if not sb_rel:
        return []
    root = Path(sl).resolve().parent
    out = []
    if wave0_id is None:
        out.append("SANDBOX: plan needs an explicit in-sandbox Wave 0 task")
    for tid, paths in owns.items():
        for p in paths:
            if not _resolves_inside(sb_rel, p, root):
                out.append(f"SANDBOX: {tid} writes {p} outside {sb_rel}")
    return out
