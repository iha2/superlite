"""factory_registry: discover plugin kinds, keep named factory instances, resolve addresses."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from pathlib import Path

_HERE_DIR = str(Path(__file__).resolve().parent)
if _HERE_DIR not in sys.path:
    sys.path.insert(0, _HERE_DIR)

import state  # noqa: E402
from factory_api import CloudError, FactoryError  # noqa: E402

# What a plugin (or a store it left behind) can raise: callers catch these, never everything.
# Errors are reported by type name only, never the message or a traceback: they may carry a token.
PLUGIN_ERRORS = (CloudError, ArithmeticError, AssertionError, AttributeError, EOFError, ImportError, LookupError,
                 NameError, OSError, RuntimeError, SyntaxError, TypeError, ValueError, SystemExit)

BUILTIN = "superlite-cloud"
BUILTIN_SUPERLITE_KEY = "builtin_superlite"  # top-level, so the factories table stays plugin-only
MANIFEST = "superlite-factory.json"
_NAME = re.compile(r"[a-z0-9-]+")
_ADDR = re.compile(r"([a-z0-9-]+):(.*)", re.S)


def _home() -> Path:
    h = os.environ.get("SUPERLITE_HOME")
    return Path(h) if h else Path.home() / state.DIR_NAME


def _store_path() -> Path:
    return _home() / "factories.json"


def _claude_home() -> Path:
    h = os.environ.get("CLAUDE_HOME")
    return Path(h) if h else Path.home() / ".claude"


def _installed(e: dict, here: Path) -> bool:
    """Is this installed_plugins entry in scope: user-wide, or a project that contains `here`."""
    pp = e.get("projectPath")
    if e.get("scope", "user") == "user" or not pp:
        return e.get("scope", "user") == "user"
    pr = Path(pp).resolve()
    return here == pr or pr in here.parents


def _installed_roots(here: Path) -> list[Path]:
    data = json.loads((_claude_home() / "plugins" / "installed_plugins.json").read_text(encoding="utf-8"))
    plugins = data.get("plugins", data) if isinstance(data, dict) else {}
    entries = [e for v in plugins.values() for e in (v if isinstance(v, list) else [v])]
    return [Path(e["installPath"]) for e in entries
            if isinstance(e, dict) and e.get("installPath") and _installed(e, here)]


def _roots(cwd: str | None) -> list[Path]:
    roots: list[Path] = []
    here = Path(cwd or os.getcwd()).resolve()
    try:
        roots += _installed_roots(here)
    except (OSError, ValueError, AttributeError):
        pass
    for p in os.environ.get("SUPERLITE_FACTORY_PATH", "").split(os.pathsep):
        if p.strip():
            roots.append(Path(p.strip()))
    return roots


def _row(root: Path) -> dict | None:
    mf = root / MANIFEST
    if not mf.is_file():
        return None
    path = str(root.resolve())
    row = {"kind": root.name, "path": path, "error": None}
    try:
        m = json.loads(mf.read_text(encoding="utf-8"))
        if not isinstance(m, dict):
            raise ValueError("not an object")
    except (OSError, ValueError) as e:
        row["error"] = f"manifest: unreadable ({type(e).__name__})"
        return row
    if isinstance(m.get("kind"), str) and _NAME.fullmatch(m["kind"]):
        row["kind"] = m["kind"]
    for key in ("kind", "module"):
        if not isinstance(m.get(key), str) or not m[key]:
            row["error"] = f'manifest: missing "{key}"'
            return row
    if m.get("api") != 1:
        row["error"] = f"manifest: unsupported api {m.get('api')!r}"
        return row
    mod = (root / m["module"]).resolve()
    if root.resolve() not in mod.parents or not mod.is_file():
        row["error"] = f'manifest: module "{m["module"]}" not found'
        return row
    row["label"] = m.get("label", m["kind"])
    row["manifest"] = m
    return row


def discover(cwd: str | None = None) -> list[dict]:
    rows = [{"kind": BUILTIN, "path": "built-in", "error": None}]
    seen = {BUILTIN}
    for root in _roots(cwd):
        try:
            row = _row(root)
        except PLUGIN_ERRORS as e:  # isolation: one broken plugin never breaks another
            row = {"kind": root.name, "path": str(root), "error": f"manifest: {type(e).__name__}"}
        if row is None or (row["kind"] in seen):
            continue
        seen.add(row["kind"])
        rows.append(row)
    return rows


def load_store() -> dict:
    try:
        d = json.loads(_store_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = None
    if not isinstance(d, dict):
        d = {}
    if not isinstance(d.get("factories"), dict):
        d["factories"] = {}
    if not isinstance(d.get("active"), str) or not d["active"]:
        d["active"] = BUILTIN
    return d


def save_store(store: dict) -> None:
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(store, indent=2)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(data)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def _kind_row(kind: str) -> dict:
    for r in discover():
        if r["kind"] == kind and kind != BUILTIN:
            if r["error"]:
                raise FactoryError(f"{kind}: {r['error']}")
            return r
    raise FactoryError(f"unknown factory kind: {kind}")


def add(name: str, kind: str, target: str | None = None, superlite: bool = False) -> dict:
    if not _NAME.fullmatch(name or "") or name == BUILTIN:
        raise FactoryError(f"invalid factory name: {name!r}")
    _kind_row(kind)
    store = load_store()
    old = store["factories"].get(name, {})
    if old.get("kind", kind) != kind:  # another kind is another service: its credential and target never carry over
        old = {}
    inst = {"kind": kind, "token": old.get("token"), "target": target,
            "target_name": old.get("target_name") if target == old.get("target") else None,
            "superlite": bool(superlite)}
    store["factories"][name] = inst
    save_store(store)
    return {"name": name, **inst}


def use(name: str, target: str | None = None, superlite: bool | None = None) -> dict:
    store = load_store()
    if name == BUILTIN:
        store["active"] = name
        if superlite is not None:
            store[BUILTIN_SUPERLITE_KEY] = bool(superlite)
        save_store(store)
        return {"name": name, "kind": BUILTIN}
    if name not in store["factories"]:
        add(name, name)
        store = load_store()
    inst = store["factories"][name]
    if target is not None:
        if target != inst.get("target"):
            inst["target_name"] = None
        inst["target"] = target
    if superlite is not None:
        inst["superlite"] = bool(superlite)
    store["active"] = name
    save_store(store)
    return {"name": name, **inst}


def superlite_enabled(name: str) -> bool:
    store = load_store()
    if name == BUILTIN:
        return bool(store.get(BUILTIN_SUPERLITE_KEY))
    return bool((store["factories"].get(name) or {}).get("superlite"))


def active() -> str:
    return load_store()["active"]


def kind_of(name: str) -> str:
    if name == BUILTIN:
        return BUILTIN
    inst = load_store()["factories"].get(name)
    if not inst:
        raise FactoryError(f"{name}: no such factory")
    return inst.get("kind", name)


def _load_module(row: dict):
    """Load the kind's module by file path under a private name; never as a bare importable name."""
    m = row["manifest"]
    path = (Path(row["path"]) / m["module"]).resolve()
    modname = "superlite_factory_" + re.sub(r"\W", "_", row["kind"])
    cached = sys.modules.get(modname)
    if cached is not None and getattr(cached, "__file__", None) == str(path):
        return cached
    spec = importlib.util.spec_from_file_location(modname, path)
    if spec is None or spec.loader is None:
        raise FactoryError(f"{row['kind']}: cannot load module")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    try:
        spec.loader.exec_module(mod)
    except PLUGIN_ERRORS as e:
        sys.modules.pop(modname, None)
        raise FactoryError(f"{row['kind']}: module failed to import ({type(e).__name__})") from None
    return mod


def provider(name: str):
    if name == BUILTIN:
        import cloud_factory  # lazy: the built-in provider wraps cloud_client
        return cloud_factory.create({"name": BUILTIN})
    inst = load_store()["factories"].get(name)
    if not inst:
        raise FactoryError(f"{name}: no such factory")
    row = _kind_row(inst.get("kind", name))
    mod = _load_module(row)
    m = row["manifest"]
    create = getattr(mod, m.get("entry") or "create", None)
    if not callable(create):
        raise FactoryError(f"{row['kind']}: entry not found")
    token = inst.get("token") or os.environ.get(m.get("token_env") or "") or None
    try:
        return create({"name": name, "kind": row["kind"], "token": token, "target": inst.get("target"),
                       "target_name": inst.get("target_name"), "superlite": bool(inst.get("superlite"))})
    except FactoryError:
        raise
    except PLUGIN_ERRORS as e:  # never echo the plugin's message: it may carry the token
        raise FactoryError(f"{row['kind']}: create failed ({type(e).__name__})") from None


def logged_in() -> list[str]:
    out = []
    try:
        import cloud_config
        cfg = cloud_config.load()
        if cfg and cfg.get("token"):
            out.append(BUILTIN)
    except PLUGIN_ERRORS:
        pass
    store = load_store()
    envs = {r["kind"]: (r.get("manifest") or {}).get("token_env") for r in discover()}
    for name, inst in store["factories"].items():
        env = envs.get(inst.get("kind"))
        if inst.get("token") or (env and os.environ.get(env)):
            out.append(name)
    return out


def resolve(address: str, attached: str | None = None) -> tuple[str, str]:
    m = _ADDR.fullmatch(address or "")
    if m and (m.group(1) == BUILTIN or m.group(1) in load_store()["factories"]):
        return m.group(1), m.group(2)
    return (attached or active()), address
