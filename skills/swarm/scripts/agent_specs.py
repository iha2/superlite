#!/usr/bin/env python3
"""Opt-in pre-implementation specs for feature (and later) flows.

The open_in_harness flag covers both finished plans and specs.

    agent_specs.py on|off|status
    agent_specs.py open on|off
    agent_specs.py prefer on|off

Default is OFF. Env SUPERLITE_AGENT_SPECS wins over project
`.superlite/agent-specs.json`, which wins over `$SUPERLITE_HOME/preferences.json`
(default per history.home(): the XDG data dir). `open_in_harness` (open plans and specs in the
harness) defaults ON, whether specs are on or off; toggle with `open on|off` (project) or prefer's global file.
`prefer` records the install/first-session choice in preferences.json so
session-start asks only once.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402
import state  # noqa: E402
from history import home  # noqa: E402  one rule for the global home

SPECS_ENV, OPEN_ENVS = "SUPERLITE_AGENT_SPECS", ("SUPERLITE_AGENT_SPECS_OPEN", "SUPERLITE_OPEN_IN_HARNESS")


def preferences_path() -> Path:
    return home() / "preferences.json"


def project_path() -> Path:
    return state.path(Path.cwd(), "agent-specs.json")


def _read(path: Path) -> dict:
    data = base.read_json(path, {})
    return data if isinstance(data, dict) else {}


def _write(path: Path, data: dict) -> None:
    base.write_json(path, data)


def prefer_recorded() -> bool:
    """True once the user has answered the first-session prefer question."""
    return "agent_specs" in _read(preferences_path())


def preferred_on() -> bool | None:
    """Global prefer from preferences.json, or None if never set."""
    data = _read(preferences_path())
    return bool(data["agent_specs"]) if "agent_specs" in data else None


def is_on() -> bool:
    """Env, then project file, then global prefer; default off."""
    return base.flag(SPECS_ENV, project_path(), "agent_specs", bool(preferred_on()))


def open_in_harness() -> bool:
    """Whether to open a finished plan or spec in the harness. Default on.

    Env SUPERLITE_AGENT_SPECS_OPEN wins, then SUPERLITE_OPEN_IN_HARNESS, then project, then preferences, else True.
    """
    env = next((e for e in OPEN_ENVS if os.environ.get(e, "").strip().lower() in base.TRUE + base.FALSE), OPEN_ENVS[0])
    fallback = base.flag("", preferences_path(), "open_in_harness", True)
    return base.flag(env, project_path(), "open_in_harness", fallback)


def set_project(on: bool | None = None, open_flag: bool | None = None) -> None:
    data = _read(project_path())
    if on is not None:
        data["agent_specs"] = bool(on)
    if open_flag is not None:
        data["open_in_harness"] = bool(open_flag)
    _write(project_path(), data)


def set_prefer(on: bool, open_flag: bool | None = None) -> None:
    data = _read(preferences_path())
    data["agent_specs"] = bool(on)
    if open_flag is not None:
        data["open_in_harness"] = bool(open_flag)
    elif "open_in_harness" not in data:
        data["open_in_harness"] = True
    _write(preferences_path(), data)


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
    elif "agent_specs" in _read(project_path()):
        lines.append(f"source: {os.path.relpath(project_path()).replace(os.sep, '/')}")
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
