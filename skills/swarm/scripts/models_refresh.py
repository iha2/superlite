#!/usr/bin/env python3
"""Refresh config/models.json rates from a local feed file (fixture or downloaded prices).

    models_refresh.py refresh --dest PATH --feed PATH

The feed is JSON with a `models` object; each model entry merges into the destination file.
Sets `updated_at` (ISO local time) and writes atomically. Stdlib only.
"""
# Complexity notation:
#   m = models in the feed, f = fields per model, s = bytes of the destination file
import argparse  # O(1)
import json  # O(1)
import os  # O(1)
import sys  # O(1)
import time  # O(1)
from pathlib import Path  # O(1)

DEFAULT = Path(__file__).resolve().parent.parent / "config" / "models.json"  # O(1)


def _die(msg) -> None:  # O(1)
    print(f"error: {msg}", file=sys.stderr)  # O(1)
    sys.exit(2)  # O(1)


def _load_json(path: str) -> dict:  # O(s)
    p = Path(path)  # O(1)
    if not p.is_file():  # O(1)
        raise FileNotFoundError(path)  # O(1)
    data = json.loads(p.read_text(encoding="utf-8"))  # O(s)
    if not isinstance(data, dict):  # O(1)
        raise ValueError(f"{path}: expected JSON object")  # O(1)
    return data  # O(1)


def _merge_models(base: dict, feed: dict) -> dict:  # O(m*f)
    out = dict(base)  # O(1)
    models = dict(out.get("models") or {})  # O(models)
    for name, patch in (feed.get("models") or {}).items():  # O(m)
        if not isinstance(patch, dict):  # O(1)
            continue  # O(1)
        cur = dict(models.get(name) or {})  # O(f)
        cur.update(patch)  # O(f)
        models[name] = cur  # O(1)
    out["models"] = models  # O(1)
    return out  # O(1)


def atomic_write(path: str, text: str) -> None:  # O(len text)
    p = Path(path)  # O(1)
    p.parent.mkdir(parents=True, exist_ok=True)  # O(1)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")  # O(1)
    tmp.write_text(text, encoding="utf-8")  # O(len text)
    tmp.replace(p)  # O(1) atomic rename


def refresh(dest: str, feed_path: str) -> dict:  # O(s + m*f)
    """Apply feed rates to dest models.json; return the written document."""
    feed = _load_json(feed_path)  # O(s)
    if Path(dest).is_file():  # O(1)
        data = _load_json(dest)  # O(s)
    else:  # O(1)
        data = _load_json(str(DEFAULT)) if DEFAULT.is_file() else {"currency": "USD", "per_tokens": 1000000, "models": {}}  # O(s)
    data = _merge_models(data, feed)  # O(m*f)
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")  # O(1)
    blob = json.dumps(data, indent=2) + "\n"  # O(s)
    atomic_write(dest, blob)  # O(s)
    return data  # O(1)


def cmd_refresh(a) -> int:  # O(s)
    try:  # O(1)
        refresh(a.dest, a.feed)  # O(s)
    except FileNotFoundError as e:  # O(1)
        _die(str(e))  # O(1)
    except (OSError, ValueError, json.JSONDecodeError) as e:  # O(1)
        _die(str(e))  # O(1)
    return 0  # O(1)


def main(argv=None):  # O(1)
    p = argparse.ArgumentParser(prog="models_refresh.py")  # O(1)
    sub = p.add_subparsers(dest="cmd", required=True)  # O(1)
    s = sub.add_parser("refresh")  # O(1)
    s.add_argument("--dest", required=True)  # O(1)
    s.add_argument("--feed", required=True)  # O(1)
    s.set_defaults(fn=cmd_refresh)  # O(1)
    a = p.parse_args(argv)  # O(len(argv))
    return a.fn(a)  # O(1)


if __name__ == "__main__":  # O(1)
    sys.exit(main())  # O(1)
