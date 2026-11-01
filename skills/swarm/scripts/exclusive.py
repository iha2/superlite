#!/usr/bin/env python3
"""Suspend peer process plugins for the length of a superlite run.

    exclusive.py on [--plan P]
    exclusive.py off [--plan P]
    exclusive.py status [--plan P]

`on` (also run by `superlite-cli orchestrator set`) writes `.superlite/peers.json` and, on a
Claude Code project, sets enabledPlugins["superpowers@claude-plugins-official"]
to false in `.claude/settings.local.json`. Other keys in that file stay. The
previous value is kept in peers.json. `off` (also run by `superlite-cli finish`) removes
the marker and restores that value once no other session's run is open
(the last run to finish releases all), so superpowers and anything like it can be used again. A second `on` does not overwrite the saved value. Unreadable
settings are left untouched. Stdlib only.
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402
import orchestrator  # noqa: E402 | sl_dir
import session_registry  # noqa: E402
import state  # noqa: E402

FILE = "peers.json"
PEER_ID = "superpowers@claude-plugins-official"  # the installed Claude Code plugin id
PEER_NAME = "superpowers"
SUSPENDED = "peers: suspended superpowers until this run finishes"
CLEAR = "peers: clear (superpowers may be used again)"


def peers_path(plan_or_dir=None) -> Path:
    return orchestrator.sl_dir(plan_or_dir) / FILE


def read_peers(plan_or_dir=None):  # O(file)
    try:
        d = json.loads(peers_path(plan_or_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def suspended(plan_or_dir=None) -> bool:  # O(file)
    d = read_peers(plan_or_dir)
    return bool(d and d.get("suspended") is True)


def settings_path(plan_or_dir=None) -> Path:
    return orchestrator.sl_dir(plan_or_dir).parent / ".claude" / "settings.local.json"


def claude_host(plan_or_dir=None) -> bool:
    """True when this project is a Claude Code host, so writing its settings is in scope."""
    if os.environ.get("CLAUDE_PLUGIN_ROOT") or os.environ.get("CLAUDE_CODE"):
        return True
    return (orchestrator.sl_dir(plan_or_dir).parent / ".claude").is_dir()


def read_settings(path: Path):  # O(S)
    """The settings object, {} when the file is absent, None when it must not be rewritten."""
    if not path.is_file():
        return {}
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def current_enabled(plan_or_dir=None):  # O(S)
    """(skip, previous). skip means the settings file is not ours to rewrite."""
    if not claude_host(plan_or_dir):
        return True, None
    data = read_settings(settings_path(plan_or_dir))
    if data is None:
        return True, None
    plugins = data.get("enabledPlugins")
    if not isinstance(plugins, dict) or PEER_ID not in plugins:
        return False, None
    return False, bool(plugins[PEER_ID])


def write_enabled(plan_or_dir, value) -> None:  # O(S)
    """Set the peer plugin flag. None removes the key. No-op when the file is not writable JSON."""
    if not claude_host(plan_or_dir):
        return
    path = settings_path(plan_or_dir)
    data = read_settings(path)
    if data is None:
        return
    plugins = data.get("enabledPlugins")
    plugins = dict(plugins) if isinstance(plugins, dict) else {}
    if value is None:
        plugins.pop(PEER_ID, None)
    else:
        plugins[PEER_ID] = value
    if plugins:
        data["enabledPlugins"] = plugins
    else:
        data.pop("enabledPlugins", None)
    if not data:
        if path.is_file():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def suspend(plan_or_dir=None) -> str:  # O(S)
    """Mark peer plugins suspended. A second call keeps the saved previous flag."""
    d = orchestrator.sl_dir(plan_or_dir)

    def mark(cur):
        if cur.get("suspended") is True:
            return cur
        skip, previous = current_enabled(plan_or_dir)
        if not skip:
            write_enabled(plan_or_dir, False)
        return {"suspended": True, "peers": [PEER_NAME], "since": datetime.now().isoformat(timespec="seconds"),
                "claude_plugin": PEER_ID, "settings_skip": skip, "previous_enabled": previous}
    base.locked_update(d / FILE, mark)
    return SUSPENDED


def other_open_runs(plan_or_dir=None) -> list:  # O(N)
    """Slugs of the sessions other than this checkout's whose run is still open; [] without a registry."""
    sl = orchestrator.sl_dir(plan_or_dir)
    try:
        hub = state.hub_root(sl.parent)
        here = Path(base.git("rev-parse", "--show-toplevel", cwd=sl.parent).stdout.strip())
    except base.GitError:
        return []
    reg = session_registry.read(hub)
    mine = session_registry.row_at(reg, here, hub)
    return [s["slug"] for s in reg["sessions"]
            if session_registry.is_open(s) and s["slug"] != (mine or {}).get("slug")]


def _release_here(sl: Path) -> None:  # O(S)
    """Restore the saved flag and drop the marker for the checkout whose run folder is sl."""
    def clear(cur):
        if cur.get("suspended") is True and not cur.get("settings_skip"):
            write_enabled(sl, cur.get("previous_enabled"))  # None deletes the key
        return {}
    base.locked_update(sl / FILE, clear)
    (sl / FILE).unlink(missing_ok=True)


def release(plan_or_dir=None) -> str:  # O(N)
    """Clear the suspension once the last open run finishes; until then the peers stay off. Runs under the registry lock."""
    sl = orchestrator.sl_dir(plan_or_dir)
    try:
        hub = state.hub_root(sl.parent)
    except base.GitError:
        return _release(plan_or_dir, sl)
    with session_registry.registry_lock(hub):
        return _release(plan_or_dir, sl)


def _release(plan_or_dir, sl: Path) -> str:  # O(N)
    others = other_open_runs(plan_or_dir)
    if others:
        return f"{SUSPENDED} ({len(others)} other run{'s' if len(others) != 1 else ''} open: {', '.join(others)})"
    _release_here(sl)
    for wt in _linked_checkouts(sl):  # peers an earlier finisher kept for this run
        run = state.run_dir(wt)
        if run != sl and (run / FILE).is_file():
            _release_here(run)
    return CLEAR


def _linked_checkouts(sl: Path) -> list:  # O(N)
    try:
        reg = session_registry.read(state.hub_root(sl.parent))
    except base.GitError:
        return []
    return [Path(s["worktree"]) for s in reg["sessions"] if s.get("worktree") and Path(s["worktree"]).is_dir()]


def status_line(plan_or_dir=None) -> str:  # O(file)
    if suspended(plan_or_dir):
        return SUSPENDED
    return CLEAR


def main(argv=None):  # O(S)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("on", "off", "status"):
        p = sub.add_parser(name)
        p.add_argument("--plan", help=f"plan path (locates {state.DIR_NAME}); default ./{state.DIR_NAME}")
    a = ap.parse_args(argv)
    if a.cmd == "on":
        print(suspend(a.plan))
    elif a.cmd == "off":
        print(release(a.plan))
    else:
        print(status_line(a.plan))
    return 0


if __name__ == "__main__":
    sys.exit(main())
