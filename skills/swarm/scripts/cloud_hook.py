"""Prompt hook for live cloud runs: routes `>>` nudges, attach lines and cloud slash commands.

Fails open: any exception in main prints nothing and exits 0. Never prints the token.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import state  # noqa: E402
from cloud_client import CloudError  # noqa: E402

ENDED = {"done", "failed", "cancelled", "canceled", "ended", "error", "complete", "completed"}
_SLASH = re.compile(r"^/superlite[:-](attach|detach|runs|watch|give)(?:[\s-]+(.*))?$", re.S)
_ENTRY_LABEL = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
_CURSOR_ENV = ("CURSOR_AGENT", "CURSOR_TRACE_ID", "CURSOR_PLUGIN_ROOT")


def _home() -> Path:
    h = os.environ.get("SUPERLITE_HOME")
    return Path(h) if h else Path.home() / state.DIR_NAME


def _api_ref(arg: str) -> str:
    """A generated entry names another owner's run `chris.login_fix`; the API wants `chris/login_fix`."""
    if _ENTRY_LABEL.match(arg):
        import cloud_entries
        return cloud_entries.ref_of(arg)
    return arg


def _client():
    import cloud_client
    import cloud_config
    cfg = cloud_config.load()
    if not cfg or not cfg.get("url") or not cfg.get("token"):
        return None
    return cloud_client.Client(cfg["url"], cfg["token"])


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
    there is no id or the lookup failed; the stored name is kept then. `lost` is True when the server says
    the run is gone or no longer visible (404/403, e.g. after giving it away), not on a network failure.
    A qualified name (`chris/login_fix`) keeps its owner when the bare name is refreshed.
    """
    st = _attach_state()
    if not st:
        return None
    out = {"run": st["run"], "id": st.get("id"), "status": None}
    if not out["id"]:
        return out
    try:
        t = client.task(out["id"])
    except CloudError as e:
        out["lost"] = getattr(e, "status", None) in (403, 404)
        return out
    except AttributeError:
        return out
    if isinstance(t, dict):
        out["status"] = t.get("status")
        owner, sep, bare = out["run"].rpartition("/")
        name = t.get("name")
        if name and name != bare:
            out["run"] = f"{owner}{sep}{name}"
            try:
                (_home() / "attach.json").write_text(json.dumps({"run": out["run"], "id": out["id"]}), encoding="utf-8")
            except OSError:
                pass
    return out


def detach_if_given(ref: str, given: dict | None, factory: str | None = None) -> None:
    """Clear the attachment when it is the run just given away (by id, else by name), on the same factory.

    `factory` is the one the give went to (None: superlite-cloud); an attachment on another factory is never cleared,
    even when both services return the same run id or name."""
    st = _attach_state()
    if not st or (st.get("factory") or "superlite-cloud") != (factory or "superlite-cloud"):
        return
    gid = given.get("id") if isinstance(given, dict) else None
    if st.get("id") and gid:
        same = st["id"] == gid
    else:
        same = st["run"].split("/")[-1] == ref.split("/")[-1]
    if same:
        clear_attach()


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
    owner, _, name = ref.rpartition("/")
    runs = None
    if owner:  # another owner's run is only in the all-runs list (an admin's); a member's own run is in the default one
        try:
            runs = client.runs(all=True)
        except CloudError:
            runs = None
    if runs is None:
        runs = client.runs()
    for r in runs:
        if r.get("name") == name and (not owner or r.get("owner") == owner):
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


def _slash(client, verb: str, arg: str, factory: str | None = None) -> dict:
    arg = (arg or "").strip()
    if verb == "detach":
        clear_attach()
        return _block("detached")
    if verb == "runs":
        import cloud
        try:  # read the unread notices first: the list call marks them seen
            notices = client.status().get("notices", [])
        except CloudError:
            notices = []
        runs = client.runs(all=arg.lstrip("-") == "all")
        lines = []
        for r in runs:
            line = f"{r.get('owner', '')}/{r['name']}  {r.get('status', '')}  {r.get('agent', '')}"
            mark = cloud._mark(r, notices)
            lines.append(f"{line}  {mark}" if mark else line)
        return _block("\n".join(lines) or "no runs")
    if verb == "give":
        ref, _, email = arg.partition(" ")
        if not ref or not email.strip():
            return _block("usage: /superlite:give <run> <email>")
        out = client.give(ref, email.strip())
        detach_if_given(ref, out, factory)
        return _block(give_line(out, ref, email.strip()))
    if not arg:
        return _block(f"usage: /superlite:{verb} <run>")
    arg = _api_ref(arg)
    if verb == "watch":
        cli = Path(_HERE) / "superlite-cli"
        cmd = f"bash {shlex.quote(str(cli))} cloud watch {shlex.quote(arg)}"
        from factory_api import watch_handoff
        return {"context": watch_handoff(cmd, arg)}
    info = client.attach(arg)
    name = info.get("run") or arg.split("/")[-1]
    if "/" in arg:  # keep the owner: a bare name would address the caller's own same-named run
        name = f"{arg.rpartition('/')[0]}/{name.split('/')[-1]}"
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


def _route(client, prompt: str, current: str | None, factory: str | None = None) -> dict | None:
    text = prompt.strip()
    attached = _attached()
    if text.startswith("\\") and attached:
        return {"pass": text[1:]}
    m = _SLASH.match(text)
    if m:
        verb, arg = m.group(1), m.group(2) or ""
        return _slash(client, verb, arg, factory)
    live = None  # the attached run, name refreshed; its id is the routing ref when known
    if attached and (text.startswith(">>") or text and (not text.startswith("/") or text in ("/cancel", "/approve"))):
        live = refresh_attached(client)
        attached = live["run"] if live else attached
        if live and live.get("lost"):
            clear_attach()
            why = f"{attached} is no longer available to you; detached"
            return _block(why) if text.startswith(">>") else {"context": why}
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


def _is_cursor(data: dict) -> bool:
    return data.get("hook_event_name") == "beforeSubmitPrompt" or any(os.environ.get(k) for k in _CURSOR_ENV)


def render(res: dict, cursor: bool) -> dict | None:
    """The hook's JSON for the host: Claude's decision/hookSpecificOutput, or Cursor's continue/additional_context."""
    if res.get("block"):
        if cursor:
            return {"continue": False, "user_message": res["reason"]}
        return {"decision": "block", "reason": res["reason"]}
    if "context" in res:
        text = res["context"]
    elif "pass" in res:
        text = "Leading backslash: this message is for you, not the cloud run: " + res["pass"]
    else:
        return None
    if cursor:
        return {"continue": True, "additional_context": text}
    return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": text}}


def main(argv: list[str] | None = None) -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
        res = route(str(data.get("prompt", "")))
        out = render(res, _is_cursor(data)) if res else None
        if out:
            print(json.dumps(out))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
