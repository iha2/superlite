"""Tests for factory_mcp: .mcp.json on/off, never a token."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "skills" / "swarm" / "scripts"))

import cloud_config  # noqa: E402
import factory_mcp  # noqa: E402
import factory_registry  # noqa: E402
from factory_api import FactoryError  # noqa: E402

MEMKIND = HERE / "fixtures" / "memkind"


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERLITE_HOME", str(tmp_path / "sl"))
    monkeypatch.setenv("CLAUDE_HOME", str(tmp_path / "claude"))
    monkeypatch.setenv("SUPERLITE_FACTORY_PATH", str(MEMKIND))
    cloud_config.save({"url": "http://127.0.0.1:9", "token": "slc_secret_token_value"})
    proj = tmp_path / "proj"
    proj.mkdir()
    return proj


def read(proj):
    return json.loads((proj / ".mcp.json").read_text())


def test_on_cloud_and_plugin(world):
    assert factory_mcp.on("superlite-cloud", str(world)) == \
        "superlite-cloud: MCP on in .mcp.json (MCP acts with the account's full permissions)"
    factory_registry.add("lab", "mem")
    factory_mcp.on("lab", str(world))
    d = read(world)["mcpServers"]
    assert d["superlite-cloud"] == {"type": "http", "url": "http://127.0.0.1:9/mcp",
                                    "headers": {"Authorization": "Bearer ${SUPERLITE_CLOUD_TOKEN}"}}
    assert d["mem-factory"] == {"type": "http", "url": "http://127.0.0.1:9/mcp"}


def test_no_token_in_file(world):
    factory_registry.add("lab", "mem")
    s = factory_registry.load_store()
    s["factories"]["lab"]["token"] = "mk_secret_plugin_token"
    factory_registry.save_store(s)
    factory_mcp.on("superlite-cloud", str(world))
    factory_mcp.on("lab", str(world))
    text = (world / ".mcp.json").read_text()
    assert "slc_secret_token_value" not in text and "mk_secret_plugin_token" not in text
    assert "${SUPERLITE_CLOUD_TOKEN}" in text


def test_on_idempotent(world):
    factory_mcp.on("superlite-cloud", str(world))
    a = (world / ".mcp.json").read_bytes()
    factory_mcp.on("superlite-cloud", str(world))
    assert (world / ".mcp.json").read_bytes() == a


def test_off_keeps_other_servers_byte_identical(world):
    other = {"mcpServers": {"zeta": {"command": "x", "args": ["1"]}, "alpha": {"type": "http", "url": "u"}},
             "extra": [1, 2]}
    before = json.dumps(other, indent=2) + "\n"
    (world / ".mcp.json").write_text(before)
    factory_mcp.on("superlite-cloud", str(world))
    assert (world / ".mcp.json").read_text() != before
    assert factory_mcp.off("superlite-cloud", str(world)) == "superlite-cloud: MCP off"
    assert (world / ".mcp.json").read_text() == before


def test_off_keeps_other_keys(world):
    (world / ".mcp.json").write_text(json.dumps({"mcpServers": {}, "extra": 1}))
    factory_mcp.on("superlite-cloud", str(world))
    factory_mcp.off("superlite-cloud", str(world))
    assert read(world) == {"mcpServers": {}, "extra": 1}


def test_off_removes_file_it_created(world):
    factory_mcp.on("superlite-cloud", str(world))
    factory_mcp.off("superlite-cloud", str(world))
    assert not (world / ".mcp.json").exists()


def test_off_without_file(world):
    assert factory_mcp.off("superlite-cloud", str(world)) == "superlite-cloud: MCP off"
    assert not (world / ".mcp.json").exists()


def test_not_logged_in(world):
    cloud_config.clear()
    with pytest.raises(FactoryError, match="superlite-cloud: not logged in"):
        factory_mcp.on("superlite-cloud", str(world))


def test_no_mcp_kind(world, tmp_path, monkeypatch):
    p = tmp_path / "nomcp" / "nomcp"
    (p / "factory").mkdir(parents=True)
    (p / "factory" / "m.py").write_text("def create(c):\n    return None\n")
    (p / "superlite-factory.json").write_text(json.dumps(
        {"api": 1, "kind": "nomcp", "module": "factory/m.py", "entry": "create"}))
    monkeypatch.setenv("SUPERLITE_FACTORY_PATH", os.pathsep.join([str(MEMKIND), str(p)]))
    factory_registry.add("nm", "nomcp")
    with pytest.raises(FactoryError, match="nm: no MCP server"):
        factory_mcp.on("nm", str(world))
    with pytest.raises(FactoryError, match="nm: no MCP server"):
        factory_mcp.off("nm", str(world))
