"""factory: one CLI over the pluggable factories (superlite-cloud built in, plugin kinds such as warp).

With superlite-cloud the run verbs delegate to the real `cloud` / `cloud_watch` mains, so output is
unchanged. Tokens are never printed. Exit codes: 1 FactoryError, 2 unsupported / not enabled / usage.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_HERE_DIR = str(Path(__file__).resolve().parent)
if _HERE_DIR not in sys.path:
    sys.path.insert(0, _HERE_DIR)

CLOUD = "superlite-cloud"


def _home() -> Path:
    h = os.environ.get("SUPERLITE_HOME")
    if h:
        return Path(h)
    import state
    return Path.home() / state.DIR_NAME


def _reg():
    import factory_registry
    return factory_registry


def _api():
    import factory_api
    return factory_api


def _attach_state() -> dict:
    try:
        d = json.loads((_home() / "attach.json").read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _attached_factory() -> str | None:
    st = _attach_state()
    if not st.get("run"):
        return None
    return st.get("factory") or CLOUD


_SOFT = (ImportError, OSError, LookupError, AttributeError, TypeError, ValueError)  # a broken store or registry


_CTX = {"name": "", "secrets": []}  # secrets: tokens resolved for this command (a rolled-back login's too)


def _secrets() -> list[str]:
    out = list(_CTX["secrets"])
    try:
        for f in (_reg().load_store().get("factories") or {}).values():
            if f.get("token"):
                out.append(f["token"])
        for d in _reg().discover():  # an env-only login: the manifest's token_env value
            env = (d.get("manifest") or {}).get("token_env")
            if env and os.environ.get(env):
                out.append(os.environ[env])
    except _SOFT:
        pass
    return out


def _scrub(msg: str, extra: list[str]) -> str:
    for s in _secrets() + extra:
        if s:
            msg = msg.replace(s, "***")
    return msg


def _provider(name: str):
    _CTX["name"] = name
    p = _reg().provider(name)
    if p is None:
        raise _api().FactoryError(f"{name}: not logged in")
    return p


def _route(address: str) -> tuple[str, str]:
    name, ref = _reg().resolve(address, _attached_factory())
    _CTX["name"] = name
    return name, ref


def _cloud_main(argv: list[str]) -> int:
    import cloud
    return int(cloud.main(argv) or 0)


def _wrapper(on: bool) -> None:
    """Install (or restore) the status-line wrapper that shows factory runs; a broken settings.json is not fatal."""
    import cloud_status
    settings = _reg()._claude_home() / "settings.json"
    try:
        if on:
            cloud_status.install(settings, f'bash "{Path(_HERE_DIR) / "superlite-cli"}" cloud statusline')
        else:
            cloud_status.restore(settings)
    except (OSError, ValueError):
        pass


def _enabled(name: str) -> bool:
    return _reg().superlite_enabled(name)


# ---- commands -------------------------------------------------------------------------------

def cmd_list(_a) -> int:
    reg = _reg()
    store = reg.load_store()
    active = reg.active()
    att = _attached_factory()

    def mark(name: str) -> str:
        m = ("*" if name == active else "") + ("@" if name == att else "")
        return m + " " if m else "  "

    cloud_in = CLOUD in reg.logged_in()
    print(f"{mark(CLOUD)}{CLOUD} (built-in)  {'logged in' if cloud_in else 'logged out'}")
    insts = store.get("factories") or {}
    used = {f.get("kind") for f in insts.values()}
    for d in reg.discover():
        if d.get("kind") == CLOUD and d.get("path") == "built-in":
            continue
        if d.get("error"):
            print(f"  {d.get('kind')}  {d.get('path')}  error: {d['error']}")
        elif d.get("kind") not in used:
            print(f"  {d['kind']}  {d['path']}  logged out")
    logged = set(reg.logged_in())
    for name, f in insts.items():
        if name == CLOUD:
            continue
        state = "logged in" if name in logged else "logged out"
        print(f"{mark(name)}{name}  {f.get('kind')}  {f.get('target_name') or '-'}  {state}")
    return 0


def cmd_add(a) -> int:
    _reg().add(a.name, a.kind, target=a.target, superlite=a.superlite)
    print(f"{a.name}: added ({a.kind})")
    if a.superlite:
        print(f"{a.name}: superlite on")
    return 0


def _token_for(name: str, kind: str, given: str | None) -> str | None:
    if given:
        return given
    for d in _reg().discover():
        if d.get("kind") == kind and not d.get("error"):
            env = (d.get("manifest") or {}).get("token_env")
            if env and os.environ.get(env):
                return os.environ[env]
    import cloud_config
    clip = cloud_config._read_clipboard()
    return clip.strip() or None


def cmd_login(a) -> int:
    api, reg = _api(), _reg()
    if a.name == CLOUD:
        argv = ["login"]
        if a.url:
            argv += ["--url", a.url]
        if a.token:
            argv += ["--token", a.token]
        return _cloud_main(argv)
    store = reg.load_store()
    if a.name not in (store.get("factories") or {}):
        kinds = {d.get("kind") for d in reg.discover() if not d.get("error")}
        if a.name not in kinds:
            raise api.FactoryError(f"{a.name}: unknown factory")
        reg.add(a.name, a.name)
        store = reg.load_store()
    inst = store["factories"][a.name]
    token = _token_for(a.name, inst["kind"], a.token)
    if not token:
        raise api.FactoryError(f"{a.name}: no token: pass --token, set the manifest token_env, or copy it to the clipboard")
    _CTX["secrets"].append(token)  # a failed login rolls the token back out of the store; keep it redacted
    inst["token"] = token
    reg.save_store(store)
    try:
        _provider(a.name).me()
    except api.FactoryError:
        store = reg.load_store()
        store["factories"][a.name].pop("token", None)
        reg.save_store(store)
        raise
    _wrapper(True)
    print(f"{a.name}: logged in")
    return 0


def cmd_logout(a) -> int:
    reg = _reg()
    if a.name == CLOUD:
        return _cloud_logout(reg)
    store = reg.load_store()
    inst = (store.get("factories") or {}).get(a.name)
    if inst is None:
        raise _api().FactoryError(f"{a.name}: unknown factory")
    inst.pop("token", None)
    reg.save_store(store)
    if a.name not in reg.logged_in():  # still logged in through the manifest's token_env: keep everything
        if _attached_factory() == a.name:
            _cloud_main(["detach"])
        if not reg.logged_in():
            _wrapper(False)
    print(f"{a.name}: logged out")
    return 0


def _cloud_logout(reg) -> int:
    """Cloud logout restores the status line and drops the attachment; neither may take a plugin's with it."""
    plugin_attach = _attached_factory() not in (None, CLOUD)
    path = _home() / "attach.json"
    kept = path.read_bytes() if plugin_attach and path.exists() else None
    rc = _cloud_main(["logout"])
    if kept is not None:
        path.write_bytes(kept)
    if any(n != CLOUD for n in reg.logged_in()):
        _wrapper(True)
    return rc


def cmd_use(a) -> int:
    api, reg = _api(), _reg()
    if a.name == CLOUD:
        reg.use(a.name, superlite=True if a.superlite else (False if a.no_superlite else None))
        print(f"{a.name}: superlite on" if a.superlite else
              f"{a.name}: superlite off" if a.no_superlite else f"{a.name}: active")
        return 0
    target = None
    tname = None
    if a.target:
        if a.name not in reg.logged_in():  # a token in the store or in the manifest's token_env
            raise api.FactoryError(f"{a.name}: not logged in")
        t = _provider(a.name).resolve_target(a.target)
        target, tname = t["uid"], t["name"]
    reg.use(a.name, target=target, superlite=True if a.superlite else (False if a.no_superlite else None))
    if target:
        store = reg.load_store()
        store["factories"][a.name]["target_name"] = tname
        reg.save_store(store)
        print(f"{a.name}: {tname} ({target})")
    elif a.superlite:
        print(f"{a.name}: superlite on")
    elif a.no_superlite:
        print(f"{a.name}: superlite off")
    else:
        print(f"{a.name}: active")
    return 0


def cmd_send(a) -> int:
    api, reg = _api(), _reg()
    name = reg.active()
    text = " ".join(a.text).strip()
    if a.plan:
        text = Path(a.plan).read_text(encoding="utf-8")
    if not text:
        print("usage: factory send <text> | --plan FILE [--superlite]", file=sys.stderr)
        return 2
    if a.superlite and not _enabled(name):
        print(f"{name}: superlite is not enabled", file=sys.stderr)
        return 2
    p = _provider(name)
    api.require(p, "send")
    if a.superlite:
        text = api.superlite_prompt(text)
    title = a.title
    if title and name == CLOUD:
        import cloud_factory
        title = cloud_factory.server_name(title)
    run = p.send(text, title=title, repo=a.repo, base=a.base, superlite=bool(a.superlite))
    print(" ".join(str(x) for x in (run.get("name"), run.get("status"), run.get("url")) if x))
    return 0


def cmd_runs(a) -> int:
    name = _reg().active()
    if name == CLOUD:
        return _cloud_main(["runs"] + (["--all"] if a.all else []))
    runs = _provider(name).runs(all=a.all)
    if not runs:
        print("no runs")
    for r in runs:
        print(f'{r["name"]} {r["status"]} "{r.get("title") or ""}"')
    return 0


def cmd_give(a) -> int:
    name, ref = _route(a.ref)
    if name == CLOUD:
        return _cloud_main(["give", ref, a.email])
    p = _provider(name)
    _api().require(p, "give")
    p.give(ref, a.email)
    return 0


def _cloud_attach(ref: str) -> int:
    """Attach to a cloud run, then check the server knows it: no dead attachment (same check as the hook)."""
    import contextlib
    import io
    import cloud_hook
    import factory_hook
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = _cloud_main(["attach", ref])
    if rc == 0:
        bad = factory_hook._unverified(_reg().provider(CLOUD), cloud_hook._api_ref(ref))
        if bad:
            cloud_hook.clear_attach()
            print(f"{CLOUD}: {bad}", file=sys.stderr)
            return 1
    sys.stdout.write(buf.getvalue())
    return rc


def cmd_attach(a) -> int:
    name, ref = _route(a.ref)
    if name == CLOUD:
        return _cloud_attach(ref)
    info = _provider(name).attach(ref)
    h = _home()
    h.mkdir(parents=True, exist_ok=True)
    (h / "attach.json").write_text(
        json.dumps({"run": info.get("run") or ref, "id": info.get("id"), "factory": name}), encoding="utf-8")
    print(f"attached {info.get('run') or ref}")
    return 0


def cmd_detach(_a) -> int:
    return _cloud_main(["detach"])  # only clears attach.json: valid for every factory


def cmd_watch(a) -> int:
    address = a.ref or _attach_state().get("run")
    if not address:
        print("usage: factory watch <run>", file=sys.stderr)
        return 2
    name, ref = _route(address) if a.ref else (_attached_factory(), address)
    if name == CLOUD:
        import cloud_watch
        return int(cloud_watch.main([ref]) or 0)
    import factory_watch
    return int(factory_watch.run(_provider(name), ref) or 0)


def cmd_nudge(a) -> int:
    name, ref = _route(a.ref)
    p = _provider(name)
    _api().require(p, "nudge")
    p.nudge(ref, " ".join(a.text))
    print(f"{ref} {getattr(p, 'nudge_word', 'nudge')} sent")
    return 0


def cmd_cancel(a) -> int:
    name, ref = _route(a.ref)
    p = _provider(name)
    _api().require(p, "cancel")
    p.cancel(ref)
    print(f"{ref} cancelled")
    return 0


def cmd_mcp(a) -> int:
    import factory_mcp
    fn = factory_mcp.on if a.action == "on" else factory_mcp.off
    print(fn(a.name, os.getcwd()))
    return 0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="factory")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("list")
    s = sub.add_parser("add")
    s.add_argument("name")
    s.add_argument("--kind", required=True)
    s.add_argument("--target")
    s.add_argument("--superlite", action="store_true")
    s = sub.add_parser("login")
    s.add_argument("name")
    s.add_argument("--token")
    s.add_argument("--url")
    s = sub.add_parser("logout")
    s.add_argument("name")
    s = sub.add_parser("use")
    s.add_argument("name")
    s.add_argument("--target")
    s.add_argument("--superlite", action="store_true")
    s.add_argument("--no-superlite", action="store_true")
    s = sub.add_parser("send")
    s.add_argument("text", nargs="*")
    s.add_argument("--plan")
    s.add_argument("--superlite", action="store_true")
    s.add_argument("--title")
    s.add_argument("--repo")
    s.add_argument("--base")
    s = sub.add_parser("runs")
    s.add_argument("--all", action="store_true")
    s = sub.add_parser("give")
    s.add_argument("ref")
    s.add_argument("email")
    s = sub.add_parser("attach")
    s.add_argument("ref")
    sub.add_parser("detach")
    s = sub.add_parser("watch")
    s.add_argument("ref", nargs="?")
    s = sub.add_parser("nudge")
    s.add_argument("ref")
    s.add_argument("text", nargs="+")
    s = sub.add_parser("cancel")
    s.add_argument("ref")
    s = sub.add_parser("mcp")
    s.add_argument("name")
    s.add_argument("action", choices=["on", "off"])
    return p


_HANDLERS = {"list": cmd_list, "add": cmd_add, "login": cmd_login, "logout": cmd_logout, "use": cmd_use,
             "send": cmd_send, "runs": cmd_runs, "give": cmd_give, "attach": cmd_attach,
             "detach": cmd_detach, "watch": cmd_watch, "nudge": cmd_nudge, "cancel": cmd_cancel,
             "mcp": cmd_mcp}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    p = _parser()
    try:
        a = p.parse_args(argv)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 2
    if a.cmd not in _HANDLERS:
        p.print_usage(sys.stderr)
        return 2
    try:
        return _HANDLERS[a.cmd](a)
    except Exception as e:
        return _fail(e, a)


def _fail(e: Exception, a) -> int:
    """One error line, never a traceback and never a token: only a CloudError's message is shown (scrubbed)."""
    try:
        api = _api()
    except _SOFT:
        api = None
    who = _CTX["name"] or getattr(a, "name", None) or _active_name()
    if api is not None and isinstance(e, (api.Unsupported, api.CloudError)):
        msg = _scrub(str(e), [getattr(a, "token", None) or ""])
        if isinstance(e, api.Unsupported):
            print(msg, file=sys.stderr)
            return 2
        print(msg if msg.startswith(f"{who}:") else f"{who}: {msg}", file=sys.stderr)
        return 1
    print(f"{who}: {type(e).__name__}", file=sys.stderr)  # a plugin's message may carry a credential
    return 1


def _active_name() -> str:
    try:
        return _reg().active()
    except _SOFT:
        return CLOUD


if __name__ == "__main__":
    sys.exit(main())
