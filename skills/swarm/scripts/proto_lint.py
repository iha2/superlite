"""Stay-inside lint: a `.proto.md` plan may only write inside the open prototype sandbox. Stdlib only."""
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


def _resolves_inside(sb_rel: str, plan_rel: str, path: str) -> bool:
    """Against the repo root first; then, for a plan that lives in the sandbox, against the sandbox."""
    n = _norm(path)
    if n is None:
        return False
    if sandbox.inside(sb_rel, n):
        return True
    if sandbox.inside(sb_rel, plan_rel):
        joined = _norm(f"{sb_rel}/{n}")
        return joined is not None and sandbox.inside(sb_rel, joined)
    return False


def check(plan_path: str, owns: dict[str, list[str]], sl: Path) -> list[str]:  # O(owns)
    """SANDBOX issues for Owns paths outside the open sandbox; [] for a non-proto plan or no open sandbox."""
    if not Path(plan_path).name.endswith(".proto.md"):
        return []
    sb_rel = sandbox.open_sandbox(sl)
    if not sb_rel:
        return []
    root = Path(sl).resolve().parent
    try:
        plan_rel = Path(plan_path).resolve().relative_to(root).as_posix()
    except ValueError:
        plan_rel = ""
    out = []
    for tid, paths in owns.items():
        for p in paths:
            if not _resolves_inside(sb_rel, plan_rel, p):
                out.append(f"SANDBOX: {tid} writes {p} outside {sb_rel}")
    return out
