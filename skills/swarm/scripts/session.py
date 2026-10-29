#!/usr/bin/env python3
"""Per-worktree superlite sessions so concurrent runs do not share run state.

    session.py start [--host-session H] [--plan P] [--id ID]
    session.py end|discard [--id ID] [--plan P]
    session.py list [--plan P]
    session.py use --id ID [--plan P]
    session.py status [--plan P]
    session.py path [--plan P]

Layout under the project `.superlite/`:
  sessions.json   registry {sessions, by_host, active}
  active          plain text id (hook-friendly fallback)
  sessions/<id>/  that session's run state (orchestrator, mvp, pool, …)
  peers.json, feature.json, worktrees/, sandboxes/  stay shared at the project `.superlite/`

Resolution order for the state dir:
  1. session id encoded in the argument path (…/sessions/<id>/…)
  2. $SUPERLITE_SESSION
  3. by_host[$SUPERLITE_HOST_SESSION]
  4. registry/active
  5. legacy flat `.superlite/` when no session is active

`start` always creates a new id (unless --id reuses a slot). Sessions clean up after themselves:
`end`/`discard` (also run by `sl finish`) deletes `sessions/<id>/`; same-host restart ends the
previous tree first; an empty registry drops `sessions.json`/`active`. `list` shows every
registered session; `use` switches the active pointer. Registry updates are locked and
atomic. `--id` rejects anything that is not a session id (no path traversal). Stdlib only.
"""
# Complexity notation:
#   everything here is O(1) file work plus O(S) registry size, plus lock wait under contention
import argparse  # O(1)
import json  # O(1)
import os  # O(1)
import re  # O(1)
import shutil  # O(1)
import sys  # O(1)
import time  # O(1)
from datetime import datetime  # O(1)
from pathlib import Path  # O(1)

REGISTRY = "sessions.json"  # O(1)
ACTIVE = "active"  # O(1)
SESSIONS = "sessions"  # O(1)
LOCK = "sessions.lock"  # O(1)
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")  # O(1)

if os.name == "nt":  # O(1)
    import msvcrt  # O(1)

    def _lock(f):  # O(1) per attempt
        f.seek(0)  # O(1)
        while True:  # O(attempts)
            try:  # O(1)
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)  # O(1)
                return  # O(1)
            except OSError:  # O(1)
                time.sleep(0.01)  # O(1)

    def _unlock(f):  # O(1)
        f.seek(0)  # O(1)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)  # O(1)
else:  # O(1)
    import fcntl  # O(1)

    def _lock(f):  # O(1)
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)  # O(1)

    def _unlock(f):  # O(1)
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)  # O(1)


def _looks_like_container(sl: Path) -> bool:  # O(1)
    """True when sl is a project `.superlite` that already holds run state (flat or sessions)."""
    return (sl / "orchestrator.json").is_file() or (sl / REGISTRY).is_file() or (sl / ACTIVE).is_file() or \
        (sl / SESSIONS).is_dir() or (sl / "peers.json").is_file() or (sl / "feature.json").is_file() or \
        (sl / "pool.json").is_file() or (sl / "prime.md").is_file() or (sl / "stable.json").is_file()  # O(1)


def _is_repo_root(d: Path) -> bool:  # O(1)
    """True for a git checkout root (`.git` directory) or linked worktree (`.git` file)."""
    return (d / ".git").exists()  # O(1)


def _ancestors_within_repo(start: Path) -> list[Path]:  # O(depth)
    """start and its parents, stopping at the nearest git root (inclusive).

    With no `.git` above start (e.g. a bare temp dir in tests), return only start —
    never walk to the filesystem root and invent `/.superlite`.
    """
    out: list[Path] = []  # O(1)
    for d in (start, *start.parents):  # O(depth)
        out.append(d)  # O(1)
        if _is_repo_root(d):  # O(1)
            return out  # O(depth)
    return [start]  # O(1)


def _container_from_arg(plan_or_dir=None) -> Path:  # O(depth)
    """The project `.superlite` folder (never a sessions/<id> path, never a nested fake `.superlite`).

    A plan under `docs/plans/` binds to that package's `.superlite` (parent of `docs`), including a
    prototype sandbox's own tree — do not walk out to an outer checkout's `.superlite`.
    From cwd with no markers, stop at the nearest git root so a fresh worktree does not inherit
    the parent checkout's run.
    """
    if plan_or_dir is None:  # O(1)
        start = Path.cwd().resolve()  # O(1)
        chain = _ancestors_within_repo(start)  # O(depth)
        for d in chain:  # O(depth)
            if d.parent.name == SESSIONS and d.parent.parent.name == ".superlite":  # O(1)
                return d.parent.parent  # O(1)
            if d.name == SESSIONS and d.parent.name == ".superlite":  # O(1)
                return d.parent  # O(1)
            if d.name == ".superlite":  # O(1)
                if d.parent.parent.name == SESSIONS and d.parent.parent.parent.name == ".superlite":  # O(1)
                    return d.parent.parent.parent  # O(1)
                return d  # O(1)
            sl = d / ".superlite"  # O(1)
            if sl.is_dir() and _looks_like_container(sl):  # O(1)
                return sl  # O(1)
        # No .git above cwd (bare temp / nested folder): climb for an existing
        # container so `sl` from a subfolder still finds the project run. Stop
        # before the filesystem root — never invent `/.superlite`.
        if not any(_is_repo_root(d) for d in chain):  # O(depth)
            for d in start.parents:  # O(depth)
                if d == d.parent:  # O(1)
                    break  # O(1)
                sl = d / ".superlite"  # O(1)
                if sl.is_dir() and _looks_like_container(sl):  # O(1)
                    return sl  # O(1)
        return chain[-1] / ".superlite"  # nearest repo root, or cwd if no .git | O(1)

    p = Path(plan_or_dir).resolve()  # O(1)
    start = p if p.is_dir() else p.parent  # O(1)
    # Session state path first (before docs/plans so a plan inside a session stays there)
    for d in (start, *start.parents):  # O(depth)
        if d.parent.name == SESSIONS and d.parent.parent.name == ".superlite":  # O(1)
            return d.parent.parent  # O(1)
        if d.name == SESSIONS and d.parent.name == ".superlite":  # O(1)
            return d.parent  # O(1)
        if d.name == ".superlite" and d.parent.parent.name == SESSIONS and d.parent.parent.parent.name == ".superlite":  # O(1)
            return d.parent.parent.parent  # O(1)
    # Plan under docs/plans: package root is parent of docs (sandbox or checkout), not an outer tree
    for d in (start, *start.parents):  # O(depth)
        if d.name == "plans" and d.parent.name == "docs":  # O(1)
            return d.parent.parent / ".superlite"  # O(1)
    chain = _ancestors_within_repo(start)  # O(depth)
    for d in chain:  # O(depth)
        if d.name == ".superlite":  # O(1)
            return d  # O(1)
        sl = d / ".superlite"  # O(1)
        if sl.is_dir() and _looks_like_container(sl):  # O(1)
            return sl  # O(1)
    return chain[-1] / ".superlite"  # O(1)


def project_sl(plan_or_dir=None) -> Path:  # O(1)
    return _container_from_arg(plan_or_dir)  # O(1)


def project_root(plan_or_dir=None) -> Path:  # O(1)
    return project_sl(plan_or_dir).parent  # O(1)


def id_from_path(path) -> str | None:  # O(1)
    """Session id when path is inside `.superlite/sessions/<id>/…`, else None."""
    p = Path(path).resolve()  # O(1)
    parts = p.parts  # O(d)
    for i, part in enumerate(parts):  # O(d)
        if part == SESSIONS and i + 1 < len(parts) and i > 0 and parts[i - 1] == ".superlite":  # O(1)
            cand = parts[i + 1]  # O(1)
            return cand if ID_RE.match(cand) else None  # O(1)
    return None  # O(1)


def _read_registry(base: Path) -> dict:  # O(S)
    f = base / REGISTRY  # O(1)
    try:  # O(1)
        d = json.loads(f.read_text(encoding="utf-8"))  # O(S)
    except (OSError, ValueError):  # O(1)
        return {"sessions": {}, "by_host": {}, "active": None}  # O(1)
    if not isinstance(d, dict):  # O(1)
        return {"sessions": {}, "by_host": {}, "active": None}  # O(1)
    d.setdefault("sessions", {})  # O(1)
    d.setdefault("by_host", {})  # O(1)
    d.setdefault("active", None)  # O(1)
    if not isinstance(d["sessions"], dict):  # O(1)
        d["sessions"] = {}  # O(1)
    if not isinstance(d["by_host"], dict):  # O(1)
        d["by_host"] = {}  # O(1)
    return d  # O(1)


def _atomic_write_registry(base: Path, data: dict) -> None:  # O(S)
    """Write sessions.json via a temp file + replace, then sync the active pointer."""
    base.mkdir(parents=True, exist_ok=True)  # O(1)
    target = base / REGISTRY  # O(1)
    tmp = base / f".{REGISTRY}.{os.getpid()}.tmp"  # O(1)
    payload = json.dumps(data, indent=1) + "\n"  # O(S)
    tmp.write_text(payload, encoding="utf-8")  # O(S)
    os.replace(tmp, target)  # O(1) atomic on the same filesystem
    active = data.get("active")  # O(1)
    af = base / ACTIVE  # O(1)
    if active:  # O(1)
        af.write_text(str(active) + "\n", encoding="utf-8")  # O(1)
    elif af.is_file():  # O(1)
        af.unlink()  # O(1)


def _with_registry_lock(base: Path, fn):  # O(fn) + lock wait
    """Run fn(reg) -> reg under an exclusive lock; write the returned registry atomically."""
    base.mkdir(parents=True, exist_ok=True)  # O(1)
    lock_path = base / LOCK  # O(1)
    with open(lock_path, "a+", encoding="utf-8") as lf:  # O(1)
        _lock(lf)  # O(1)
        try:  # O(1)
            reg = _read_registry(base)  # O(S)
            new = fn(reg)  # O(fn)
            if new is not None:  # O(1)
                _atomic_write_registry(base, new)  # O(S)
            return new if new is not None else reg  # O(1)
        finally:  # O(1)
            _unlock(lf)  # O(1)


def _new_id(base: Path, reg: dict) -> str:  # O(1)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")  # O(1)
    cand = f"{stamp}-{os.getpid():x}-{os.urandom(2).hex()}"  # O(1)
    while cand in reg["sessions"] or (base / SESSIONS / cand).exists():  # O(1)
        cand = f"{stamp}-{os.urandom(3).hex()}"  # O(1)
    return cand  # O(1)


def _active_id(base: Path) -> str | None:  # O(S)
    env = (os.environ.get("SUPERLITE_SESSION") or "").strip()  # O(1)
    if env and ID_RE.match(env) and (base / SESSIONS / env).is_dir():  # O(1)
        return env  # O(1)
    host = (os.environ.get("SUPERLITE_HOST_SESSION") or "").strip()  # O(1)
    reg = _read_registry(base)  # O(S)
    if host and host in reg["by_host"]:  # O(1)
        sid = reg["by_host"][host]  # O(1)
        if isinstance(sid, str) and ID_RE.match(sid) and (base / SESSIONS / sid).is_dir():  # O(1)
            return sid  # O(1)
    active = reg.get("active")  # O(1)
    if active and ID_RE.match(str(active)) and (base / SESSIONS / str(active)).is_dir():  # O(1)
        return str(active)  # O(1)
    try:  # O(1)
        aid = (base / ACTIVE).read_text(encoding="utf-8").strip()  # O(1)
    except OSError:  # O(1)
        aid = ""  # O(1)
    if aid and ID_RE.match(aid) and (base / SESSIONS / aid).is_dir():  # O(1)
        return aid  # O(1)
    return None  # O(1)


def state_dir(plan_or_dir=None) -> Path:  # O(S)
    """Directory that holds this run's orchestrator/mvp/pool (session or legacy flat).

    An argument inside `sessions/<id>/` always selects that session, even when another is active.
    """
    if plan_or_dir is not None:  # O(1)
        encoded = id_from_path(plan_or_dir)  # O(d)
        if encoded:  # O(1)
            base = project_sl(plan_or_dir)  # O(1)
            return base / SESSIONS / encoded  # O(1)
    base = project_sl(plan_or_dir)  # O(1)
    sid = _active_id(base)  # O(S)
    if sid:  # O(1)
        return base / SESSIONS / sid  # O(1)
    return base  # O(1)


def display_path(path, start=None) -> str:  # O(1)
    """Prefer a cwd-relative posix path for CLI defaults/prints; absolute when outside cwd."""
    p = Path(path).resolve()  # O(1)
    base = Path(start or Path.cwd()).resolve()  # O(1)
    try:  # O(1)
        return p.relative_to(base).as_posix()  # O(1)
    except ValueError:  # O(1)
        return str(p)  # O(1)


def current_id(plan_or_dir=None) -> str | None:  # O(S)
    if plan_or_dir is not None:  # O(1)
        encoded = id_from_path(plan_or_dir)  # O(d)
        if encoded:  # O(1)
            return encoded  # O(1)
    return _active_id(project_sl(plan_or_dir))  # O(S)


def registered_ids(plan_or_dir=None) -> list[str]:  # O(S)
    """Session ids still registered under the project container."""
    return list(_read_registry(project_sl(plan_or_dir))["sessions"])  # O(S)


def start(plan_or_dir=None, host_session: str | None = None, session_id: str | None = None) -> tuple[str, Path]:  # O(S + tree)
    """Create a new session dir (or reuse --id), mark it active, return (id, path).

    Same `--host-session` / `$SUPERLITE_HOST_SESSION` again ends that host's previous session first
    so restarts do not leave orphan trees.
    """
    base = project_sl(plan_or_dir)  # O(1)
    base.mkdir(parents=True, exist_ok=True)  # O(1)
    host = (host_session or os.environ.get("SUPERLITE_HOST_SESSION") or "").strip() or None  # O(1)
    want = (session_id or "").strip()  # O(1)
    if want and not ID_RE.match(want):  # O(1)
        raise ValueError(f"invalid session id: {want}")  # O(1)

    # Restart cleanup outside the write lock's critical path for the new id (end takes its own lock).
    if host:  # O(1)
        prev = _read_registry(base)["by_host"].get(host)  # O(S)
        if prev and prev != want and isinstance(prev, str) and ID_RE.match(prev):  # O(1)
            end(plan_or_dir, session_id=prev)  # O(tree)

    held: dict = {}  # O(1)

    def mutate(reg: dict):  # O(1)
        sid = want or _new_id(base, reg)  # O(1)
        held["sid"] = sid  # O(1)
        path = base / SESSIONS / sid  # O(1)
        path.mkdir(parents=True, exist_ok=True)  # O(1)
        (path / "mvp").mkdir(exist_ok=True)  # O(1)
        held["path"] = path  # O(1)
        reg["sessions"][sid] = {  # O(1)
            "created": datetime.now().isoformat(timespec="seconds"),  # O(1)
            "host_session": host,  # O(1)
        }  # O(1)
        if host:  # O(1)
            reg["by_host"][host] = sid  # O(1)
        reg["active"] = sid  # O(1)
        return reg  # O(1)

    _with_registry_lock(base, mutate)  # O(S)
    sid, path = held["sid"], held["path"]  # O(1)
    os.environ["SUPERLITE_SESSION"] = sid  # so this process and children keep the same session | O(1)
    return sid, path  # O(1)


def end(plan_or_dir=None, session_id: str | None = None) -> str:  # O(tree)
    """Delete sessions/<id> and clear registry pointers. Returns a one-line status.

    Resolves id from (1) explicit session_id, (2) path-encoded id, (3) active/env.
    Rejects ids that do not match ID_RE before any delete (blocks `end --id ../../src`).
    Empty-registry cleanup runs under the same lock as the registry write so a concurrent
    start cannot have its new sessions.json deleted. The lock file itself is retained.
    Also drops this session's peers-suspension ownership.
    """
    base = project_sl(plan_or_dir)  # O(1)
    sid = (session_id or "").strip()  # O(1)
    if not sid and plan_or_dir is not None:  # O(1)
        sid = id_from_path(plan_or_dir) or ""  # O(d)
    if not sid:  # O(1)
        sid = (_active_id(base) or "").strip()  # O(S)
    if not sid:  # O(1)
        return "session: none"  # O(1)
    if not ID_RE.match(sid):  # O(1)
        raise ValueError(f"invalid session id: {sid}")  # O(1)

    path = base / SESSIONS / sid  # O(1)
    # Refuse to delete anything outside sessions/<id> (defense in depth with ID_RE)
    try:  # O(1)
        path.resolve().relative_to((base / SESSIONS).resolve())  # O(1)
    except ValueError:  # O(1)
        raise ValueError(f"invalid session id: {sid}") from None  # O(1)

    # Drop peers ownership even when finish already released (idempotent) — covers
    # `session end` and same-host restart cleanup that never go through finish.
    try:  # O(1)
        import exclusive  # O(1)
        exclusive.release(plan_or_dir or base, owner=sid)  # O(S)
    except Exception:  # noqa: BLE001 | never block session cleanup on peers | O(1)
        pass  # O(1)

    base.mkdir(parents=True, exist_ok=True)  # O(1)
    with open(base / LOCK, "a+", encoding="utf-8") as lf:  # O(1)
        _lock(lf)  # O(1)
        try:  # O(1)
            reg = _read_registry(base)  # O(S)
            reg["sessions"].pop(sid, None)  # O(1)
            reg["by_host"] = {h: s for h, s in reg["by_host"].items() if s != sid}  # O(H)
            if reg.get("active") == sid:  # O(1)
                reg["active"] = None  # O(1)
            if reg["sessions"]:  # O(1)
                _atomic_write_registry(base, reg)  # O(S)
            else:  # O(1)
                # Empty: drop registry/active under the lock; keep LOCK for waiters.
                for name in (REGISTRY, ACTIVE):  # O(1)
                    f = base / name  # O(1)
                    if f.is_file():  # O(1)
                        try:  # O(1)
                            f.unlink()  # O(1)
                        except OSError:  # O(1)
                            pass  # O(1)
            if path.is_dir():  # O(1)
                shutil.rmtree(path, ignore_errors=True)  # O(tree)
            sroot = base / SESSIONS  # O(1)
            if sroot.is_dir() and not any(sroot.iterdir()):  # O(1)
                try:  # O(1)
                    sroot.rmdir()  # O(1)
                except OSError:  # O(1)
                    pass  # O(1)
        finally:  # O(1)
            _unlock(lf)  # O(1)

    if os.environ.get("SUPERLITE_SESSION") == sid:  # O(1)
        os.environ.pop("SUPERLITE_SESSION", None)  # O(1)
    return f"session: ended {sid}"  # O(1)


def status_line(plan_or_dir=None) -> str:  # O(S)
    base = project_sl(plan_or_dir)  # O(1)
    sid = current_id(plan_or_dir) if plan_or_dir is not None else _active_id(base)  # O(S)
    reg = _read_registry(base)  # O(S)
    n = len(reg["sessions"])  # O(1)
    if not sid:  # O(1)
        return f"session: none (legacy flat .superlite; {n} registered)"  # O(1)
    return f"session: {sid} path={base / SESSIONS / sid} registered={n}"  # O(1)


def _session_meta(base: Path, sid: str, info) -> dict:  # O(1)
    """Small display fields for one registered session (orch / stopped / host / created)."""
    path = base / SESSIONS / sid  # O(1)
    meta = info if isinstance(info, dict) else {}  # O(1)
    created = meta.get("created") or "?"  # O(1)
    host = meta.get("host_session") or "-"  # O(1)
    stopped = "yes" if (path / "stopped.md").is_file() else "no"  # O(1)
    orch = "?"  # O(1)
    oj = path / "orchestrator.json"  # O(1)
    if oj.is_file():  # O(1)
        try:  # O(1)
            data = json.loads(oj.read_text(encoding="utf-8"))  # O(1)
            if isinstance(data, dict) and data.get("mode"):  # O(1)
                orch = str(data["mode"])  # O(1)
        except (OSError, ValueError):  # O(1)
            pass  # O(1)
    return {"created": created, "host": host, "stopped": stopped, "orch": orch, "path": path}  # O(1)


def list_lines(plan_or_dir=None) -> list[str]:  # O(S)
    """Human-readable list: header plus one row per registered session (* = active)."""
    base = project_sl(plan_or_dir)  # O(1)
    reg = _read_registry(base)  # O(S)
    active = _active_id(base)  # O(S)
    sessions = reg.get("sessions") or {}  # O(1)
    lines = [f"session: list active={active or 'none'} count={len(sessions)}"]  # O(1)
    # Stable order: active first, then created ascending, then id
    rows = []  # O(1)
    for sid, info in sessions.items():  # O(S)
        if not isinstance(sid, str) or not ID_RE.match(sid):  # O(1)
            continue  # O(1)
        meta = _session_meta(base, sid, info)  # O(1)
        rows.append((0 if sid == active else 1, str(meta["created"]), sid, meta))  # O(1)
    rows.sort()  # O(S log S)
    for _, _, sid, meta in rows:  # O(S)
        mark = "* " if sid == active else "  "  # O(1)
        lines.append(  # O(1)
            f"{mark}{sid} created={meta['created']} host={meta['host']} "
            f"stopped={meta['stopped']} orch={meta['orch']}"
        )  # O(1)
    return lines  # O(S)


def use(plan_or_dir=None, session_id: str | None = None) -> tuple[str, Path]:  # O(S)
    """Make an existing session the active one. Returns (id, path).

    Rebinds `$SUPERLITE_HOST_SESSION` in the registry when that env is set, so the next
    hook resolve for this chat finds the switched session. Rejects unknown / invalid ids.
    """
    base = project_sl(plan_or_dir)  # O(1)
    sid = (session_id or "").strip()  # O(1)
    if not sid or not ID_RE.match(sid):  # O(1)
        raise ValueError(f"invalid session id: {sid or '(empty)'}")  # O(1)
    path = base / SESSIONS / sid  # O(1)
    try:  # O(1)
        path.resolve().relative_to((base / SESSIONS).resolve())  # O(1)
    except ValueError:  # O(1)
        raise ValueError(f"invalid session id: {sid}") from None  # O(1)
    if not path.is_dir():  # O(1)
        raise ValueError(f"unknown session: {sid}")  # O(1)

    host = (os.environ.get("SUPERLITE_HOST_SESSION") or "").strip() or None  # O(1)

    def mutate(reg: dict):  # O(1)
        if sid not in reg["sessions"]:  # O(1)
            # Dir exists but registry drifted — re-register lightly so use still works.
            reg["sessions"][sid] = {  # O(1)
                "created": datetime.now().isoformat(timespec="seconds"),  # O(1)
                "host_session": host,  # O(1)
            }  # O(1)
        elif host:  # O(1)
            info = reg["sessions"].get(sid)  # O(1)
            if isinstance(info, dict):  # O(1)
                info["host_session"] = host  # O(1)
        if host:  # O(1)
            reg["by_host"][host] = sid  # O(1)
        reg["active"] = sid  # O(1)
        return reg  # O(1)

    _with_registry_lock(base, mutate)  # O(S)
    os.environ["SUPERLITE_SESSION"] = sid  # O(1)
    return sid, path  # O(1)


def main(argv=None) -> int:  # O(S)
    ap = argparse.ArgumentParser(description=__doc__.split(chr(10))[0])  # O(1)
    sub = ap.add_subparsers(dest="cmd", required=True)  # O(1)
    s = sub.add_parser("start")  # O(1)
    s.add_argument("--host-session", help="harness session id to bind (also $SUPERLITE_HOST_SESSION)")  # O(1)
    s.add_argument("--id", help="reuse this session id (must match [A-Za-z0-9._-]+)")  # O(1)
    e = sub.add_parser("end")  # O(1)
    e.add_argument("--id", help="session to end (default: active)")  # O(1)
    d = sub.add_parser("discard", help="same as end: delete sessions/<id>")  # O(1)
    d.add_argument("--id", help="session to discard (default: active)")  # O(1)
    u = sub.add_parser("use", help="switch the active session")  # O(1)
    u.add_argument("--id", required=True, help="session id to make active")  # O(1)
    for name in ("list", "status", "path"):  # O(1)
        sub.add_parser(name)  # O(1)
    for p in sub.choices.values():  # O(1)
        if hasattr(p, "add_argument"):  # O(1)
            p.add_argument("--plan", help="plan or .superlite path; default nearest project .superlite")  # O(1)
    a = ap.parse_args(argv)  # O(1)
    if a.cmd == "start":  # O(1)
        try:  # O(1)
            sid, path = start(a.plan, host_session=getattr(a, "host_session", None),  # O(S)
                              session_id=getattr(a, "id", None))  # O(1)
        except ValueError as err:  # O(1)
            print(f"session: {err}", file=sys.stderr)  # O(1)
            return 2  # O(1)
        print(f"session: started {sid} path={path}")  # O(1)
        print(f"SUPERLITE_SESSION={sid}")  # O(1)
        return 0  # O(1)
    if a.cmd in ("end", "discard"):  # O(1)
        try:  # O(1)
            print(end(a.plan, session_id=getattr(a, "id", None)))  # O(tree)
        except ValueError as err:  # O(1)
            print(f"session: {err}", file=sys.stderr)  # O(1)
            return 2  # O(1)
        return 0  # O(1)
    if a.cmd == "use":  # O(1)
        try:  # O(1)
            sid, path = use(a.plan, session_id=getattr(a, "id", None))  # O(S)
        except ValueError as err:  # O(1)
            print(f"session: {err}", file=sys.stderr)  # O(1)
            return 2  # O(1)
        print(f"session: using {sid} path={path}")  # O(1)
        print(f"SUPERLITE_SESSION={sid}")  # O(1)
        return 0  # O(1)
    if a.cmd == "list":  # O(1)
        print("\n".join(list_lines(a.plan)))  # O(S)
        return 0  # O(1)
    if a.cmd == "path":  # O(1)
        print(state_dir(a.plan))  # O(S)
        return 0  # O(1)
    print(status_line(a.plan))  # O(S)
    return 0  # O(1)


if __name__ == "__main__":  # O(1)
    sys.exit(main())  # O(1)
