"""Prototype sandboxes: where they live, what is in them, whether a path is inside one. Stdlib only."""
import json
import os
import posixpath
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402
import session_registry  # noqa: E402
import state  # noqa: E402

HOST = "host"
SKIP_DIRS = {"node_modules", ".venv", "__pycache__"}
OPEN = ("building", "tryout", "approved")
FEATURE = "feature.json"


def path_for(checkout: Path, session: str, topic: str) -> Path:
    """`<checkout>/.superlite/sandboxes/<session>/<topic>`."""
    return Path(checkout) / state.DIR_NAME / "sandboxes" / session / topic


def session_of(checkout: Path) -> str:
    """Slug of the session row whose worktree is `checkout`; `host` without a registry, a row or on any error."""
    try:
        here = Path(checkout).resolve()
        root = session_registry.hub_root(here)
        reg = session_registry.read(root)
        row = session_registry.row_at(reg, here, session_registry.resolve_host(root, reg))
        return (row or {}).get("slug") or HOST
    except (base.GitError, OSError, ValueError, IndexError, KeyError, TypeError):
        return HOST


def files(checkout: Path, sb: Path) -> list[str]:
    """Sorted repo-relative posix paths of the files under `sb`, skipping dependency and cache folders."""
    out: list[str] = []
    for dirpath, dirs, names in os.walk(sb):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        out.extend(_rel(checkout, Path(dirpath) / n) for n in names)
    return sorted(out)


def _rel(checkout: Path, p: Path) -> str:
    try:
        return p.relative_to(checkout).as_posix()
    except ValueError:
        return p.resolve().relative_to(Path(checkout).resolve()).as_posix()


def _norm(p: str) -> str:
    return posixpath.normpath(p.replace("\\", "/"))


def inside(sb_rel: str, path: str) -> bool:
    """True when `path` (either slash style, `..` resolved) is the sandbox folder `sb_rel` or below it."""
    sb, target = _norm(sb_rel), _norm(path)
    return target == sb or target.startswith(sb.rstrip("/") + "/")


def open_sandbox(sl: Path) -> str | None:
    """Sandbox of the open prototype in run folder `sl`, relative to `sl.parent` (posix); None otherwise."""
    try:
        feat = json.loads((Path(sl) / FEATURE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(feat, dict):
        return None
    sb = feat.get("sandbox")
    if feat.get("status") not in OPEN or not isinstance(sb, str) or not sb:
        return None
    try:
        return Path(sb).relative_to(Path(sl).resolve().parent).as_posix()
    except ValueError:
        return Path(sb).as_posix()
