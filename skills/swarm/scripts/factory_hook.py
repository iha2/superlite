"""Prompt hook across factories: `<factory>:<ref>` addresses, attach.json `factory` key, labelled replies.

Wraps cloud_hook._route(provider, prompt, current); with only superlite-cloud in play it is
cloud_hook.route_main. Lines meant for a run (attached plain line, `>>`, addressed verbs) are blocked when the
hook raises or times out; any other line fails open (prints nothing, exit 0). Tokens are scrubbed.
"""
from __future__ import annotations

import json
import re
import os
import shlex
import sys
import threading
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import cloud_hook  # noqa: E402
from cloud_client import CloudError  # noqa: E402
import factory_registry  # noqa: E402

CLOUD = "superlite-cloud"
PLUGIN_ERRORS = factory_registry.PLUGIN_ERRORS
_ADDR_VERBS = ("attach", "watch", "give")


def _reg():
    import factory_registry
    return factory_registry


def _block(reason: str) -> dict:
    return {"block": True, "reason": reason}


def attached_factory() -> str | None:
    st = cloud_hook._attach_state()
    if not st:
        return None
    return st.get("factory") or CLOUD


def _set_factory(fac: str | None) -> None:
    """Write the factory key into attach.json (absent = superlite-cloud); leave other keys alone."""
    p = cloud_hook._home() / "attach.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(d, dict) or not d.get("run"):
        return
    if fac and fac != CLOUD:
        d["factory"] = fac
    else:
        d.pop("factory", None)
    try:
        p.write_text(json.dumps(d), encoding="utf-8")
    except OSError:
        pass


def _keep_factory(af: str | None) -> None:
    """refresh_attached rewrites attach.json without the factory key; a failed send must not leave it unkeyed
    (the next line would go to superlite-cloud)."""
    st = cloud_hook._attach_state()
    if af and af != CLOUD and st and not st.get("factory"):
        _set_factory(af)


def _names(reg) -> set[str]:
    names = {CLOUD, *reg.logged_in()}
    try:
        names |= set(reg.load_store().get("factories", {}))
    except PLUGIN_ERRORS:
        pass
    return names


def _cloud_entry(verb: str | None, tok: str) -> bool:
    """True when the generated entry folder for `/superlite-<verb>-<tok>` is a cloud entry (cloud marker only)."""
    if not verb:
        return False
    import cloud_entries
    import factory_status
    d = factory_registry._claude_home() / "skills" / f"superlite-{verb}-{tok}"
    return (d / cloud_entries.MARKER).exists() and not (d / factory_status.MARKER).exists()


def _addr(reg, tok: str, af: str | None, dashed: bool, verb: str | None = None) -> tuple[str, str, bool]:
    """(factory, ref, explicit) for a token; explicit when it named a known factory.

    A dashed entry always names its factory (plugin entries) or is a cloud entry (never prefixed): a cloud
    run `lab-fix` must not reach a factory `lab`, and a bare cloud entry must not follow the attached factory.
    """
    names = _names(reg)
    head, sep, rest = tok.partition(":")
    if sep and rest and head in names:
        return head, rest, True
    if dashed:
        if _cloud_entry(verb, tok):
            return CLOUD, tok, True
        for n in sorted(names, key=len, reverse=True):
            if tok.startswith(n + "-") and len(tok) > len(n) + 1:
                return n, tok[len(n) + 1:], True
        return CLOUD, tok, True
    fac, ref = reg.resolve(tok, af)
    return fac, ref, False


def _scrub(text: str, *providers) -> str:
    for p in providers:
        tok = getattr(p, "_token", "")
        if tok:
            text = text.replace(tok, "***")
    return text


def _label(res: dict, fac: str, kind: str, provider, multi: bool) -> dict:
    """Rewrite a reason to `→ <factory> (<kind>) <ref>: <what>` when two or more factories are in play."""
    if not multi or not res or not res.get("block"):
        return res
    why = res.get("reason", "")
    if why.startswith("→ ") and ": " not in why:
        word = getattr(provider, "nudge_word", "nudge")
        return _block(f"→ {fac} ({kind}) {why[2:]}: {word} sent")
    if why.startswith("attached "):
        return _block(f"→ {fac} ({kind}) {why[9:]}: attached")
    return res


def _kind(reg, fac: str) -> str:
    try:
        return reg.kind_of(fac)
    except PLUGIN_ERRORS:
        return fac


def _watch(reg, fac: str, ref: str) -> dict:
    cli = Path(cloud_hook._HERE) / "superlite-cli"
    cmd = f"bash {shlex.quote(str(cli))} factory watch {shlex.quote(fac + ':' + ref)}"
    from factory_api import watch_handoff
    return {"context": watch_handoff(cmd, f"{fac}:{ref}")}


def _unverified(prov, ref: str) -> str | None:
    """A reason when an attach the server accepted names no run it knows (no id came back and the lookup fails)."""
    st = cloud_hook._attach_state()
    if not st or st.get("id"):
        return None
    try:
        prov.task(st.get("run") or ref)
    except CloudError as e:
        if getattr(e, "status", None) in (403, 404):
            return f"not attached: {ref}: not found"
    except PLUGIN_ERRORS:
        pass
    return None


def _local_escape(text: str, af: str | None) -> tuple[bool, dict | None]:
    """(handled, reply) for the lines that never need a provider: a leading backslash and `/superlite:detach`."""
    if text.startswith("\\"):
        return True, ({"pass": text[1:]} if af else None)
    m = cloud_hook._SLASH.match(text)
    if m and m.group(1) == "detach":
        cloud_hook.clear_attach()
        return True, _block("detached")
    return False, None


def _explicit_cloud(text: str) -> bool:
    """True for `/superlite:<attach|watch|give> superlite-cloud:<ref>` and `>> superlite-cloud:<ref> ...`."""
    prefix = CLOUD + ":"
    m = cloud_hook._SLASH.match(text)
    if m:
        return m.group(1) in _ADDR_VERBS and (m.group(2) or "").strip().startswith(prefix)
    return text.startswith(">>") and text[2:].lstrip().startswith(prefix)


def _route_multi(reg, prompt: str, af: str | None, multi: bool) -> dict | None:
    text = prompt.strip()
    done, reply = _local_escape(text, af)
    if done:
        return reply
    dashed = text.startswith("/superlite-")
    target = None  # (factory, rewritten prompt) for _route
    direct = None  # (factory, ref, message) for an explicit `>> fac:ref text`
    verb = None
    m = cloud_hook._SLASH.match(text)
    if m:
        verb, arg = m.group(1), (m.group(2) or "").strip()
        if verb in _ADDR_VERBS and arg:
            tok, _, tail = arg.partition(" ")
            fac, ref, _x = _addr(reg, tok, af, dashed, verb)
            target = (fac, f"/superlite:{verb} {ref}" + (f" {tail}" if tail.strip() else ""))
        elif verb == "runs":
            tok, _, tail = arg.partition(" ")
            if tok in _names(reg):
                target = (tok, f"/superlite:runs {tail}".rstrip())
            else:
                target = (reg.active(), text)
        else:
            target = (af or reg.active(), text)
    elif text.startswith(">>"):
        rest = text[2:].lstrip()  # `>> fac:ref msg` and `>>fac:ref msg` both address a run
        tok, _, tail = rest.partition(" ")
        if rest and ":" in tok:
            fac, ref, explicit = _addr(reg, tok, af, False)
            if explicit:
                direct = (fac, ref, tail.strip())
        if direct is None:
            target = (af or reg.active(), text)
    elif af and text and (not text.startswith("/") or text in ("/cancel", "/approve")):
        target = (af or reg.active(), text)
    else:
        return None
    fac = (direct or target)[0]
    try:
        if fac != CLOUD and fac not in reg.logged_in():
            raise reg.FactoryError(f"{fac}: not logged in")
        prov = reg.provider(fac)
    except PLUGIN_ERRORS as e:  # a broken or logged-out factory blocks addressed input; it never reaches the model
        if direct or verb or af == fac or text.startswith(">>"):  # the type only: a plugin's own message may carry its token
            return _block(f"{fac}: unavailable ({type(e).__name__})")
        return None
    kind = _kind(reg, fac)
    try:
        if direct:
            _f, ref, msg = direct
            if not msg:
                return _block("nothing to send: write the message after >>")
            res = cloud_hook._send(prov, "nudge", ref, msg, label=ref)
            return _label(res, fac, kind, prov, multi)
        if verb == "give":
            from factory_api import require
            require(prov, "give")
        if verb == "watch" and fac != CLOUD:
            ref = target[1].partition(" ")[2].strip()
            return _watch(reg, fac, ref) if ref else _block("usage: /superlite:watch <run>")
        res = cloud_hook._route(prov, target[1], None, fac)
    except CloudError as e:
        return _block(_scrub(str(e), prov))
    except PLUGIN_ERRORS as e:
        if not isinstance(e, reg.FactoryError):  # a plugin's own exception: the type only, its message may carry a token
            return _block(f"not sent: {fac}: {type(e).__name__}")
        return _block("not sent: " + _scrub(str(e), prov))
    finally:
        _keep_factory(af)
    if res:
        if verb == "attach" and str(res.get("reason", "")).startswith("attached "):
            bad = _unverified(prov, target[1].split(" ", 1)[1].strip())
            if bad:
                cloud_hook.clear_attach()
                return _block(_scrub(bad, prov))
            _set_factory(fac)
        elif af:
            st = cloud_hook._attach_state()
            if st and not st.get("factory") and af != CLOUD:
                _set_factory(af)  # refresh_attached rewrote attach.json without the key
        res = _label(res, fac, kind, prov, multi)
        for k in ("reason", "context"):
            if isinstance(res.get(k), str):
                res[k] = _scrub(res[k], prov)
    return res


def _verify_attach(reg, prompt: str, res: dict | None) -> dict | None:
    """Cloud-only path: an accepted attach that names no known run is blocked and leaves no attach.json."""
    try:
        m = cloud_hook._SLASH.match(prompt.strip())
        if not (res and m and m.group(1) == "attach" and str(res.get("reason", "")).startswith("attached ")):
            return res
        prov = reg.provider(CLOUD)
        bad = _unverified(prov, cloud_hook._api_ref((m.group(2) or "").strip()))
        if bad:
            cloud_hook.clear_attach()
            return _block(_scrub(bad, prov))
    except PLUGIN_ERRORS:
        pass
    return res


def _names_plugin(reg, text: str) -> bool:
    """True when the input's first address token could name a registered plugin (a false positive only routes wider)."""
    plugins = _names(reg) - {CLOUD}
    m = cloud_hook._SLASH.match(text)
    if m:
        if m.group(1) not in _ADDR_VERBS + ("runs",):
            return False
        tok = (m.group(2) or "").strip().partition(" ")[0]
    elif text.startswith(">>"):
        tok = text[2:].lstrip().partition(" ")[0]
    else:
        return False
    return tok.partition(":")[0] in plugins or any(tok.startswith(n + "-") for n in plugins)


def _registry_failed(prompt: str, err: BaseException) -> dict | None:
    """The registry raised. A plugin attachment (read from attach.json, no registry) must not guess a provider: a
    run-meant line is blocked, while local detach and `\\...` lines still work. Else the cloud fallback."""
    af = attached_factory()
    if af and af != CLOUD and run_meant(prompt):
        return _block(f"not sent: {af}: unavailable ({type(err).__name__})")
    return cloud_hook.route_main(prompt)


def route(prompt: str) -> dict | None:
    try:
        reg = _reg()
        logged = reg.logged_in()
        af = attached_factory()
        cloud_only = (set(logged) <= {CLOUD} and af in (None, CLOUD) and reg.active() == CLOUD
                      and not _names_plugin(reg, prompt.strip()))
    except PLUGIN_ERRORS as e:
        return _registry_failed(prompt, e)
    if cloud_only:
        if CLOUD in logged and _explicit_cloud(prompt.strip()):  # the address names the run, not the literal text
            return _route_multi(reg, prompt, af, False)
        res = cloud_hook.route_main(prompt)
        if res is None and af and CLOUD not in logged and run_meant(prompt):  # no client: it never reaches the run
            return _block("not sent: not logged in: run superlite-cli cloud login")
        return _verify_attach(reg, prompt, res)
    return _route_multi(reg, prompt, af, len(logged) >= 2)


def run_meant(prompt: str) -> bool:
    """True for a line clearly meant for a run: an attached plain line, `>>`, or an addressed verb."""
    text = prompt.strip()
    if not text or text.startswith("\\"):
        return False
    if text.startswith(">>"):
        return True
    m = cloud_hook._SLASH.match(text)
    if m:
        return m.group(1) in _ADDR_VERBS
    return bool(cloud_hook._attach_state()) and (not text.startswith("/") or text in ("/cancel", "/approve"))


def _timeout() -> float:
    try:
        return max(0.1, float(os.environ.get("SUPERLITE_HOOK_TIMEOUT", "30")))
    except ValueError:
        return 30.0


def _guarded_main(prompt: str) -> dict | None:
    """route(prompt); an exception or a hang on a run-meant line is a block (type name only), else fails open."""
    box: dict = {}

    def thread_main():
        try:
            box["res"] = route(prompt)
        except BaseException as e:  # noqa: BLE001 - SystemExit from a plugin included
            box["err"] = type(e).__name__

    t = threading.Thread(target=thread_main, daemon=True)
    t.start()
    t.join(_timeout())
    why = "timed out" if t.is_alive() else box.get("err")
    if not why:
        return box.get("res")
    try:
        meant = run_meant(prompt)
    except Exception:
        meant = True
    if not meant:
        return None
    if t.is_alive():
        return _block("not sent: hook timed out; delivery not confirmed, check the run before resending")
    return _block(f"not sent: hook failed ({why}); the line did not reach the run")


def main(argv: list[str] | None = None) -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
        res = _guarded_main(str(data.get("prompt", "")))
        out = cloud_hook.render(res, cloud_hook._is_cursor(data)) if res else None
        if out:
            print(json.dumps(out))
    except Exception:
        return 0
    if threading.active_count() > 1:  # a hung plugin thread must not hold the process open
        sys.stdout.flush()
        os._exit(0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
