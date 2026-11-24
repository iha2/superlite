"""Tests for factory_api, the memkind fixture and the e2e harness helpers."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "skills" / "swarm" / "scripts"))
sys.path.insert(0, str(HERE))

import factory_e2e  # noqa: E402
from cloud_client import CloudError  # noqa: E402
from factory_api import SUPERLITE_PREAMBLE, FactoryError, Unsupported, require, superlite_prompt  # noqa: E402

MEMKIND = HERE / "fixtures" / "memkind"


class _Provider:
    name = "mem"
    caps = frozenset({"send", "status"})


def _load_memkind():
    spec = importlib.util.spec_from_file_location("memkind_t", MEMKIND / "factory" / "memkind.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _cfg(name="lab"):
    return {"name": name, "kind": "mem", "token": "mk-test", "target": "t1", "target_name": name,
            "superlite": False}


def test_unsupported_text():
    assert str(Unsupported("mem", "give")) == "mem: give is not supported"


def test_require():
    p = _Provider()
    require(p, "send")
    try:
        require(p, "give")
    except Unsupported as exc:
        assert str(exc) == "mem: give is not supported"
    else:
        raise AssertionError("require did not raise")


def test_superlite_prompt():
    assert SUPERLITE_PREAMBLE.startswith("Build this with superlite")
    assert "Skill tool" in SUPERLITE_PREAMBLE and "/superlite:ai" not in SUPERLITE_PREAMBLE
    assert "superlite:using-superlite" in SUPERLITE_PREAMBLE
    assert "superlite:start" in SUPERLITE_PREAMBLE
    assert "superlite-cli orchestrator set ai" in SUPERLITE_PREAMBLE
    assert superlite_prompt("Fix") == SUPERLITE_PREAMBLE + "\n\n" + "Fix"


def test_error_is_clouderror():
    assert issubclass(FactoryError, CloudError)
    assert FactoryError("x", 404).status == 404
    assert FactoryError("x").status is None


def test_manifest():
    m = json.loads((MEMKIND / "superlite-factory.json").read_text())
    assert m["kind"] == "mem" and m["module"] == "factory/memkind.py" and m["entry"] == "create"
    assert m["token_env"] == "MEM_API_KEY" and m["mcp"]["name"] == "mem-factory"


def test_memkind_send_and_task(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERLITE_HOME", str(tmp_path))
    mk = _load_memkind()
    p = mk.create(_cfg())
    assert p.nudge_word == "follow-up" and p.name == "lab"
    assert p.caps == frozenset({"me", "status", "runs", "task", "nudge", "respond", "cancel", "attach",
                                "events", "send", "target"})
    a = p.send("Fix flaky test")
    b = p.send("Other")
    assert a["status"] == "queued" and len(a["id"]) == 12 and a["name"] == a["id"][:8]
    assert a["id"] != b["id"]
    assert p.task(a["name"])["id"] == a["id"]
    try:
        p.task("zzzz")
    except FactoryError as e:
        assert str(e) == "lab: zzzz: not found" and e.status == 404
    else:
        raise AssertionError("task did not raise")
    st = json.loads((tmp_path / "memkind-lab.json").read_text())
    assert st["requests"][0] == {"verb": "send", "prompt": "Fix flaky test", "superlite": False}
    assert mk.create({**_cfg(), "token": ""}) is None


def test_memkind_events(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERLITE_HOME", str(tmp_path))
    p = _load_memkind().create(_cfg())
    r = p.send("go")
    evs = list(p.events(r["name"]))
    r8 = r["name"]
    assert evs == [
        {"id": 1, "kind": "status", "data": {"run": r8, "to": "running"}},
        {"id": 2, "kind": "text", "data": {"text": "Looking at the test"}},
        {"id": 3, "kind": "tool", "data": {"tool": "run_command", "target": "pytest"}},
        {"id": 4, "kind": "status", "data": {"run": r8, "to": "done"}},
    ]
    assert [e["id"] for e in p.events(r8, after=2)] == [3, 4]


def test_memkind_state_shared_across_processes(tmp_path):
    code = (
        "import importlib.util as u, json, sys; "
        f"s=u.spec_from_file_location('m', {str(MEMKIND / 'factory' / 'memkind.py')!r}); "
        "m=u.module_from_spec(s); s.loader.exec_module(m); "
        "p=m.create(json.loads(sys.argv[2])); "
        "print(json.dumps(p.send(sys.argv[1]) if sys.argv[1] != 'LIST' else p.runs()))"
    )
    env = {**os.environ, "SUPERLITE_HOME": str(tmp_path)}
    r = subprocess.run([sys.executable, "-c", code, "Fix", json.dumps(_cfg())], env=env,
                       capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    sent = json.loads(r.stdout)
    r2 = subprocess.run([sys.executable, "-c", code, "LIST", json.dumps(_cfg())], env=env,
                        capture_output=True, text=True, timeout=20)
    assert [x["id"] for x in json.loads(r2.stdout)] == [sent["id"]]


def test_harness_env_and_cloud_login(tmp_path):
    e = factory_e2e.env(tmp_path, cloud_url="http://127.0.0.1:1")
    assert e["SUPERLITE_FACTORY_PATH"] == str(MEMKIND)
    assert e["SUPERLITE_CLOUD_URL"] == "http://127.0.0.1:1"
    assert (Path(e["_cwd"]) / ".git").exists()
    assert e["SUPERLITE_HOME"].startswith(str(tmp_path)) and e["CLAUDE_HOME"].startswith(str(tmp_path))
    factory_e2e.cloud_login(e, "http://127.0.0.1:1")
    p = Path(e["SUPERLITE_HOME"]) / "cloud.json"
    assert json.loads(p.read_text()) == {"url": "http://127.0.0.1:1", "token": "slc_test_tok"}
    assert os.name == "nt" or (p.stat().st_mode & 0o777) == 0o600
