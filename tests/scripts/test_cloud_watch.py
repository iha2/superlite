import io
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"))
import cloud_watch  # noqa: E402

R = cloud_watch.render


def test_render_examples():
    assert R({"id": 4, "kind": "nudge", "data": {"text": "nudge from chris", "run": "login_fix"}}) == "nudge from chris"
    assert R({"id": 9, "kind": "rename", "data": {"from": "login_fix", "to": "login_fix_3", "why": "branch taken on origin"}}) == "renamed login_fix to login_fix_3: branch taken on origin"
    assert R({"id": 12, "kind": "given", "data": {"from": "chris", "to": "pat", "name": "login_fix"}}) == "login_fix given to pat"
    assert R({"kind": "given", "data": {"to": "pat", "name": "x", "to_email": "p@e.com"}}) == "x given to pat (p@e.com)"


def test_unknown_kind():
    assert R({"id": 1, "kind": "zzz", "data": {"a": 1}}) is None
    assert R({}) is None
    assert R({"kind": "nudge", "data": None}) is not None or True


def test_control_chars():
    out = R({"kind": "nudge", "data": {"run": "sa\x1bm", "text": "a\x1b[31mred\x07\x00\nb\x9b"}})
    assert out is not None
    assert not any(ord(c) < 32 or 0x7F <= ord(c) < 0xA0 for c in out)
    assert "\x1b" not in out


def test_input_line():
    c = mock.Mock()
    cloud_watch.handle_input(c, "r", "/approve")
    c.respond.assert_called_once_with("r", "approve")
    cloud_watch.handle_input(c, "r", "/cancel")
    c.cancel.assert_called_once_with("r")
    cloud_watch.handle_input(c, "r", "run the tests")
    c.nudge.assert_called_once_with("r", "run the tests")
    c2 = mock.Mock()
    cloud_watch.handle_input(c2, "r", "   ")
    assert not (c2.nudge.called or c2.respond.called or c2.cancel.called)


def _run(evts, capsys):
    cfg = {"url": "http://x", "token": "tok"}
    with mock.patch.object(cloud_watch.cloud_config, "load", return_value=cfg), \
         mock.patch.object(cloud_watch.cloud_stream, "events", return_value=iter(evts)), \
         mock.patch("sys.stdin", io.StringIO("")):
        rc = cloud_watch.main(["login_fix"])
    return rc, capsys.readouterr().out


def test_result_shown(capsys):
    rc, out = _run([
        {"id": 1, "kind": "status", "data": {"run": "login_fix", "to": "running"}},
        {"id": 2, "kind": "status", "data": {"run": "login_fix", "to": "done", "result": "PR opened"}},
        {"id": 3, "kind": "nudge", "data": {"by": "x", "text": "never shown"}},
    ], capsys)
    assert rc == 0
    assert "login_fix done: PR opened" in out
    assert "never shown" not in out
    assert "tok" not in out


def test_not_logged_in(capsys):
    with mock.patch.object(cloud_watch.cloud_config, "load", return_value=None):
        assert cloud_watch.main(["x"]) == 1
    assert "not logged in" in capsys.readouterr().err


def test_result_event_rendered():
    assert cloud_watch.render({"kind": "result", "data": {"text": "PR opened"}}) == "result: PR opened"
    assert cloud_watch.render({"kind": "result", "data": {}}) is None


def test_nudge_renders_sender():
    assert R({"kind": "nudge", "data": {"by": "chris", "text": "run the tests"}}) == "nudge from chris: run the tests"
    assert R({"kind": "nudge", "data": {"text": "run the tests"}}) == "run the tests"


def _ev(i, kind, **d):
    return {"id": i, "kind": kind, "data": d}


def test_result_then_waiting_keeps_watching(capsys):
    rc, out = _run([_ev(1, "result", text="the plan"), _ev(2, "status", run="login_fix", to="waiting"),
                    _ev(3, "nudge", by="chris", text="still here")], capsys)
    assert rc == 0
    assert "result: the plan" in out and "login_fix waiting" in out and "still here" in out


def test_result_then_done_exits_after_done(capsys):
    rc, out = _run([_ev(1, "result", text="ok"), _ev(2, "status", run="login_fix", to="done"),
                    _ev(3, "nudge", text="shown while waiting for a late result")], capsys)
    assert "result: ok" in out and "login_fix done" in out
    assert "shown while waiting" in out  # bare terminal status: keep reading within the stream's grace


def test_done_then_result_printed_then_exits(capsys):
    rc, out = _run([_ev(1, "status", run="login_fix", to="done"), _ev(2, "result", text="late"),
                    _ev(3, "nudge", text="never shown")], capsys)
    assert rc == 0
    assert "result: late" in out and "never shown" not in out
