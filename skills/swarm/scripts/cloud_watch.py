"""T13: watch pane. Prints a cloud run's live events; input lines nudge, approve or cancel."""
from __future__ import annotations

import argparse
import re
import sys
import threading
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import cloud_client  # noqa: E402
import cloud_config  # noqa: E402
import cloud_stream  # noqa: E402

TERMINAL = {"done", "failed", "cancelled"}
_WS = re.compile(r"[\r\n\t]+")
_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def clean(v) -> str:
    return _CTRL.sub("", _WS.sub(" ", str(v if v is not None else "")))


def _status_line(d: dict) -> str:
    line = f"{clean(d.get('run', ''))} {clean(d.get('to', ''))}".strip()
    if d.get("result"):
        line += f": {clean(d['result'])}"
    return line


def render(event: dict) -> str | None:
    if not isinstance(event, dict):
        return None
    d = event.get("data")
    d = d if isinstance(d, dict) else {}
    kind = event.get("kind")
    if kind == "nudge":
        return f"nudge from {clean(d.get('by'))}: {clean(d.get('text'))}"
    if kind == "rename":
        return f"renamed {clean(d.get('from'))} to {clean(d.get('to'))}: {clean(d.get('why'))}"
    if kind == "given":
        line = f"{clean(d.get('name'))} given to {clean(d.get('to'))}"
        if d.get("email"):
            line += f" ({clean(d['email'])})"
        return line
    if kind == "status":
        return _status_line(d)
    if kind == "result":
        txt = d.get("result") or d.get("text")
        return f"result: {clean(txt)}" if txt else None
    return None


def handle_input(client, ref: str, line: str) -> None:
    text = line.strip()
    if not text:
        return
    if text == "/approve":
        client.respond(ref, "approve")
    elif text == "/cancel":
        client.cancel(ref)
    else:
        client.nudge(ref, text)


def _read_input(client, ref: str) -> None:
    for line in sys.stdin:
        try:
            handle_input(client, ref, line)
        except cloud_client.CloudError as e:
            print(clean(e), file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="cloud watch")
    p.add_argument("ref")
    p.add_argument("--after", type=int, default=0)
    a = p.parse_args(argv)
    cfg = cloud_config.load()
    if not cfg or not cfg.get("url") or not cfg.get("token"):
        print("not logged in: run superlite-cli cloud login", file=sys.stderr)
        return 1
    client = cloud_client.Client(cfg["url"], cfg["token"])
    threading.Thread(target=_read_input, args=(client, a.ref), daemon=True).start()
    try:
        for ev in cloud_stream.events(cfg["url"], cfg["token"], a.ref, after=a.after):
            line = render(ev)
            if line:
                print(line, flush=True)
            d = ev.get("data") if isinstance(ev, dict) else None
            if isinstance(ev, dict) and ev.get("kind") == "status" and isinstance(d, dict) \
                    and d.get("to") in TERMINAL:
                return 0
    except cloud_client.CloudError as e:
        print(clean(str(e).replace(cfg["token"], "***")), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
