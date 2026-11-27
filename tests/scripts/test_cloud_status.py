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


def _run_main(monkeypatch, capsys, stdin=None):
    """The status line's stdin names its session; attachment is per session (`test-session` unless told)."""
    text = json.dumps({"session_id": "test-session"}) if stdin is None else stdin
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: False, read=lambda: text))
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


def test_second_login_from_new_install_keeps_original_prev(env):
    s = env / "ch" / "settings.json"
    orig = b'{"model":"x",  "statusLine": {"type":"command","command":"echo hi"}}\n'
    s.write_bytes(orig)
    cloud_status.install(s, CMD)
    new = 'bash "/y/superlite-cli" cloud statusline'
    cloud_status.install(s, new)  # another plugin version
    assert json.loads(s.read_text())["statusLine"]["command"] == new
    mem = json.loads((env / "ch" / "superlite-statusline-prev.json").read_text())
    assert mem["prev"]["command"] == "echo hi"
    cloud_status.restore(s)
    assert s.read_bytes() == orig


def test_second_login_new_command_after_user_edit_restores_statusline_only(env):
    s = env / "ch" / "settings.json"
    s.write_bytes(b'{"statusLine": {"type":"command","command":"echo hi"}}')
    cloud_status.install(s, CMD)
    d = json.loads(s.read_text())
    d["other"] = 2
    s.write_text(json.dumps(d))
    cloud_status.install(s, 'bash "/y/superlite-cli" cloud statusline')
    cloud_status.restore(s)
    d = json.loads(s.read_text())
    assert d["other"] == 2 and d["statusLine"]["command"] == "echo hi"


def test_admin_sync_skipped(env, monkeypatch, capsys):
    calls = _fake_client(monkeypatch, {"runs": [RUN], "notices": []})

    class A:
        def me(self):
            return {"handle": "sam", "role": "admin"}

        def status(self):
            return {"runs": [RUN], "notices": []}
    monkeypatch.setattr(cloud_status, "_make_client", lambda: A())
    _run_main(monkeypatch, capsys)
    assert calls == []  # an admin's status lists only their own runs: a sync would drop others' entries


def test_attached_lost_run_detaches(env, monkeypatch, capsys):
    from cloud_client import CloudError
    (env / "sl").mkdir()
    (env / "sl" / "attach").mkdir(parents=True, exist_ok=True)
    (env / "sl" / "attach" / "test-session.json").write_text(json.dumps({"run": "login_fix", "id": "t_9"}))

    class C:
        def status(self):
            return {"runs": [], "notices": []}

        def me(self):
            return {"handle": "chris", "role": "member"}

        def task(self, ref):
            e = CloudError("t_9: not found")
            e.status = 404
            raise e
    _fake_client(monkeypatch, {"runs": [], "notices": []})
    monkeypatch.setattr(cloud_status, "_make_client", lambda: C())
    assert _run_main(monkeypatch, capsys) == ""
    assert not (env / "sl" / "attach" / "test-session.json").exists()


def test_attached_shows_new_notice():
    st = {"runs": [], "notices": [{"id": 3, "text": "gift from pat", "seen": 0}]}
    assert cloud_status.segment(st, attached="login_fix") == "ATTACHED login_fix · new: gift from pat"
    st["notices"][0]["seen"] = 1
    assert cloud_status.segment(st, attached="login_fix") == "ATTACHED login_fix"


def _attached_client(monkeypatch, task):
    calls = _fake_client(monkeypatch, {"runs": [], "notices": []})
    seen = []

    class C:
        def status(self):
            return {"runs": [], "notices": []}

        def me(self):
            return {"handle": "chris", "role": "member"}

        def task(self, ref):
            seen.append(ref)
            return task
    monkeypatch.setattr(cloud_status, "_make_client", lambda: C())
    return seen


def test_attached_name_follows_rename(env, monkeypatch, capsys):
    (env / "sl").mkdir()
    (env / "sl" / "attach").mkdir(parents=True, exist_ok=True)
    (env / "sl" / "attach" / "test-session.json").write_text(json.dumps({"run": "login_fix", "id": "t_9"}))
    seen = _attached_client(monkeypatch, {"id": "t_9", "name": "login_fix_2", "status": "running"})
    assert _run_main(monkeypatch, capsys) == "ATTACHED login_fix_2"
    assert seen == ["t_9"]
    assert json.loads((env / "sl" / "attach" / "test-session.json").read_text())["run"] == "login_fix_2"


def test_attached_ended_falls_back_and_detaches(env, monkeypatch, capsys):
    (env / "sl").mkdir()
    (env / "sl" / "attach").mkdir(parents=True, exist_ok=True)
    (env / "sl" / "attach" / "test-session.json").write_text(json.dumps({"run": "login_fix", "id": "t_9"}))
    _attached_client(monkeypatch, {"id": "t_9", "name": "login_fix", "status": "failed"})
    assert _run_main(monkeypatch, capsys) == ""
    assert not (env / "sl" / "attach" / "test-session.json").exists()


def test_upgrade_after_user_edit_then_upgrade_again_keeps_original_prev(env):
    s = env / "ch" / "settings.json"
    s.write_bytes(b'{"statusLine": {"type":"command","command":"echo hi"}}')
    cmd_a, cmd_b, cmd_c = (f'bash "/{x}/superlite-cli" cloud statusline' for x in "abc")
    cloud_status.install(s, cmd_a)
    d = json.loads(s.read_text())
    d["other"] = 2
    s.write_text(json.dumps(d))
    cloud_status.install(s, cmd_b)
    cloud_status.install(s, cmd_c)
    mem = json.loads((env / "ch" / "superlite-statusline-prev.json").read_text())
    assert mem["prev"]["command"] == "echo hi"
    assert json.loads(s.read_text())["statusLine"]["command"] == cmd_c
    cloud_status.restore(s)
    d = json.loads(s.read_text())
    assert d["other"] == 2 and d["statusLine"]["command"] == "echo hi"


def test_status_line_shows_only_its_own_sessions_attachment(env, monkeypatch, capsys):
    (env / "sl" / "attach").mkdir(parents=True, exist_ok=True)
    (env / "sl" / "attach" / "sess-a.json").write_text(json.dumps({"run": "login_fix", "id": "t_9"}))
    (env / "sl" / "attach.json").write_text(json.dumps({"run": "legacy", "id": "t_8"}))
    _attached_client(monkeypatch, {"id": "t_9", "name": "login_fix", "status": "running"})
    assert _run_main(monkeypatch, capsys, json.dumps({"session_id": "sess-a"})) == "ATTACHED login_fix"
    assert _run_main(monkeypatch, capsys, json.dumps({"session_id": "sess-b"})) == ""  # B shows none, A's file stays
    assert (env / "sl" / "attach" / "sess-a.json").exists()
    # no session id on stdin: none, even though the env var names a session with an attachment
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-a")
    assert _run_main(monkeypatch, capsys, "{}") == ""
    assert _run_main(monkeypatch, capsys, "not json") == ""
    assert (env / "sl" / "attach" / "sess-a.json").exists()
