"""factory_watch: render and follow a run's events for any factory provider."""
from __future__ import annotations

import sys
import threading
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import cloud_client  # noqa: E402
import cloud_watch  # noqa: E402
import factory_api  # noqa: E402
import factory_registry  # noqa: E402

TERMINAL = ("done", "failed", "cancelled")
PLUGIN_ERRORS = factory_registry.PLUGIN_ERRORS


def render(event: dict) -> str | None:
    if not isinstance(event, dict):
        return None
    d = event.get("data")
    d = d if isinstance(d, dict) else {}
    kind = event.get("kind")
    clean = cloud_watch.clean
    if kind == "status":
        to = clean(d.get("to"))
        lines = [f"status: {to}"]
        if to in TERMINAL:
            if d.get("url"):
                lines.append(f"session: {clean(d['url'])}")
            if d.get("result"):
                lines.append(f"result: {clean(d['result'])}")
        return "\n".join(lines)
    if kind == "text":
        t = clean(d.get("text")).strip()
        return t or None
    if kind == "tool":
        return f"tool: {clean(d.get('tool'))} {clean(d.get('target'))}".rstrip()
    return cloud_watch.render(event)


def _read_input(provider, ref: str) -> None:
    try:
        for line in sys.stdin:
            try:
                cloud_watch.handle_input(provider, ref, line)
            except PLUGIN_ERRORS as e:
                print(_scrub(provider, e), file=sys.stderr, flush=True)
    except (OSError, ValueError):
        pass


def _scrub(provider, msg) -> str:
    s = str(msg)
    tok = getattr(provider, "_token", None)
    if tok:
        s = s.replace(tok, "***")
    return cloud_watch.clean(s)


def run(provider, ref: str, after: int = 0, follow: bool = False) -> int:
    if not follow and factory_api.agent_without_tty():
        name = getattr(provider, "name", None)
        address = f"{name}:{ref}" if name else ref
        print(factory_api.watch_handoff(factory_api.watch_command("factory", [address]), address))
        return 0
    threading.Thread(target=_read_input, args=(provider, ref), daemon=True).start()
    try:
        for ev in provider.events(ref, after):
            line = render(ev)
            if line:
                print(line, flush=True)
            d = ev.get("data") if isinstance(ev, dict) else None
            if isinstance(ev, dict) and ev.get("kind") == "status" and isinstance(d, dict) \
                    and d.get("to") in TERMINAL:
                return 0
    except cloud_client.CloudError as e:
        print(_scrub(provider, e), file=sys.stderr, flush=True)
        return 1
    except KeyboardInterrupt:
        return 0
    except PLUGIN_ERRORS as e:  # a plugin's own error: scrubbed, no traceback
        print(_scrub(provider, f"{type(e).__name__}: {e}"), file=sys.stderr, flush=True)
        return 1
    return 0
