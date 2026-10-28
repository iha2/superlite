#!/usr/bin/env python3
"""Suspend peer process plugins for the length of a superlite run.

    exclusive.py on [--plan P]
    exclusive.py off [--plan P]
    exclusive.py status [--plan P]

`on` (also run by `sl orchestrator set`) writes `.superlite/peers.json` and, on a
Claude Code project, sets enabledPlugins["superpowers@claude-plugins-official"]
to false in `.claude/settings.local.json`. Other keys in that file stay. The
previous value is kept in peers.json. `off` (also run by `sl finish`) removes
the marker and restores that value, so superpowers and anything like it can be
used again. A second `on` does not overwrite the saved value. Unreadable
settings are left untouched. Stdlib only.
"""
# Complexity notation:
#   everything here is O(1) file work, plus O(S) to read and write the settings file
import argparse  # O(1)
import json  # O(1)
import os  # O(1)
import sys  # O(1)
from datetime import datetime  # O(1)
from pathlib import Path  # O(1)

sys.path.insert(0, str(Path(__file__).resolve().parent))  # O(1)
import orchestrator  # noqa: E402 | sl_dir | O(1)
import session as sl_session  # noqa: E402 | project_sl / project_root | O(1)

FILE = "peers.json"  # O(1)
PEER_ID = "superpowers@claude-plugins-official"  # the installed Claude Code plugin id | O(1)
PEER_NAME = "superpowers"  # O(1)
SUSPENDED = "peers: suspended superpowers until this run finishes"  # O(1)
CLEAR = "peers: clear (superpowers may be used again)"  # O(1)


def peers_path(plan_or_dir=None) -> Path:  # O(1)
    """Peers are project-wide (one suspend for the checkout), never per-session."""
    return sl_session.project_sl(plan_or_dir) / FILE  # O(1)


def read_peers(plan_or_dir=None):  # O(file)
    try:  # O(1)
        d = json.loads(peers_path(plan_or_dir).read_text(encoding="utf-8"))  # O(file)
    except (OSError, ValueError):  # O(1)
        return None  # O(1)
    return d if isinstance(d, dict) else None  # O(1)


def suspended(plan_or_dir=None) -> bool:  # O(file)
    d = read_peers(plan_or_dir)  # O(file)
    return bool(d and d.get("suspended") is True)  # O(1)


def settings_path(plan_or_dir=None) -> Path:  # O(1)
    return sl_session.project_root(plan_or_dir) / ".claude" / "settings.local.json"  # O(1)


def claude_host(plan_or_dir=None) -> bool:  # O(1)
    """True when this project is a Claude Code host, so writing its settings is in scope."""
    if os.environ.get("CLAUDE_PLUGIN_ROOT") or os.environ.get("CLAUDE_CODE"):  # O(1)
        return True  # O(1)
    return (sl_session.project_root(plan_or_dir) / ".claude").is_dir()  # O(1) stat


def read_settings(path: Path):  # O(S)
    """The settings object, {} when the file is absent, None when it must not be rewritten."""
    if not path.is_file():  # O(1)
        return {}  # O(1)
    try:  # O(1)
        d = json.loads(path.read_text(encoding="utf-8"))  # O(S)
    except (OSError, ValueError):  # O(1)
        return None  # O(1)
    return d if isinstance(d, dict) else None  # O(1)


def current_enabled(plan_or_dir=None):  # O(S)
    """(skip, previous). skip means the settings file is not ours to rewrite."""
    if not claude_host(plan_or_dir):  # O(1)
        return True, None  # O(1)
    data = read_settings(settings_path(plan_or_dir))  # O(S)
    if data is None:  # O(1)
        return True, None  # O(1)
    plugins = data.get("enabledPlugins")  # O(1)
    if not isinstance(plugins, dict) or PEER_ID not in plugins:  # O(1)
        return False, None  # O(1)
    return False, bool(plugins[PEER_ID])  # O(1)


def write_enabled(plan_or_dir, value) -> None:  # O(S)
    """Set the peer plugin flag. None removes the key. No-op when the file is not writable JSON."""
    if not claude_host(plan_or_dir):  # O(1)
        return  # O(1)
    path = settings_path(plan_or_dir)  # O(1)
    data = read_settings(path)  # O(S)
    if data is None:  # O(1)
        return  # O(1)
    plugins = data.get("enabledPlugins")  # O(1)
    plugins = dict(plugins) if isinstance(plugins, dict) else {}  # O(1)
    if value is None:  # O(1)
        plugins.pop(PEER_ID, None)  # O(1)
    else:  # O(1)
        plugins[PEER_ID] = value  # O(1)
    if plugins:  # O(1)
        data["enabledPlugins"] = plugins  # O(1)
    else:  # O(1)
        data.pop("enabledPlugins", None)  # O(1)
    if not data:  # O(1)
        if path.is_file():  # O(1)
            path.unlink()  # O(1)
        return  # O(1)
    path.parent.mkdir(parents=True, exist_ok=True)  # O(1)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")  # O(S)


def suspend(plan_or_dir=None) -> str:  # O(S)
    """Mark peer plugins suspended. A second call keeps the saved previous flag."""
    cur = read_peers(plan_or_dir)  # O(file)
    if cur and cur.get("suspended") is True:  # O(1)
        return SUSPENDED  # O(1)
    skip, previous = current_enabled(plan_or_dir)  # O(S)
    state = {  # O(1)
        "suspended": True,  # O(1)
        "peers": [PEER_NAME],  # O(1)
        "since": datetime.now().isoformat(timespec="seconds"),  # O(1)
        "claude_plugin": PEER_ID,  # O(1)
        "settings_skip": skip,  # O(1)
        "previous_enabled": previous,  # O(1)
    }  # O(1)
    d = sl_session.project_sl(plan_or_dir)  # O(1)
    d.mkdir(parents=True, exist_ok=True)  # O(1)
    if not skip:  # O(1)
        write_enabled(plan_or_dir, False)  # O(S)
    (d / FILE).write_text(json.dumps(state, indent=1) + "\n", encoding="utf-8")  # O(1)
    return SUSPENDED  # O(1)


def release(plan_or_dir=None) -> str:  # O(S)
    """Clear the suspension and restore the peer plugin flag saved at suspend."""
    cur = read_peers(plan_or_dir)  # O(file)
    path = peers_path(plan_or_dir)  # O(1)
    if path.is_file():  # O(1)
        path.unlink()  # O(1)
    if cur and cur.get("suspended") is True and not cur.get("settings_skip"):  # O(1)
        write_enabled(plan_or_dir, cur.get("previous_enabled"))  # None deletes the key | O(S)
    return CLEAR  # O(1)


def status_line(plan_or_dir=None) -> str:  # O(file)
    if suspended(plan_or_dir):  # O(file)
        return SUSPENDED  # O(1)
    return CLEAR  # O(1)


def main(argv=None):  # O(S)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])  # O(1)
    sub = ap.add_subparsers(dest="cmd", required=True)  # O(1)
    for name in ("on", "off", "status"):  # O(1)
        p = sub.add_parser(name)  # O(1)
        p.add_argument("--plan", help="plan path (locates .superlite); default ./.superlite")  # O(1)
    a = ap.parse_args(argv)  # O(1)
    if a.cmd == "on":  # O(1)
        print(suspend(a.plan))  # O(S)
    elif a.cmd == "off":  # O(1)
        print(release(a.plan))  # O(S)
    else:  # O(1)
        print(status_line(a.plan))  # O(file)
    return 0  # O(1)


if __name__ == "__main__":  # O(1)
    sys.exit(main())  # O(1)
