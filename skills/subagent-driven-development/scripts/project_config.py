#!/usr/bin/env python3
"""Load `superlite.json` at the root of the project being built.

Missing file means shipped defaults (`{}`). Bad JSON, a version other than 1, or an
unknown key (including `skills`) raises ProjectConfigError. Callers exit 2.
Stdlib only.
"""
from __future__ import annotations

import json
from pathlib import Path

KEYS = frozenset({"version", "cap", "gitignore", "hosts", "testprobe", "models"})
DEFAULT_CAP = 10


class ProjectConfigError(Exception):
    """The overlay file is present and unusable. `path` is the file."""

    def __init__(self, path: Path, message: str):
        self.path = Path(path)
        super().__init__(f"{self.path}: {message}")


def overlay_path(root) -> Path:
    return Path(root).resolve() / "superlite.json"


def load(root) -> dict:
    """The overlay object, or {} when the file is absent."""
    path = overlay_path(root)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ProjectConfigError(path, "not valid JSON") from exc
    except OSError as exc:
        raise ProjectConfigError(path, "not valid JSON") from exc
    if not isinstance(data, dict):
        raise ProjectConfigError(path, "not a JSON object")
    unknown = sorted(k for k in data if k not in KEYS)
    if unknown:
        raise ProjectConfigError(path, f"unknown key {unknown[0]}")
    if data.get("version") != 1:
        raise ProjectConfigError(path, "version must be 1")
    return data


def effective_cap(explicit: int | None, root) -> int:
    """An explicit cap wins, including 0. Otherwise the overlay int, otherwise 10.

    Always reads the overlay, so a bad file fails closed even when `--cap` is passed.
    """
    data = load(root)
    if explicit is not None:
        return int(explicit)
    if "cap" not in data:
        return DEFAULT_CAP
    cap = data["cap"]
    if isinstance(cap, bool) or not isinstance(cap, int):
        raise ProjectConfigError(overlay_path(root), "cap must be an integer")
    return cap


def language(root) -> str:
    """Project language from test-cmd.json, else marker detection. Absent means python."""
    import testcmd

    info = testcmd.resolve(root)
    if not info or info.get("python"):
        return "python"
    lang = str(info.get("language") or "").strip().lower()
    return lang or "python"


def apply_project_prices(models: dict, root) -> dict:
    """Deep-copy `models`. Numeric overlay fields replace the same fields on existing names.

    `source` is `project` only on a model that received at least one numeric field.
    """
    import copy

    data = copy.deepcopy(models)
    overlay = load(root).get("models")
    if not isinstance(overlay, dict):
        return data
    table = data.get("models")
    if not isinstance(table, dict):
        return data
    for name, fields in overlay.items():
        model = table.get(name)
        if not isinstance(model, dict) or not isinstance(fields, dict):
            continue
        touched = False
        for key, value in fields.items():
            if key == "source" or isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            model[key] = value
            touched = True
        if touched:
            model["source"] = "project"
    return data
