"""Tests for factory_registry: discovery, store, address grammar, load by path."""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "skills" / "swarm" / "scripts"))

import factory_registry as fr  # noqa: E402
from factory_api import FactoryError  # noqa: E402

MEMKIND = HERE / "fixtures" / "memkind"


def make_plugin(root, kind="fake", manifest=None, module_src=None, write_module=True):
    root.mkdir(parents=True, exist_ok=True)
    m = {"api": 1, "kind": kind, "label": kind.title(), "module": "factory/mod.py",
         "entry": "create", "token_env": "FAKE_KEY_" + kind.upper()}
    m.update(manifest or {})
    (root / "superlite-factory.json").write_text(json.dumps(m))
    if write_module:
        (root / "factory").mkdir(exist_ok=True)
        (root / "factory" / "mod.py").write_text(module_src or (
            "class P:\n"
            "    caps = frozenset()\n"
            "    nudge_word = 'follow-up'\n"
            "    def __init__(s, c):\n"
            "        s.cfg = c; s.name = c['name']; s._token = c.get('token')\n"
            "def create(c):\n    return P(c)\n"))
    return root


@pytest.fixture(autouse=True)
def _homes(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERLITE_HOME", str(tmp_path / "sl-home"))
    monkeypatch.setenv("CLAUDE_HOME", str(tmp_path / "claude-home"))
    monkeypatch.delenv("SUPERLITE_FACTORY_PATH", raising=False)
    for k in [k for k in os.environ if k.startswith("FAKE_KEY_")]:
        monkeypatch.delenv(k)


@pytest.fixture
def plug(tmp_path, monkeypatch):
    r = make_plugin(tmp_path / "plug" / "fakekind")
    monkeypatch.setenv("SUPERLITE_FACTORY_PATH", str(r))
    return r


def test_discover_builtin_first(plug):
    rows = fr.discover()
    assert rows[0] == {"kind": "superlite-cloud", "path": "built-in", "error": None}
    assert rows[1]["kind"] == "fake" and rows[1]["error"] is None
    assert rows[1]["manifest"]["api"] == 1 and os.path.isabs(rows[1]["path"])


def test_discover_memkind(monkeypatch):
    monkeypatch.setenv("SUPERLITE_FACTORY_PATH", str(MEMKIND))
    rows = fr.discover()
    assert rows[1]["kind"] == "mem" and rows[1]["label"] == "Memory factory (tests)"
    assert rows[1]["error"] is None and rows[1]["path"] == str(MEMKIND.resolve())


def test_store_mode_0600(plug):
    fr.save_store({"active": "superlite-cloud", "factories": {}})
    p = os.path.join(os.environ["SUPERLITE_HOME"], "factories.json")
    assert os.name == "nt" or stat.S_IMODE(os.stat(p).st_mode) == 0o600
    fr.add("web", "fake")
    assert os.name == "nt" or stat.S_IMODE(os.stat(p).st_mode) == 0o600


def test_load_store_default(plug):
    assert fr.load_store() == {"active": "superlite-cloud", "factories": {}}


@pytest.mark.parametrize("case", ["badjson", "nomodule", "api2", "raises"])
def test_broken_manifest_isolated(tmp_path, monkeypatch, plug, case):
    bad = tmp_path / "bad" / "brokenkind"
    if case == "badjson":
        bad.mkdir(parents=True)
        (bad / "superlite-factory.json").write_text("{nope")
    elif case == "nomodule":
        make_plugin(bad, kind="brokenkind")
        m = json.loads((bad / "superlite-factory.json").read_text())
        del m["module"]
        (bad / "superlite-factory.json").write_text(json.dumps(m))
    elif case == "api2":
        make_plugin(bad, kind="brokenkind", manifest={"api": 2})
    else:
        make_plugin(bad, kind="brokenkind", module_src="raise RuntimeError('boom')\n")
    monkeypatch.setenv("SUPERLITE_FACTORY_PATH", os.pathsep.join([str(bad), str(plug)]))
    by = {r["kind"]: r for r in fr.discover()}
    assert by["fake"]["error"] is None
    if case == "nomodule":
        assert by["brokenkind"]["error"] == 'manifest: missing "module"'
    if case != "raises":
        assert by["brokenkind"]["error"]
        with pytest.raises(FactoryError):
            fr.add("x", "brokenkind")
    else:
        fr.add("x", "brokenkind")
        with pytest.raises(FactoryError):
            fr.provider("x")
    fr.add("good", "fake")
    assert fr.provider("good").name == "good"


def test_installed_plugins_scope(tmp_path):
    a = make_plugin(tmp_path / "a", kind="ka")
    b = make_plugin(tmp_path / "b", kind="kb")
    c = make_plugin(tmp_path / "c", kind="kc")
    proj = tmp_path / "proj"
    (proj / "sub").mkdir(parents=True)
    ch = tmp_path / "claude-home" / "plugins"
    ch.mkdir(parents=True)
    (ch / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {
        "a@m": [{"scope": "user", "installPath": str(a)}],
        "b@m": [{"scope": "project", "installPath": str(b), "projectPath": str(proj)}],
        "c@m": [{"scope": "project", "installPath": str(c), "projectPath": str(tmp_path / "other")}],
    }}))
    kinds = {r["kind"] for r in fr.discover(str(proj / "sub"))}
    assert {"ka", "kb"} <= kinds and "kc" not in kinds
    kinds = {r["kind"] for r in fr.discover(str(tmp_path))}
    assert "ka" in kinds and "kb" not in kinds


def test_add_and_use(plug):
    r = fr.add("web", "fake", target="fct_web")
    assert r["name"] == "web" and r["kind"] == "fake" and r["target"] == "fct_web"
    assert fr.kind_of("web") == "fake"
    assert fr.active() == "superlite-cloud"
    r = fr.use("fake")
    assert r["name"] == "fake" and fr.active() == "fake"
    fr.use("web", target="t2", superlite=True)
    s = fr.load_store()["factories"]["web"]
    assert fr.active() == "web" and s["target"] == "t2" and s["superlite"] is True
    assert fr.kind_of("superlite-cloud") == "superlite-cloud"
    with pytest.raises(FactoryError):
        fr.add("Bad Name", "fake")
    with pytest.raises(FactoryError):
        fr.add("z", "nokind")
    with pytest.raises(FactoryError, match="ghost"):
        fr.kind_of("ghost")


def test_provider_by_path(plug):
    fr.add("payments", "fake", target="t", superlite=True)
    s = fr.load_store()
    s["factories"]["payments"]["token"] = "tok"
    fr.save_store(s)
    p = fr.provider("payments")
    assert p.cfg["kind"] == "fake" and p.cfg["token"] == "tok" and p.cfg["target"] == "t"
    assert p.cfg["superlite"] is True and "target_name" in p.cfg
    assert "fake" not in sys.modules and "superlite_factory_fake" in sys.modules
    with pytest.raises(FactoryError, match="nope: no such factory"):
        fr.provider("nope")


def test_provider_memkind(monkeypatch):
    monkeypatch.setenv("SUPERLITE_FACTORY_PATH", str(MEMKIND))
    monkeypatch.setenv("MEM_API_KEY", "envtok")
    fr.add("lab", "mem", target="t1")
    p = fr.provider("lab")
    assert p.name == "lab" and p._token == "envtok"
    assert "memkind" not in sys.modules


def test_provider_builtin_lazy(monkeypatch):
    import types
    fake = types.SimpleNamespace(create=lambda cfg: ("cloud", cfg))
    monkeypatch.setitem(sys.modules, "cloud_factory", fake)
    assert fr.provider("superlite-cloud") == ("cloud", {"name": "superlite-cloud"})


def test_logged_in(plug):
    import cloud_config
    assert fr.logged_in() == []
    cloud_config.save({"url": "http://127.0.0.1:1", "token": "slc_x"})
    fr.add("payments", "fake")
    assert fr.logged_in() == ["superlite-cloud"]
    s = fr.load_store()
    s["factories"]["payments"]["token"] = "t"
    fr.save_store(s)
    assert fr.logged_in() == ["superlite-cloud", "payments"]


def test_address_grammar(plug):
    fr.add("payments", "fake")
    assert fr.resolve("payments:1a2b3c4d") == ("payments", "1a2b3c4d")
    assert fr.resolve("superlite-cloud:r1") == ("superlite-cloud", "r1")
    assert fr.resolve("chris/login_fix", attached="payments") == ("payments", "chris/login_fix")
    assert fr.resolve("nope:x") == (fr.active(), "nope:x")
    assert fr.resolve("Payments:x") == (fr.active(), "Payments:x")
    assert fr.resolve("abc") == ("superlite-cloud", "abc")
    fr.use("payments")
    assert fr.resolve("nope:x", attached="superlite-cloud") == ("superlite-cloud", "nope:x")


def test_builtin_superlite_flag(plug):
    assert fr.superlite_enabled("superlite-cloud") is False
    r = fr.use("superlite-cloud", superlite=True)
    assert r == {"name": "superlite-cloud", "kind": "superlite-cloud"}
    st = fr.load_store()
    assert st["builtin_superlite"] is True and "superlite-cloud" not in st["factories"]
    assert fr.superlite_enabled("superlite-cloud") is True
    fr.use("superlite-cloud", superlite=False)
    assert fr.superlite_enabled("superlite-cloud") is False
    fr.add("web", "fake", superlite=True)
    assert fr.superlite_enabled("web") is True and fr.superlite_enabled("ghost") is False


def test_create_failure_never_echoes_token(tmp_path, monkeypatch):
    r = make_plugin(tmp_path / "plug" / "boomkind", kind="boom", module_src=(
        "def create(c):\n    raise RuntimeError('bad token ' + str(c.get('token')))\n"))
    monkeypatch.setenv("SUPERLITE_FACTORY_PATH", str(r))
    fr.add("b1", "boom")
    s = fr.load_store()
    s["factories"]["b1"]["token"] = "sekret-tok"
    fr.save_store(s)
    with pytest.raises(FactoryError) as ei:
        fr.provider("b1")
    assert "sekret-tok" not in str(ei.value) and "RuntimeError" in str(ei.value)


def test_readd_with_another_kind_drops_the_credential(tmp_path, monkeypatch):
    a = make_plugin(tmp_path / "plug" / "kinda", kind="kinda")
    b = make_plugin(tmp_path / "plug" / "kindb", kind="kindb")
    monkeypatch.setenv("SUPERLITE_FACTORY_PATH", os.pathsep.join([str(a), str(b)]))
    fr.add("lab", "kinda", target="t1")
    store = fr.load_store()
    store["factories"]["lab"].update(token="secret-a", target_name="T One")
    fr.save_store(store)
    fr.add("lab", "kindb", target="t1")
    inst = fr.load_store()["factories"]["lab"]
    assert inst["kind"] == "kindb" and inst["token"] is None and inst["target_name"] is None
    assert fr.provider("lab").cfg["token"] is None
    fr.add("lab", "kindb", target="t1")  # the same kind keeps what it has
    store = fr.load_store()
    store["factories"]["lab"]["token"] = "secret-b"
    fr.save_store(store)
    fr.add("lab", "kindb", target="t1")
    assert fr.load_store()["factories"]["lab"]["token"] == "secret-b"


def test_plugin_defined_exception_subclass_is_reported_by_type(tmp_path, monkeypatch):
    r = make_plugin(tmp_path / "plug" / "ownerr", kind="ownerr", module_src=(
        "class ProviderError(Exception):\n    pass\n"
        "def create(c):\n    raise ProviderError('bad token ' + str(c.get('token')))\n"))
    bad = make_plugin(tmp_path / "plug" / "badimp", kind="badimp", module_src=(
        "class Boom(Exception):\n    pass\nraise Boom('import with sekret-tok')\n"))
    monkeypatch.setenv("SUPERLITE_FACTORY_PATH", os.pathsep.join([str(r), str(bad)]))
    s = fr.load_store()
    for n in ("o1", "b1"):
        fr.add(n, "ownerr" if n == "o1" else "badimp")
    s = fr.load_store()
    s["factories"]["o1"]["token"] = s["factories"]["b1"]["token"] = "sekret-tok"
    fr.save_store(s)
    for name, typ in (("o1", "ProviderError"), ("b1", "Boom")):
        with pytest.raises(FactoryError) as ei:
            fr.provider(name)
        assert typ in str(ei.value) and "sekret-tok" not in str(ei.value)


def test_use_saves_orchestrator(plug):
    assert fr.orchestrator_default("web") == "ai"
    fr.add("web", "fake")
    assert fr.orchestrator_default("web") == "ai"  # old entry without the key reads ai
    fr.use("web", orchestrator="human")
    assert fr.orchestrator_default("web") == "human"
    fr.use("web", target="t3")  # an unrelated use keeps it
    assert fr.orchestrator_default("web") == "human"
    fr.use("web", orchestrator="ai")
    assert fr.orchestrator_default("web") == "ai"
    fr.use("superlite-cloud", orchestrator="human")
    assert fr.orchestrator_default("superlite-cloud") == "human"
    with pytest.raises(FactoryError, match="orchestrator"):
        fr.use("web", orchestrator="robot")
