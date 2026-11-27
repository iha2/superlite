"""T2: factory_watch render and run, against the memkind fixture and cloud_watch."""
import importlib.util
import io
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "swarm" / "scripts"))
import cloud_watch  # noqa: E402
import factory_api  # noqa: E402
import factory_watch  # noqa: E402


@pytest.fixture(autouse=True)
def _stream_inline(monkeypatch):
    """These tests run watch under pytest, which may sit inside an agent harness: let it stream."""
    monkeypatch.setenv("SUPERLITE_WATCH_INLINE", "1")


def _memkind(name="lab"):
    p = ROOT / "tests" / "scripts" / "fixtures" / "memkind" / "factory" / "memkind.py"
    spec = importlib.util.spec_from_file_location("memkind_t2", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.create({"name": name, "kind": "mem", "token": "mk-test", "target": "t1", "target_name": name,
                     "superlite": False})


def test_render_matches_cloud_watch():
    r = factory_watch.render
    assert r({"kind": "status", "data": {"to": "running"}}) == "status: running"
    assert r({"kind": "status", "data": {"to": "done", "url": "https://w/s"}}) == "status: done\nsession: https://w/s"
    assert r({"kind": "text", "data": {"text": "Looking\nat\tit\x07"}}) == "Looking at it"
    assert r({"kind": "text", "data": {"text": ""}}) is None
    assert r({"kind": "tool", "data": {"tool": "run_command", "target": "pytest tests/test_x.py"}}) == \
        "tool: run_command pytest tests/test_x.py"
    ev = {"kind": "nudge", "data": {"text": "hi", "by": "bo"}}
    assert r(ev) == cloud_watch.render(ev) == "» nudge from bo: hi"
    assert r({"kind": "mystery", "data": {}}) is None


class FakeProv:
    name = "fake"
    caps = frozenset()

    def __init__(self, events):
        self._events = events
        self.calls = []

    def events(self, ref, after=0):
        for e in self._events:
            if e["id"] > after:
                yield e

    def nudge(self, ref, text):
        self.calls.append(("nudge", ref, text))

    def respond(self, ref, text):
        self.calls.append(("respond", ref, text))

    def cancel(self, ref):
        self.calls.append(("cancel", ref))


def test_watch_terminal_exit_fake(capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    evs = [{"id": 1, "kind": "status", "data": {"to": "queued"}},
           {"id": 2, "kind": "text", "data": {"text": "hello"}},
           {"id": 3, "kind": "status", "data": {"to": "done", "url": "https://w/s"}},
           {"id": 4, "kind": "text", "data": {"text": "never printed"}}]
    assert factory_watch.run(FakeProv(evs), "ref") == 0
    out = capsys.readouterr().out.splitlines()
    assert out == ["status: queued", "hello", "status: done", "session: https://w/s"]
    capsys.readouterr()
    factory_watch.run(FakeProv(evs), "ref", after=2)
    assert capsys.readouterr().out.splitlines() == ["status: done", "session: https://w/s"]


def test_run_stdin_and_errors(capsys, monkeypatch):
    prov = FakeProv([{"id": 1, "kind": "status", "data": {"to": "done"}}])
    monkeypatch.setattr(sys, "stdin", io.StringIO("do more\n/approve\n/cancel\n"))
    assert factory_watch.run(prov, "ref") == 0
    import time
    for _ in range(50):
        if len(prov.calls) == 3:
            break
        time.sleep(0.02)
    assert prov.calls == [("nudge", "ref", "do more"), ("respond", "ref", "approve"), ("cancel", "ref")]

    class Bad(FakeProv):
        _token = "sekret-tok"

        def events(self, ref, after=0):
            raise factory_api.FactoryError("boom sekret-tok")
            yield

    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    capsys.readouterr()
    assert factory_watch.run(Bad([]), "ref") == 1
    assert "sekret-tok" not in capsys.readouterr().err




def test_watch_terminal_exit(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("SUPERLITE_HOME", str(tmp_path / "sl"))
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    p = _memkind()
    run = p.send("Fix flaky test")
    assert factory_watch.run(p, run["id"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "status: running", "Looking at the test", "tool: run_command pytest", "status: done"]
    assert factory_watch.run(p, run["id"], after=3) == 0
    assert capsys.readouterr().out.splitlines() == ["status: done"]


def test_watch_unknown_ref_exits_1(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("SUPERLITE_HOME", str(tmp_path / "sl"))
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert factory_watch.run(_memkind(), "zzzz") == 1
    assert "lab: zzzz: not found" in capsys.readouterr().err


def test_run_plugin_exception_scrubbed(capsys, monkeypatch):
    class P:
        _token = "tok-xyz"

        def events(self, ref, after=0):
            raise RuntimeError("boom tok-xyz")
            yield

    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert factory_watch.run(P(), "r1") == 1
    err = capsys.readouterr().err
    assert "tok-xyz" not in err and "RuntimeError" in err


class _Tty(io.StringIO):
    def isatty(self):
        return True


def _agent(monkeypatch, stdout, **env):
    monkeypatch.delenv("SUPERLITE_WATCH_INLINE", raising=False)
    monkeypatch.setenv("CLAUDECODE", "1")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    monkeypatch.setattr(sys, "stdout", stdout)


_DONE = [{"id": 1, "kind": "status", "data": {"to": "done"}}]


def test_agent_without_tty_gets_the_handoff_and_no_stream(monkeypatch):
    out = io.StringIO()
    _agent(monkeypatch, out)
    prov = FakeProv(_DONE)
    prov.name = "lab"
    assert factory_watch.run(prov, "r1") == 0
    assert "factory watch lab:r1" in out.getvalue() and "status: done" not in out.getvalue()


def test_follow_inline_env_and_tty_stream(monkeypatch):
    for kw, env, out in [({"follow": True}, {}, io.StringIO()), ({}, {"SUPERLITE_WATCH_INLINE": "1"}, io.StringIO()),
                         ({}, {}, _Tty())]:
        _agent(monkeypatch, out, **env)
        assert factory_watch.run(FakeProv(_DONE), "r1", **kw) == 0
        assert out.getvalue() == "status: done\n"
