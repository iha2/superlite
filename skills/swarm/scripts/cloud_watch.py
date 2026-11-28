"""T13: watch pane. Prints a cloud run's live events; input lines nudge, approve or cancel."""
from __future__ import annotations

import argparse
import os
import re
import sys
import threading
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import cloud_client  # noqa: E402
import cloud_config  # noqa: E402
import factory_api  # noqa: E402
import cloud_stream  # noqa: E402

PLAN_MARKER = "Reply approve to start"
TERMINAL = {"done", "failed", "cancelled"}
_WS = re.compile(r"[\r\n\t]+")
_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def clean(v, keep_newlines: bool = False) -> str:
    """Strip control chars from server data. keep_newlines keeps \n (multi-line messages); everything else is stripped."""
    s = str(v if v is not None else "")
    if keep_newlines:
        s = re.sub(r"\r\n|\r", "\n", s).replace("\t", " ")
        return "\n".join(_CTRL.sub("", ln) for ln in s.split("\n"))
    return _CTRL.sub("", _WS.sub(" ", s))


def use_color(stream=None) -> bool:
    stream = stream if stream is not None else sys.stdout
    try:
        tty = bool(stream.isatty())
    except (AttributeError, ValueError):
        tty = False
    return tty and not os.environ.get("NO_COLOR")


_STYLES = {"dim": "2", "bold": "1", "green": "32", "red": "31", "yellow": "33", "dim_italic": "2;3"}


def _sty(text: str, style: str, color: bool) -> str:
    """Wrap already-cleaned text in ANSI, per line so multi-line output stays styled."""
    if not color or not text:
        return text
    code = _STYLES[style]
    return "\n".join(f"\x1b[{code}m{ln}\x1b[0m" if ln else ln for ln in text.split("\n"))


def _status_line(d: dict) -> str:
    line = f"{clean(d.get('run', ''))} {clean(d.get('to', ''))}".strip()
    if d.get("result"):
        line += f": {clean(d['result'])}"
    return line


def _block(text: str) -> list[str]:
    return text.split("\n")


def render(event: dict, color: bool | None = None) -> str | None:
    if not isinstance(event, dict):
        return None
    color = use_color() if color is None else color
    d = event.get("data")
    d = d if isinstance(d, dict) else {}
    kind = event.get("kind")
    if kind == "text":
        text = clean(d.get("text"), keep_newlines=True).strip("\n")
        if not text.strip():
            return None
        lines = _block(text)
        return "\n".join([f"\u23fa {lines[0]}"] + [f"  {ln}" for ln in lines[1:]])
    if kind == "thinking":
        text = clean(d.get("text"), keep_newlines=True).strip("\n")
        head = _sty("\u273b Thinking\u2026", "dim_italic", color)
        if not text.strip():
            return head
        return head + "\n" + _sty("\n".join(f"  {ln}" for ln in _block(text)), "dim_italic", color)
    if kind == "tool":
        tool = clean(d.get("tool"))
        if not tool:
            return None
        target = clean(d.get("target"))
        tail = f"({target})" if target else ""
        return "\u23fa " + _sty(tool, "bold", color) + tail
    if kind == "file":
        path = clean(d.get("path"))
        return _sty(f"  \u23bf  {path}", "dim", color) if path else None
    if kind == "nudge":
        text = clean(d.get("text"))
        if not text:
            return None
        by = clean(d.get("by")) if d.get("by") else ""
        return _sty(f"\u00bb nudge from {by}: {text}" if by else f"\u00bb nudge: {text}", "yellow", color)
    if kind == "rename":
        return f"renamed {clean(d.get('from'))} to {clean(d.get('to'))}: {clean(d.get('why'))}"
    if kind == "given":
        line = f"{clean(d.get('name'))} given to {clean(d.get('to'))}"
        email = d.get("to_email") or d.get("email")
        if email:
            line += f" ({clean(email)})"
        return line
    if kind == "status":
        line = _status_line(d)
        to = d.get("to")
        if to == "done":
            return _sty(f"\u2713 {line}", "green", color)
        if to in TERMINAL:
            return _sty(f"\u2717 {line}", "red", color)
        return _sty(f"\u00b7 {line}", "dim", color)
    if kind == "question":
        txt = clean(d.get("text"))
        return f"? {txt}" if txt else None
    if kind == "result":
        txt = d.get("result") or d.get("text")
        return _sty(f"\u2713 result: {clean(txt)}", "green", color) if txt else None
    return None


class WatchState:
    """What the run waits on: written by the event loop (observe), read by the input thread (get)."""

    def __init__(self) -> None:
        self.locked = threading.Lock()
        self.waiting_on: str | None = None

    def observe(self, event) -> None:
        if not isinstance(event, dict):
            return
        d = event.get("data")
        d = d if isinstance(d, dict) else {}
        kind = event.get("kind")
        with self.locked:
            if kind == "question":
                self.waiting_on = "question"
            elif kind == "result" and PLAN_MARKER in str(d.get("result") or d.get("text") or ""):
                self.waiting_on = "plan"
            elif kind == "status" and d.get("to") != "waiting":
                self.waiting_on = None

    def get(self) -> str | None:
        with self.locked:
            return self.waiting_on


def handle_input(client, ref: str, line: str, state: WatchState | None = None) -> None:
    text = line.strip()
    if not text:
        return
    if text == "/approve":
        client.respond(ref, "approve")
    elif text == "/cancel":
        client.cancel(ref)
    elif state is not None and state.get() == "question":
        client.respond(ref, text)
    else:
        client.nudge(ref, text)


def _read_input(client, ref: str, state: WatchState | None = None) -> None:
    for line in sys.stdin:
        try:
            handle_input(client, ref, line, state)
        except cloud_client.CloudError as e:
            print(clean(e), file=sys.stderr, flush=True)


def _is_terminal(ev) -> bool:
    d = ev.get("data") if isinstance(ev, dict) else None
    return isinstance(d, dict) and ev.get("kind") == "status" and d.get("to") in TERMINAL


def _finished(ev, terminal_seen: bool) -> bool:
    """True when nothing more is worth waiting for: a terminal status that embeds its result, or a result after one.

    A bare terminal status is not final: the stream (bounded by its grace period) may still deliver the result."""
    if _is_terminal(ev):
        return bool(ev["data"].get("result"))
    return terminal_seen and isinstance(ev, dict) and ev.get("kind") == "result"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="cloud watch")
    p.add_argument("ref")
    p.add_argument("--after", type=int, default=0)
    p.add_argument("--follow", action="store_true", help="stream even when an agent runs this without a terminal")
    a = p.parse_args(argv)
    if not a.follow and factory_api.agent_without_tty():
        args = [a.ref, *(["--after", str(a.after)] if a.after else [])]  # keep the cursor: the terminal must skip too
        print(factory_api.watch_handoff(factory_api.watch_command("cloud", args), a.ref))
        return 0
    cfg = cloud_config.load()
    if not cfg or not cfg.get("url") or not cfg.get("token"):
        print("not logged in: run superlite-cli cloud login", file=sys.stderr)
        return 1
    client = cloud_client.Client(cfg["url"], cfg["token"])
    state = WatchState()
    threading.Thread(target=_read_input, args=(client, a.ref, state), daemon=True).start()
    terminal = False
    try:
        for ev in cloud_stream.events(cfg["url"], cfg["token"], a.ref, after=a.after):
            state.observe(ev)
            line = render(ev)
            if line:
                print(line, flush=True)
            if _finished(ev, terminal):
                return 0
            terminal = terminal or _is_terminal(ev)
    except cloud_client.CloudError as e:
        print(clean(str(e).replace(cfg["token"], "***")), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
