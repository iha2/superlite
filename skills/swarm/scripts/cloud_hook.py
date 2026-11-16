"""Prompt hook for live cloud runs: routes `>>` nudges, attach lines and cloud slash commands.

Fails open: any exception in main prints nothing and exits 0. Never prints the token.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import state  # noqa: E402
from cloud_client import CloudError  # noqa: E402

ENDED = {"done", "failed", "cancelled", "canceled", "ended", "error", "complete", "completed"}
_SLASH = re.compile(r"^/superlite[:-](attach|detach|runs|watch|give)(?:[\s-]+(.*))?$", re.S)


def _home() -> Path:
    h = os.environ.get("SUPERLITE_HOME")
    return Path(h) if h else Path.home() / state.DIR_NAME


def _client():
    import cloud_client
    import cloud_config
    cfg = cloud_config.load()
    if not cfg or not cfg.get("url") or not cfg.get("token"):
        return None
    return cloud_client.Client(cfg["url"], cfg["token"])


def _sh(cmd: list[str]):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=10)


def _attach_state() -> dict:
    try:
        d = json.loads((_home() / "attach.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) and d.get("run") else {}


def _attached() -> str | None:
    return _attach_state().get("run") or None


def clear_attach() -> None:
    try:
        (_home() / "attach.json").unlink()
    except OSError:
        pass


def refresh_attached(client) -> dict | None:
    """The attached run as {run, id, status}, its name refreshed from the server (a hand-back renames it).

    Looks the run up by id with GET /api/tasks/{id}, which never marks notices seen. status is None when
    there is no id or the lookup failed; the stored name is kept then.
    """
    st = _attach_state()
    if not st:
        return None
    out = {"run": st["run"], "id": st.get("id"), "status": None}
    if not out["id"]:
        return out
    try:
        t = client.task(out["id"])
    except (CloudError, AttributeError):
        return out
    if isinstance(t, dict):
        out["status"] = t.get("status")
        name = t.get("name")
        if name and name != out["run"]:
            out["run"] = name
            try:
                (_home() / "attach.json").write_text(json.dumps({"run": name, "id": out["id"]}), encoding="utf-8")
            except OSError:
                pass
    return out


def give_line(out: dict, ref: str, email: str) -> str:
    """"<name> given to <handle> (<email>)"; just the email when the server sent no to_handle."""
    out = out if isinstance(out, dict) else {}
    name = out.get("name") or ref
    to = out.get("to_email") or email
    handle = out.get("to_handle")
    return f"{name} given to {handle} ({to})" if handle else f"{name} given to {to}"


def _block(reason: str) -> dict:
    return {"block": True, "reason": reason}


def _newest_running(client) -> str | None:
    me = client.me().get("handle")
    mine = [r for r in client.runs() if r.get("status") == "running" and r.get("owner") == me]
    if not mine:
        return None
    return max(enumerate(mine), key=lambda p: (p[1].get("started") or p[1].get("created") or 0, p[0]))[1]["name"]


def _send(client, verb: str, ref: str, *args, label: str | None = None) -> dict:
    try:
        getattr(client, verb)(ref, *args)
    except CloudError as e:
        msg = str(e)
        tok = getattr(client, "_token", "")
        return _block(f"{verb} not sent: {msg.replace(tok, '***') if tok else msg}")
    return _block(f"→ {label or ref}")


def _status_of(client, ref: str) -> str | None:
    name = ref.split("/")[-1]
    for r in client.runs():
        if r.get("name") == name:
            return r.get("status")
    return None


def _known(client, tok: str) -> bool:
    """A bare name is a run the user can see; a failed lookup counts as known (the send reports the error)."""
    if "/" in tok:
        return True
    try:
        return tok in {r.get("name") for r in client.runs(all=True)}
    except CloudError:
        return True


def _split_ref(client, rest: str) -> tuple[str | None, str]:
    """(explicit run, message) from the text after `>>`; the run is None when the first word is part of the message."""
    if rest[:1].isspace():
        return None, rest
    tok, _, text = rest.partition(" ")
    if text.strip() and _known(client, tok):
        return tok, text
    return None, rest


def _nudge_prefix(client, rest: str, current: str | None, label: str | None = None) -> dict:
    ref, rest = _split_ref(client, rest)
    text = rest.strip()
    if ref is None and current:
        ref = current
    else:
        label = None
        ref = ref or _newest_running(client)
    if not ref:
        return _block("no run to nudge: attach one with /superlite:attach <run>")
    if not text:
        return _block("nothing to send: write the message after >>")
    return _send(client, "nudge", ref, text, label=label)


def _slash(client, verb: str, arg: str) -> dict:
    arg = (arg or "").strip()
    if verb == "detach":
        clear_attach()
        return _block("detached")
    if verb == "runs":
        runs = client.runs()
        lines = [f"{r.get('owner', '')}/{r['name']}  {r.get('status', '')}  {r.get('agent', '')}" for r in runs]
        return _block("\n".join(lines) or "no runs")
    if verb == "give":
        ref, _, email = arg.partition(" ")
        if not ref or not email.strip():
            return _block("usage: /superlite:give <run> <email>")
        out = client.give(ref, email.strip())
        return _block(give_line(out, ref, email.strip()))
    if not arg:
        return _block(f"usage: /superlite:{verb} <run>")
    if verb == "watch":
        cli = Path(_HERE) / "superlite-cli"
        cmd = f'bash "{cli}" cloud watch {arg}'
        if os.environ.get("HERDR_ENV") == "1":
            pane = _sh(["herdr", "pane", "split", "--current", "--direction", "right", "--no-focus"])
            pid = (pane.stdout or "").strip()
            if pid:
                _sh(["herdr", "pane", "run", pid, cmd])
                return _block(f"watching {arg} in a pane on the right")
        return _block(f"run this in another terminal: {cmd}")
    info = client.attach(arg)
    name = info.get("run") or arg.split("/")[-1]
    if info.get("status") in ENDED:
        return _block(f"{name} has ended")
    home = _home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "attach.json").write_text(json.dumps({"run": name, "id": info.get("id")}), encoding="utf-8")
    return _block(f"attached {name}")


def route_main(prompt: str, current: str | None = None) -> dict | None:
    """The hook's entry point. A cloud line whose call fails is blocked with the error; it never reaches Claude."""
    client = _client()
    if client is None:
        return None
    try:
        return _route(client, prompt, current)
    except Exception as e:
        msg = str(e)
        tok = getattr(client, "_token", "")
        if tok:
            msg = msg.replace(tok, "***")
        return _block(f"not sent: {msg}")


route = route_main


def _route(client, prompt: str, current: str | None) -> dict | None:
    text = prompt.strip()
    attached = _attached()
    if text.startswith("\\") and attached:
        return {"pass": text[1:]}
    m = _SLASH.match(text)
    if m:
        verb, arg = m.group(1), m.group(2) or ""
        return _slash(client, verb, arg)
    live = None  # the attached run, name refreshed; its id is the routing ref when known
    if attached and (text.startswith(">>") or text and (not text.startswith("/") or text in ("/cancel", "/approve"))):
        live = refresh_attached(client)
        attached = live["run"] if live else attached
    ref = (live or {}).get("id") or attached
    cur = current or ref
    if text.startswith(">>"):
        return _nudge_prefix(client, text[2:], cur, attached if cur == ref else None)
    if not attached or not text or text.startswith("/") and text not in ("/cancel", "/approve"):
        return None
    if text == "/cancel":
        try:
            client.cancel(ref)
        except CloudError as e:
            return _block(f"cancel not sent: {e}")
        return _block(f"cancelled {attached}")
    if text == "/approve":
        return _send(client, "respond", ref, "approve", label=attached)
    status = live.get("status") if live else None
    if status is None:
        status = _status_of(client, attached)
    if status in ENDED:
        clear_attach()
        return {"context": f"{attached} has ended; detached"}
    return _send(client, "nudge" if status == "running" else "respond", ref, text, label=attached)


def main(argv: list[str] | None = None) -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
        res = route(str(data.get("prompt", "")))
        if not res:
            return 0
        if res.get("block"):
            print(json.dumps({"decision": "block", "reason": res["reason"]}))
        elif "context" in res:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit", "additionalContext": res["context"]}}))
        elif "pass" in res:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": "Leading backslash: this message is for you, not the cloud run: " + res["pass"]}}))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
