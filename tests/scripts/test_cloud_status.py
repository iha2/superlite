import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"))
import cloud_status  # noqa: E402

CMD = 'bash "/x/superlite-cli" cloud statusline'
RUN = {"name": "login_fix", "status": "running", "step": 14, "doing": "editing api.py"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERLITE_HOME", str(tmp_path / "sl"))
    monkeypatch.setenv("CLAUDE_HOME", str(tmp_path / "ch"))
    (tmp_path / "ch").mkdir()
    return tmp_path


def test_segment():
    assert cloud_status.segment({"runs": [RUN], "notices": []}) == "login_fix running · step 14 · editing api.py"
    assert cloud_status.segment({"runs": [RUN], "notices": []}, attached="login_fix") == "ATTACHED login_fix"


def test_roundtrip_with_prev(env):
    s = env / "ch" / "settings.json"
    orig = b'{"model":"x",  "statusLine": {"type":"command","command":"echo hi"}}\n'
    s.write_bytes(orig)
    cloud_status.install(s, CMD)
    assert json.loads(s.read_text())["statusLine"]["command"] == CMD
    cloud_status.install(s, CMD)  # twice keeps the first memory
    cloud_status.restore(s)
    assert s.read_bytes() == orig


def test_roundtrip_without_prev(env):
    s = env / "ch" / "settings.json"
    orig = b'{"model": "x"}'
    s.write_bytes(orig)
    cloud_status.install(s, CMD)
    cloud_status.restore(s)
    assert s.read_bytes() == orig
    s.write_bytes(b'{"a":1}')
    cloud_status.install(s, CMD)
    d = json.loads(s.read_text())
    d["other"] = 2
    s.write_text(json.dumps(d))
    cloud_status.restore(s)
    assert "statusLine" not in json.loads(s.read_text())
    assert json.loads(s.read_text())["other"] == 2


def test_no_settings_file(env):
    s = env / "ch" / "settings.json"
    cloud_status.install(s, CMD)
    cloud_status.restore(s)
    assert not s.exists()
    cloud_status.restore(s)  # no memory: no-op


def _fake_client(monkeypatch, status=None, fail=False):
    calls = []

    class C:
        def status(self):
            if fail:
                raise RuntimeError("cloud unreachable: https://c.example")
            return status

        def me(self):
            return {"handle": "chris", "role": "member"}

        def runs(self, all=False):
            raise AssertionError("status line must never call GET /api/tasks")
    monkeypatch.setattr(cloud_status, "_make_client", lambda: C())
    ents = types.SimpleNamespace(sync=lambda *a: calls.append(a) or [])
    monkeypatch.setitem(sys.modules, "cloud_entries", ents)
    return calls


def _run_main(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: False, read=lambda: "{}"))
    assert cloud_status.main([]) == 0
    return capsys.readouterr().out.strip()


def test_wraps_prev(env, monkeypatch, capsys):
    s = env / "ch" / "settings.json"
    s.write_text(json.dumps({"statusLine": {"type": "command", "command": "echo PREV"}}))
    cloud_status.install(s, CMD)
    _fake_client(monkeypatch, {"runs": [RUN], "notices": []})
    assert _run_main(monkeypatch, capsys) == "PREV · login_fix running · step 14 · editing api.py"


def test_notice(env):
    st = {"runs": [], "notices": [{"id": 3, "task": "login_fix", "text": "login_fix from chris", "seen": 0}]}
    assert cloud_status.segment(st) == "new: login_fix from chris"
    st["notices"][0]["seen"] = 1
    assert cloud_status.segment(st) == ""


def test_rename_shown():
    r = dict(RUN, last_event={"type": "rename", "from": "login_fix", "to": "login_fix_3"})
    assert cloud_status.segment({"runs": [r], "notices": []}) == "renamed login_fix to login_fix_3"


def test_offline(env, monkeypatch, capsys):
    s = env / "ch" / "settings.json"
    s.write_text(json.dumps({"statusLine": {"type": "command", "command": "echo PREV"}}))
    cloud_status.install(s, CMD)
    _fake_client(monkeypatch, fail=True)
    assert _run_main(monkeypatch, capsys) == "PREV"


def test_entries_rate_limited(env, monkeypatch, capsys):
    calls = _fake_client(monkeypatch, {"runs": [], "notices": []})
    _run_main(monkeypatch, capsys)
    _run_main(monkeypatch, capsys)
    assert len(calls) == 1
    stamp = env / "sl" / "entries-sync"
    import os
    os.utime(stamp, (1, 1))
    _run_main(monkeypatch, capsys)
    assert len(calls) == 2


def test_sync_uses_status_runs_not_tasks(env, monkeypatch, capsys):
    calls = _fake_client(monkeypatch, {"runs": [RUN], "notices": []})
    _run_main(monkeypatch, capsys)
    assert len(calls) == 1
    synced = calls[0][0]
    assert [r["name"] for r in synced] == ["login_fix"] and synced[0]["owner"] == "chris"
