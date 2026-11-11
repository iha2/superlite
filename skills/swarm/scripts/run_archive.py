"""Run archive: choose a run's files, make its id, write run.json and move the files into the hub's runs folder. Stdlib only."""
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402
import session_registry  # noqa: E402
import state  # noqa: E402

KEEP = {"runs", "research", "test-cmd.json", "agent-specs.json", "host-models.json", "peers.json"}


def run_id(slug: str | None, now: datetime, unfinished: bool = False) -> str:
    return f"{now:%Y%m%d-%H%M%S}-{slug or 'run'}" + ("-unfinished" if unfinished else "")


def entries(sl: Path) -> list[Path]:
    """Run files in the live folder, sorted: everything except stationary/config entries and locks."""
    if not sl.is_dir():
        return []
    return sorted((p for p in sl.iterdir() if p.name not in KEEP and p.name not in session_registry.STATIONARY
                   and not p.name.endswith(".lock")), key=lambda p: p.name)


def runs_dir(checkout: Path) -> Path:
    try:
        return state.hub_root(checkout) / state.DIR_NAME / "runs"
    except (base.GitError, OSError, IndexError, ValueError):
        return Path(checkout) / state.DIR_NAME / "runs"


def _rollback(dest: Path, moved: list[Path]) -> None:
    for name in ("run.json", "run.json.tmp"):
        try:
            (dest / name).unlink(missing_ok=True)
        except OSError:
            pass
    for p in reversed(moved):
        try:
            shutil.move(str(dest / p.name), str(p))
        except OSError:
            pass
    try:
        dest.rmdir()
    except OSError:
        pass


def reserve(sl: Path, slug: str | None = None, unfinished: bool = False, now: datetime | None = None,
            rid: str | None = None) -> Path | None:
    """Reserve the final collision-free archive folder, or return None when there are no run files."""
    if not entries(sl):
        return None
    now = (now or datetime.now()).replace(microsecond=0)
    base_id = rid or run_id(slug, now, unfinished)
    root = runs_dir(Path(sl).parent)
    root.mkdir(parents=True, exist_ok=True)
    candidate, n = base_id, 1
    while True:
        dest = root / candidate
        try:
            dest.mkdir()
            return dest
        except FileExistsError:
            n += 1
            candidate = f"{base_id}-{n}"


def cancel_reservation(dest: Path | None) -> None:
    if dest is not None:
        try:
            Path(dest).rmdir()
        except OSError:
            pass


def archive(sl: Path, slug: str | None = None, unfinished: bool = False, plan: str | None = None,
            now: datetime | None = None, rid: str | None = None, reserved: Path | None = None) -> Path | None:
    """Move the run files of live folder `sl` into <hub>/.superlite/runs/<id>/ with run.json; None if nothing to move.
    Takes no registry lock (session ensure already holds it)."""
    files = entries(sl)
    if not files:
        cancel_reservation(reserved)
        return None
    now = (now or datetime.now()).replace(microsecond=0)
    checkout = Path(sl).parent
    dest = Path(reserved) if reserved is not None else reserve(sl, slug, unfinished, now, rid)
    if dest is None:
        return None
    rid = dest.name
    names = sorted(p.name for p in files)
    moved: list[Path] = []
    done = False
    try:
        for p in files:
            shutil.move(str(p), str(dest / p.name))
            moved.append(p)
        meta = {"id": rid, "slug": slug or "run", "checkout": Path(checkout).resolve().as_posix(), "plan": plan,
                "archived_at": now.isoformat(timespec="seconds"),
                "status": "unfinished" if unfinished else "finished", "files": names}
        tmp = dest / "run.json.tmp"
        tmp.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8", newline="\n")
        os.replace(tmp, dest / "run.json")
        done = True
    finally:
        if not done:
            # A failed manifest write is the same transaction failure as a partial move.
            _rollback(dest, moved)
    return dest


def current_slug(checkout: Path) -> str | None:
    """Slug of the session registered for `checkout`, read without the registry lock; None on any error."""
    try:
        here = Path(checkout).resolve()
        root = session_registry.hub_root(here)
        reg = session_registry.read(root)
        row = session_registry.row_at(reg, here, session_registry.resolve_host(root, reg))
        return (row or {}).get("slug") or None
    except (base.GitError, OSError, ValueError, IndexError, KeyError, TypeError):
        return None


def restore(dest: Path, sl: Path) -> None:
    """Move an archived run's files back into live folder `sl` and remove the archive folder."""
    dest, sl = Path(dest), Path(sl)
    sl.mkdir(parents=True, exist_ok=True)
    for p in sorted(dest.iterdir()):
        if p.name in ("run.json", "run.json.tmp"):
            continue
        target = sl / p.name
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()
        shutil.move(str(p), str(target))
    shutil.rmtree(dest)
