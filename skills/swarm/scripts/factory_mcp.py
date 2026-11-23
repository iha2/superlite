"""factory_mcp: switch a factory's MCP server on or off in a project's .mcp.json. Never writes a token."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE_DIR = str(Path(__file__).resolve().parent)
if _HERE_DIR not in sys.path:
    sys.path.insert(0, _HERE_DIR)

import factory_registry  # noqa: E402
from factory_api import FactoryError  # noqa: E402

BUILTIN = factory_registry.BUILTIN


def _server(name: str) -> tuple[str, dict]:
    kind = factory_registry.kind_of(name)
    if kind == BUILTIN:
        import cloud_config
        cfg = cloud_config.load() or {}
        url = (cfg.get("url") or "").rstrip("/")
        if not url:
            raise FactoryError(f"{name}: not logged in")
        return BUILTIN, {"type": "http", "url": url + "/mcp",
                         "headers": {"Authorization": "Bearer ${SUPERLITE_CLOUD_TOKEN}"}}
    for row in factory_registry.discover():
        if row["kind"] == kind and not row["error"]:
            mcp = (row.get("manifest") or {}).get("mcp")
            if isinstance(mcp, dict) and mcp.get("name") and mcp.get("url"):
                return mcp["name"], {"type": "http", "url": mcp["url"]}
    raise FactoryError(f"{name}: no MCP server")


def _load(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        raise FactoryError(f"{path.name}: not valid JSON") from None
    if not isinstance(d, dict):
        raise FactoryError(f"{path.name}: not a JSON object")
    return d


def _write(path: Path, d: dict) -> None:
    path.write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8", newline="\n")


def on(name: str, project: str) -> str:
    key, entry = _server(name)
    path = Path(project) / ".mcp.json"
    d = _load(path) or {}
    servers = d.get("mcpServers")
    if not isinstance(servers, dict):
        servers = d["mcpServers"] = {}
    servers[key] = entry
    _write(path, d)
    return f"{name}: MCP on in .mcp.json (MCP acts with the account's full permissions)"


def off(name: str, project: str) -> str:
    key, _ = _server(name)
    path = Path(project) / ".mcp.json"
    d = _load(path)
    if d and isinstance(d.get("mcpServers"), dict) and key in d["mcpServers"]:
        del d["mcpServers"][key]
        if not d["mcpServers"] and set(d) == {"mcpServers"}:
            path.unlink()
        else:
            _write(path, d)
    return f"{name}: MCP off"
