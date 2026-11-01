"""Path identity: whole-path comparison, never string prefixes. Stdlib only."""
import os
from pathlib import Path


def _key(p: Path) -> str:
    return os.path.normcase(os.path.realpath(p))


def same_path(a: Path, b: Path) -> bool:
    return _key(a) == _key(b)


def within(child: Path, parent: Path) -> bool:
    """True when child is parent or lies below it (whole path components, so /r/host-extra is not in /r/host)."""
    c, p = Path(_key(child)), Path(_key(parent))
    return c == p or p in c.parents
