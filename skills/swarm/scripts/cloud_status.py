"""Status line for live cloud runs: wraps the user's own status line, adds one cloud segment.

install/restore edit ~/.claude/settings.json and put it back byte for byte. Never prints the token.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import state  # noqa: E402

SYNC_EVERY = 15.0
_PREV_NAME = "superlite-statusline-prev.json"


def _home() -> Path:
    h = os.environ.get("SUPERLITE_HOME")
    return Path(h) if h else Path.home() / state.DIR_NAME


def _claude_home() -> Path:
    h = os.environ.get("CLAUDE_HOME")
    return Path(h) if h else Path.home() / ".claude"


def _side(settings: Path) -> Path:
    return settings.with_name(_PREV_NAME)


def _memory(side: Path) -> dict | None:
    try:
        mem = json.loads(side.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return mem if isinstance(mem, dict) else None


def _is_ours(cur: dict, mem: dict, command: str) -> bool:
    """The statusLine is one of our wrappers: the new command, or the one the first install wrote."""
    if cur.get("command") == command:
        return True
    try:
        written = json.loads(mem.get("written") or "")["statusLine"]
    except (ValueError, KeyError, TypeError):
        return False
    return isinstance(written, dict) and written.get("command") == cur.get("command")


def install(settings: Path, command: str) -> None:
    """Point statusLine at command, remembering what was there. Calling it twice keeps the first memory."""
    settings = Path(settings)
    side = _side(settings)
    raw = settings.read_bytes() if settings.exists() else None
    data = json.loads(raw.decode("utf-8")) if raw and raw.strip() else {}
    if not isinstance(data, dict):
        raise ValueError("settings.json is not an object")
    cur = data.get("statusLine")
    mem = _memory(side)
    if isinstance(cur, dict) and cur.get("command") == command and side.exists():
        return
    if isinstance(cur, dict) and mem is not None and _is_ours(cur, mem, command):
        if cur.get("command") != command:  # a newer plugin install: keep the original memory, update the command
            data["statusLine"] = {"type": "command", "command": command}
            out = (json.dumps(data, indent=2) + "\n").encode("utf-8")
            # byte-identical restore only while settings.json is still as the first install left it
            mem["written"] = out.decode("utf-8") if raw.decode("utf-8") == mem.get("written") else ""
            side.write_text(json.dumps(mem), encoding="utf-8")
            settings.write_bytes(out)
        return
    memory = {"existed": raw is not None, "orig": raw.decode("utf-8") if raw is not None else None,
              "had": "statusLine" in data, "prev": cur}
    data["statusLine"] = {"type": "command", "command": command}
    out = (json.dumps(data, indent=2) + "\n").encode("utf-8")
    memory["written"] = out.decode("utf-8")
    side.parent.mkdir(parents=True, exist_ok=True)
    side.write_text(json.dumps(memory), encoding="utf-8")
    settings.write_bytes(out)


def restore(settings: Path) -> None:
    """Undo install. Byte-identical when settings.json is as install left it; else only statusLine is reverted."""
    settings = Path(settings)
    side = _side(settings)
    if not side.exists():
        return
    try:
        mem = json.loads(side.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        side.unlink(missing_ok=True)
        return
    cur = settings.read_bytes().decode("utf-8") if settings.exists() else None
    if cur == mem.get("written"):
        if mem.get("existed"):
            settings.write_bytes(mem["orig"].encode("utf-8"))
        else:
            settings.unlink(missing_ok=True)
    elif cur is not None:
        try:
            data = json.loads(cur)
        except ValueError:
            data = None
        if isinstance(data, dict):
            if mem.get("had"):
                data["statusLine"] = mem.get("prev")
            else:
                data.pop("statusLine", None)
            settings.write_bytes((json.dumps(data, indent=2) + "\n").encode("utf-8"))
    side.unlink(missing_ok=True)


def _attached_name(client) -> str | None:
    """The attached run's current name, or None when nothing is attached or the run has ended (then detach)."""
    import cloud_hook
    live = cloud_hook.refresh_attached(client)
    if not live:
        return None
    if live.get("lost") or live.get("status") in cloud_hook.ENDED:
        cloud_hook.clear_attach()
        return None
    return live["run"]


def segment(status: dict, attached: str | None = None) -> str:
    new = next((f"new: {n.get('text', '')}" for n in status.get("notices") or [] if not n.get("seen")), "")
    if attached:
        return f"ATTACHED {attached} · {new}" if new else f"ATTACHED {attached}"
    if new:
        return new
    runs = status.get("runs") or []
    for r in runs:
        ev = r.get("last_event") or {}
        if ev.get("type") == "rename":
            return f"renamed {ev.get('from')} to {ev.get('to')}"
    for r in runs:
        if r.get("status") == "running":
            parts = [f"{r['name']} running"]
            if r.get("step") is not None:
                parts.append(f"step {r['step']}")
            if r.get("doing"):
                parts.append(str(r["doing"]))
            return " · ".join(parts)
    if runs:
        return f"{runs[0]['name']} {runs[0].get('status', '')}".strip()
    return ""


def _prev_output(stdin_text: str) -> str:
    try:
        mem = json.loads(_side(_claude_home() / "settings.json").read_text(encoding="utf-8"))
        prev = mem.get("prev")
        cmd = prev.get("command") if isinstance(prev, dict) else None
    except (OSError, ValueError, AttributeError):
        return ""
    if not cmd:
        return ""
    try:
        r = subprocess.run(cmd, shell=True, input=stdin_text, capture_output=True, text=True, timeout=5)
        return r.stdout.strip("\n")
    except (OSError, subprocess.SubprocessError):
        return ""


def _maybe_sync(client, now=time.time, status: dict | None = None) -> None:
    stamp = _home() / "entries-sync"
    try:
        if now() - stamp.stat().st_mtime < SYNC_EVERY:
            return
    except OSError:
        pass
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(str(now()), encoding="utf-8")
    import cloud_entries
    me = client.me()
    # /api/status, never /api/tasks: the server marks notices seen on a tasks call.
    st = status if isinstance(status, dict) else client.status()
    runs = [{"owner": me.get("handle", ""), **r} for r in (st.get("runs") or []) if isinstance(r, dict)]
    if me.get("role") == "admin":
        return  # /api/status lists only the caller's live runs: syncing it would drop the entries of others' runs
    cloud_entries.sync(runs, _claude_home() / "skills", me.get("handle", ""), False)


def _make_client():
    import cloud_client
    import cloud_config
    cfg = cloud_config.load()
    if not cfg or not cfg.get("url") or not cfg.get("token"):
        return None
    return cloud_client.Client(cfg["url"], cfg["token"], 2.0)


def main(argv: list[str] | None = None) -> int:
    try:
        stdin_text = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
    except OSError:
        stdin_text = ""
    prev = _prev_output(stdin_text)
    seg = ""
    try:
        client = _make_client()
        if client is not None:
            st = client.status()
            seg = segment(st, _attached_name(client))
            try:
                _maybe_sync(client, status=st)
            except Exception:
                pass
    except Exception:
        seg = ""
    print(" · ".join(x for x in (prev, seg) if x))
    return 0


if __name__ == "__main__":
    sys.exit(main())
