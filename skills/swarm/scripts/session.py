#!/usr/bin/env python3
"""Per-worktree superlite sessions so concurrent runs do not share run state.

    session.py start [--host-session H] [--plan P] [--id ID]
    session.py end [--id ID] [--plan P]
    session.py status [--plan P]
    session.py path [--plan P]

Layout under the project `.superlite/`:
  sessions.json   registry {sessions, by_host, active}
  active          plain text id (hook-friendly fallback)
  sessions/<id>/  that session's run state (orchestrator, mvp, pool, …)
  peers.json, worktrees/, sandboxes/  stay shared at the project `.superlite/`

Resolution order for the state dir: $SUPERLITE_SESSION, then by_host[$SUPERLITE_HOST_SESSION],
then registry/active, else the legacy flat `.superlite/` (no session started).
`start` always creates a new id (unless --id reuses an empty slot). Sessions clean up after
themselves: `end` (also run by `sl finish`) deletes `sessions/<id>/` and clears registry/active;
starting again with the same `--host-session` ends that host's previous session first; an empty
registry drops `sessions.json` and `active` so the tree is flat again. Stdlib only.
"""
# Complexity notation:
#   everything here is O(1) file work plus O(S) registry size
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
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")  # O(1)


def _looks_like_container(sl: Path) -> bool:  # O(1)
    """True when sl is a project `.superlite` that already holds run state (flat or sessions)."""
    return (sl / "orchestrator.json").is_file() or (sl / REGISTRY).is_file() or (sl / ACTIVE).is_file() or \
        (sl / SESSIONS).is_dir() or (sl / "peers.json").is_file() or (sl / "feature.json").is_file() or \
        (sl / "pool.json").is_file() or (sl / "prime.md").is_file() or (sl / "stable.json").is_file()  # O(1)


def _container_from_arg(plan_or_dir=None) -> Path:  # O(depth)
    """The project `.superlite` folder (never a sessions/<id> path, never a nested fake `.superlite`).

    A plan under `docs/plans/` binds to that package's `.superlite` (parent of `docs`), including a
    prototype sandbox's own tree — do not walk out to an outer checkout's `.superlite`.
    """
    if plan_or_dir is None:  # O(1)
        start = Path.cwd().resolve()  # O(1)
        for d in (start, *start.parents):  # O(depth)
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
        return Path(".superlite")  # O(1)

    p = Path(plan_or_dir).resolve()  # O(1)
    start = p if p.is_dir() else p.parent  # O(1)
    # Session state path first
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
    # Explicit .superlite path, or nearest existing container, else beside the path
    for d in (start, *start.parents):  # O(depth)
        if d.name == ".superlite":  # O(1)
            return d  # O(1)
        sl = d / ".superlite"  # O(1)
        if sl.is_dir() and _looks_like_container(sl):  # O(1)
            return sl  # O(1)
    return start / ".superlite"  # O(1)


def project_sl(plan_or_dir=None) -> Path:  # O(1)
    return _container_from_arg(plan_or_dir)  # O(1)


def project_root(plan_or_dir=None) -> Path:  # O(1)
    return project_sl(plan_or_dir).parent  # O(1)


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


def _write_registry(base: Path, data: dict) -> None:  # O(S)
    base.mkdir(parents=True, exist_ok=True)  # O(1)
    (base / REGISTRY).write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")  # O(S)
    active = data.get("active")  # O(1)
    af = base / ACTIVE  # O(1)
    if active:  # O(1)
        af.write_text(str(active) + "\n", encoding="utf-8")  # O(1)
    elif af.is_file():  # O(1)
        af.unlink()  # O(1)


def _new_id(base: Path) -> str:  # O(S)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")  # O(1)
    suffix = f"{os.getpid():x}-{os.urandom(2).hex()}"  # O(1)
    cand = f"{stamp}-{suffix}"  # O(1)
    reg = _read_registry(base)  # O(S)
    while cand in reg["sessions"] or (base / SESSIONS / cand).exists():  # O(1)
        cand = f"{stamp}-{os.urandom(3).hex()}"  # O(1)
        time.sleep(0)  # O(1)
    return cand  # O(1)


def _active_id(base: Path) -> str | None:  # O(S)
    env = (os.environ.get("SUPERLITE_SESSION") or "").strip()  # O(1)
    if env and ID_RE.match(env) and (base / SESSIONS / env).is_dir():  # O(1)
        return env  # O(1)
    host = (os.environ.get("SUPERLITE_HOST_SESSION") or "").strip()  # O(1)
    reg = _read_registry(base)  # O(S)
    if host and host in reg["by_host"]:  # O(1)
        sid = reg["by_host"][host]  # O(1)
        if (base / SESSIONS / sid).is_dir():  # O(1)
            return sid  # O(1)
    active = reg.get("active")  # O(1)
    if active and (base / SESSIONS / str(active)).is_dir():  # O(1)
        return str(active)  # O(1)
    try:  # O(1)
        aid = (base / ACTIVE).read_text(encoding="utf-8").strip()  # O(1)
    except OSError:  # O(1)
        aid = ""  # O(1)
    if aid and (base / SESSIONS / aid).is_dir():  # O(1)
        return aid  # O(1)
    return None  # O(1)


def state_dir(plan_or_dir=None) -> Path:  # O(S)
    """Directory that holds this run's orchestrator/mvp/pool (session or legacy flat)."""
    base = project_sl(plan_or_dir)  # O(1)
    sid = _active_id(base)  # O(S)
    if sid:  # O(1)
        return base / SESSIONS / sid  # O(1)
    return base  # O(1)


def current_id(plan_or_dir=None) -> str | None:  # O(S)
    return _active_id(project_sl(plan_or_dir))  # O(S)


def id_from_path(path) -> str | None:  # O(1)
    """Session id when path is inside `.superlite/sessions/<id>/…`, else None."""
    p = Path(path).resolve()  # O(1)
    parts = p.parts  # O(d)
    for i, part in enumerate(parts):  # O(d)
        if part == SESSIONS and i + 1 < len(parts) and i > 0 and parts[i - 1] == ".superlite":  # O(1)
            return parts[i + 1]  # O(1)
    return None  # O(1)


def start(plan_or_dir=None, host_session: str | None = None, session_id: str | None = None) -> tuple[str, Path]:  # O(S + tree)
    """Create a new session dir (or reuse --id), mark it active, return (id, path).

    Same `--host-session` / `$SUPERLITE_HOST_SESSION` again ends that host's previous session first
    so restarts do not leave orphan trees.
    """
    base = project_sl(plan_or_dir)  # O(1)
    base.mkdir(parents=True, exist_ok=True)  # O(1)
    reg = _read_registry(base)  # O(S)
    host = (host_session or os.environ.get("SUPERLITE_HOST_SESSION") or "").strip() or None  # O(1)
    sid = (session_id or "").strip()  # O(1)
    if sid:  # O(1)
        if not ID_RE.match(sid):  # O(1)
            raise ValueError(f"invalid session id: {sid}")  # O(1)
    else:  # O(1)
        sid = _new_id(base)  # O(S)
    # Restart cleanup: this host already owned a different session → end it first.
    if host and host in reg["by_host"] and reg["by_host"][host] != sid:  # O(1)
        end(plan_or_dir, session_id=reg["by_host"][host])  # O(tree)
        reg = _read_registry(base)  # O(S)
    path = base / SESSIONS / sid  # O(1)
    path.mkdir(parents=True, exist_ok=True)  # O(1)
    (path / "mvp").mkdir(exist_ok=True)  # O(1)
    reg["sessions"][sid] = {  # O(1)
        "created": datetime.now().isoformat(timespec="seconds"),  # O(1)
        "host_session": host,  # O(1)
    }  # O(1)
    if host:  # O(1)
        reg["by_host"][host] = sid  # O(1)
    reg["active"] = sid  # O(1)
    _write_registry(base, reg)  # O(S)
    os.environ["SUPERLITE_SESSION"] = sid  # so this process and children keep the same session | O(1)
    return sid, path  # O(1)


def end(plan_or_dir=None, session_id: str | None = None) -> str:  # O(tree)
    """Delete sessions/<id> and clear registry pointers. Returns a one-line status.

    Resolves id from (1) explicit session_id, (2) active/env, (3) plan_or_dir when it points
    inside a sessions/<id> tree (so `finish` cleans up from the ledger path alone).
    """
    base = project_sl(plan_or_dir)  # O(1)
    reg = _read_registry(base)  # O(S)
    sid = (session_id or "").strip()  # O(1)
    if not sid and plan_or_dir is not None:  # O(1)
        sid = id_from_path(plan_or_dir) or ""  # O(d)
    if not sid:  # O(1)
        sid = (_active_id(base) or "").strip()  # O(S)
    if not sid:  # O(1)
        return "session: none"  # O(1)
    path = base / SESSIONS / sid  # O(1)
    if path.is_dir():  # O(1)
        shutil.rmtree(path, ignore_errors=True)  # O(tree)
    reg["sessions"].pop(sid, None)  # O(1)
    reg["by_host"] = {h: s for h, s in reg["by_host"].items() if s != sid}  # O(H)
    if reg.get("active") == sid:  # O(1)
        reg["active"] = None  # O(1)
    if os.environ.get("SUPERLITE_SESSION") == sid:  # O(1)
        os.environ.pop("SUPERLITE_SESSION", None)  # O(1)
    # Fully flat again when nothing remains: drop registry + active + empty sessions/
    if not reg["sessions"]:  # O(1)
        for name in (REGISTRY, ACTIVE):  # O(1)
            f = base / name  # O(1)
            if f.is_file():  # O(1)
                f.unlink()  # O(1)
        sroot = base / SESSIONS  # O(1)
        if sroot.is_dir() and not any(sroot.iterdir()):  # O(1)
            sroot.rmdir()  # O(1)
        return f"session: ended {sid}"  # O(1)
    _write_registry(base, reg)  # O(S)
    sroot = base / SESSIONS  # O(1)
    if sroot.is_dir() and not any(sroot.iterdir()):  # O(1)
        sroot.rmdir()  # O(1)
    return f"session: ended {sid}"  # O(1)


def status_line(plan_or_dir=None) -> str:  # O(S)
    base = project_sl(plan_or_dir)  # O(1)
    sid = _active_id(base)  # O(S)
    reg = _read_registry(base)  # O(S)
    n = len(reg["sessions"])  # O(1)
    if not sid:  # O(1)
        return f"session: none (legacy flat .superlite; {n} registered)"  # O(1)
    return f"session: {sid} path={base / SESSIONS / sid} registered={n}"  # O(1)


def main(argv=None) -> int:  # O(S)
    ap = argparse.ArgumentParser(description=__doc__.split(chr(10))[0])  # O(1)
    sub = ap.add_subparsers(dest="cmd", required=True)  # O(1)
    s = sub.add_parser("start")  # O(1)
    s.add_argument("--host-session", help="harness session id to bind (also $SUPERLITE_HOST_SESSION)")  # O(1)
    s.add_argument("--id", help="reuse this session id (must match [A-Za-z0-9._-]+)")  # O(1)
    e = sub.add_parser("end")  # O(1)
    e.add_argument("--id", help="session to end (default: active)")  # O(1)
    for name in ("status", "path"):  # O(1)
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
    if a.cmd == "end":  # O(1)
        print(end(a.plan, session_id=getattr(a, "id", None)))  # O(tree)
        return 0  # O(1)
    if a.cmd == "path":  # O(1)
        print(state_dir(a.plan))  # O(S)
        return 0  # O(1)
    print(status_line(a.plan))  # O(S)
    return 0  # O(1)


if __name__ == "__main__":  # O(1)
    sys.exit(main())  # O(1)
