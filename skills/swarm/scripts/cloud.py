"""superlite-cli cloud <login|logout|runs|give|attach|detach|watch|statusline|hook>. Never prints the token."""
from __future__ import annotations

import argparse
import importlib
import os
import sys
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

NOT_LOGGED_IN = "not logged in: run superlite-cli cloud login"
_DELEGATES = {"watch": "cloud_watch", "statusline": "cloud_status", "hook": "cloud_hook"}


def _claude_home() -> Path:
    h = os.environ.get("CLAUDE_HOME")
    return Path(h) if h else Path.home() / ".claude"


def _err(msg: str, secret: str | None = None) -> None:
    if secret:
        msg = msg.replace(secret, "***")
    print(msg, file=sys.stderr)


def _client():
    import cloud_client
    import cloud_config
    cfg = cloud_config.load()
    if not cfg or not cfg.get("url") or not cfg.get("token"):
        return None, None
    return cloud_client.Client(cfg["url"], cfg["token"]), cfg


def _login(args) -> int:
    import cloud_client
    import cloud_config
    found = cloud_config.find(Path.cwd(), args.url, args.token)
    for w in found.get("warnings", []):
        print(f"warning: {w}", file=sys.stderr)
    if not found.get("url") or not found.get("token"):
        _err("no cloud url or token: pass --url and --token, or set "
             "SUPERLITE_CLOUD_URL and SUPERLITE_CLOUD_TOKEN")
        return 2
    token = found["token"]
    try:
        me = cloud_client.Client(found["url"], token).me()
    except cloud_client.CloudError as e:
        _err(f"login failed: {e}", token)
        return 1
    prev = (cloud_config.load() or {}).get("statusline_prev")
    cloud_config.save({"url": found["url"], "token": token, "statusline_prev": prev})
    import cloud_status
    here = Path(__file__).resolve().parent
    cloud_status.install(_claude_home() / "settings.json",
                         f'bash "{here / "superlite-cli"}" cloud statusline')
    print(f"logged in as {me['handle']} ({me['email']})")
    return 0


def _logout(_args) -> int:
    import cloud_config
    import cloud_entries
    import cloud_status
    cloud_status.restore(_claude_home() / "settings.json")
    cloud_entries.remove_all(_claude_home() / "skills")
    cloud_config.clear()
    print("logged out")
    return 0


def _mark(run: dict, notices: list) -> str:
    for n in notices:
        if n.get("task") == run.get("name") and not n.get("seen"):
            _, sep, who = (n.get("text") or "").partition(" from ")
            return f"new · given by {who}" if sep and who else "new"
    return ""


def cmd_runs(args) -> int:
    import cloud_client
    client, cfg = _client()
    if client is None:
        _err(NOT_LOGGED_IN)
        return 2
    try:
        runs = client.runs(all=args.all)
        try:
            notices = client.status().get("notices", [])
        except cloud_client.CloudError:
            notices = []
    except cloud_client.CloudError as e:
        _err(f"error: {e}", cfg["token"])
        return 1
    try:
        import cloud_entries
        me = client.me()
        admin = me.get("role") == "admin"
        if args.all or not admin:  # an admin's default list omits others' runs; syncing it would drop their entries
            cloud_entries.sync(runs, _claude_home() / "skills", me.get("handle", ""), admin)
    except Exception:
        pass
    for r in runs:
        line = f"{r['name']}  {r['status']}"
        mark = _mark(r, notices)
        print(f"{line}  {mark}" if mark else line)
    return 0


def _give(args) -> int:
    import cloud_client
    client, cfg = _client()
    if client is None:
        _err(NOT_LOGGED_IN)
        return 2
    try:
        t = client.give(args.ref, args.email)
    except cloud_client.CloudError as e:
        _err(f"error: {e}", cfg["token"])
        return 1
    import cloud_hook
    print(cloud_hook.give_line(t, args.ref, args.email))
    return 0


def _hook_verb(verb: str, ref: str = "") -> int:
    """attach/detach reuse cloud_hook's logic (attach.json state file); prints its one line."""
    import cloud_client
    import cloud_hook
    client, cfg = (None, None) if verb == "detach" else _client()
    if client is None and verb != "detach":
        _err(NOT_LOGGED_IN)
        return 2
    try:
        res = cloud_hook._slash(client, verb, ref)
    except cloud_client.CloudError as e:
        _err(f"error: {e}", cfg["token"] if cfg else None)
        return 1
    print(res["reason"])
    return 0


def _attach(args) -> int:
    return _hook_verb("attach", args.ref)


def _detach(_args) -> int:
    return _hook_verb("detach")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in _DELEGATES:
        mod = importlib.import_module(_DELEGATES[argv[0]])
        return int(mod.main(argv[1:]) or 0)
    p = argparse.ArgumentParser(prog="superlite-cli cloud")
    sub = p.add_subparsers(dest="cmd")
    lg = sub.add_parser("login")
    lg.add_argument("--url")
    lg.add_argument("--token")
    sub.add_parser("logout")
    rn = sub.add_parser("runs")
    rn.add_argument("--all", action="store_true")
    gv = sub.add_parser("give")
    gv.add_argument("ref")
    gv.add_argument("email")
    at = sub.add_parser("attach")
    at.add_argument("ref")
    sub.add_parser("detach")
    for name in _DELEGATES:  # routed above before parsing; listed so --help and the doc contract see them
        sub.add_parser(name, add_help=False).add_argument("rest", nargs="*")
    try:
        args = p.parse_args(argv)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 2
    handlers = {"login": _login, "logout": _logout, "runs": cmd_runs, "give": _give,
                "attach": _attach, "detach": _detach}
    if args.cmd not in handlers:
        p.print_usage(sys.stderr)
        return 2
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
