#!/usr/bin/env python3
"""Opt-in pre-implementation specs for feature (and later) flows.

The open_in_harness flag covers both finished plans and specs.

    agent_specs.py on|off|status
    agent_specs.py open on|off
    agent_specs.py prefer on|off

Default is OFF. Env SUPERLITE_AGENT_SPECS wins over project
`.superlite/agent-specs.json`, which wins over `$SUPERLITE_HOME/preferences.json`
(default per history.home(): XDG data dir, or an existing ~/.superlite). `open_in_harness` (open plans and specs in the
harness) defaults ON, whether specs are on or off; toggle with `open on|off` (project) or prefer's global file.
`prefer` records the install/first-session choice in preferences.json so
session-start asks only once.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from history import home  # noqa: E402  one rule for the global home

PROJECT = Path(".superlite/agent-specs.json")
TRUE, FALSE = ("1", "true", "yes", "on"), ("0", "false", "no", "off")


def preferences_path() -> Path:
    return home() / "preferences.json"


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")


def _env_bool(name: str) -> bool | None:
    env = os.environ.get(name, "").strip().lower()
    if env in TRUE:
        return True
    if env in FALSE and env:
        return False
    return None


def prefer_recorded() -> bool:
    """True once the user has answered the first-session prefer question."""
    return "agent_specs" in _read_json(preferences_path())


def preferred_on() -> bool | None:
    """Global prefer from preferences.json, or None if never set."""
    data = _read_json(preferences_path())
    if "agent_specs" not in data:
        return None
    return bool(data.get("agent_specs"))


def is_on() -> bool:
    """Env, then project file, then global prefer; default off."""
    env = _env_bool("SUPERLITE_AGENT_SPECS")
    if env is not None:
        return env
    proj = _read_json(PROJECT)
    if "agent_specs" in proj:
        return bool(proj.get("agent_specs"))
    pref = preferred_on()
    if pref is not None:
        return pref
    return False


def open_in_harness() -> bool:
    """Whether to open a finished plan or spec in the harness. Default on.

    Env SUPERLITE_AGENT_SPECS_OPEN wins, then SUPERLITE_OPEN_IN_HARNESS,
    then project, then preferences, else True.
    """
    env = _env_bool("SUPERLITE_AGENT_SPECS_OPEN")
    if env is None:
        env = _env_bool("SUPERLITE_OPEN_IN_HARNESS")
    if env is not None:
        return env
    proj = _read_json(PROJECT)
    if "open_in_harness" in proj:
        return bool(proj.get("open_in_harness"))
    pref = _read_json(preferences_path())
    if "open_in_harness" in pref:
        return bool(pref.get("open_in_harness"))
    return True


def set_project(on: bool | None = None, open_flag: bool | None = None) -> None:
    data = _read_json(PROJECT)
    if on is not None:
        data["agent_specs"] = bool(on)
    if open_flag is not None:
        data["open_in_harness"] = bool(open_flag)
    _write_json(PROJECT, data)


def set_prefer(on: bool, open_flag: bool | None = None) -> None:
    data = _read_json(preferences_path())
    data["agent_specs"] = bool(on)
    if open_flag is not None:
        data["open_in_harness"] = bool(open_flag)
    elif "open_in_harness" not in data:
        data["open_in_harness"] = True
    _write_json(preferences_path(), data)


def status_lines() -> list[str]:
    on = is_on()
    open_h = open_in_harness()
    lines = [
        f"agent_specs: {'on' if on else 'off'}",
        f"open_in_harness: {'on' if open_h else 'off'}",
    ]
    env = os.environ.get("SUPERLITE_AGENT_SPECS", "").strip()
    if env:
        lines.append(f"source: env SUPERLITE_AGENT_SPECS={env}")
    elif PROJECT.is_file() and "agent_specs" in _read_json(PROJECT):
        lines.append(f"source: {PROJECT.as_posix()}")
    elif prefer_recorded():
        lines.append(f"source: {preferences_path().as_posix()} (prefer)")
    else:
        lines.append("source: default (off)")
    return lines


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        argv = ["status"]
    cmd = argv[0]
    if cmd in ("on", "off"):
        set_project(on=(cmd == "on"))
        print(f"agent_specs {cmd}")
        return 0
    if cmd == "open":
        if len(argv) < 2 or argv[1] not in ("on", "off"):
            print("usage: agent_specs open on|off", file=sys.stderr)
            return 2
        set_project(open_flag=(argv[1] == "on"))
        print(f"agent_specs open {argv[1]}")
        return 0
    if cmd == "prefer":
        if len(argv) < 2 or argv[1] not in ("on", "off"):
            print("usage: agent_specs prefer on|off", file=sys.stderr)
            return 2
        set_prefer(argv[1] == "on")
        print(f"agent_specs prefer {argv[1]} ({preferences_path().as_posix()})")
        return 0
    if cmd == "status":
        print("\n".join(status_lines()))
        return 0
    print(
        __doc__.split("\n")[0]
        + "\nusage: agent_specs.py on|off|status|open on|off|prefer on|off",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
