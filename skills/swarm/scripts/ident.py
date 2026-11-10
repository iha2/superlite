"""Path identity: whole-path comparison, never string prefixes. Stdlib only."""
import os
import re
from pathlib import Path

_DRIVE_TAIL = re.compile(r"(?:^|/)([A-Za-z]):/(.*)$")
_MNT = re.compile(r"^/mnt/([A-Za-z])(?:/(.*))?$")


def git_path(p: str) -> str:
    """A worktree path as Windows git wrote it. WSL git prefixes such a path with its gitdir
    (`<gitdir>/worktrees/n/C:/x`); keep the `C:/x` tail so every platform records the same string."""
    if not p:
        return p
    if os.name == "nt":  # WSL git writes `/mnt/c/x` into shared metadata; Windows git writes `C:/x`
        m = _MNT.match(p.replace("\\", "/"))
        return f"{m.group(1).upper()}:/{m.group(2) or ''}" if m else p
    m = _DRIVE_TAIL.search(p.replace("\\", "/"))
    return f"{m.group(1)}:/{m.group(2)}" if m else p


def native(p) -> Path:
    """A path this process can open: `C:/x` becomes `/mnt/c/x` under WSL when that mount exists."""
    s = git_path(str(p))
    if os.name != "nt" and len(s) > 2 and s[1] == ":" and s[0].isalpha():
        mnt = f"/mnt/{s[0].lower()}"
        if os.path.isdir(mnt):
            return Path(mnt + "/" + s[3:])
    return Path(p)


def _key(p: Path) -> str:
    return os.path.normcase(os.path.realpath(native(p)))


def same_path(a: Path, b: Path) -> bool:
    return _key(a) == _key(b)


def within(child: Path, parent: Path) -> bool:
    """True when child is parent or lies below it (whole path components, so /r/host-extra is not in /r/host)."""
    c, p = Path(_key(child)), Path(_key(parent))
    return c == p or p in c.parents
