import io
import json
import sys
import urllib.error
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"))
import cloud_client  # noqa: E402
from cloud_client import Client, CloudError  # noqa: E402

TOKEN = "sekret-token-123"
URL = "http://cloud.test:9"


class Resp:
    def __init__(self, body=b"{}", status=200):
        self._b = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.status = status

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(code, body):
    return urllib.error.HTTPError(URL, code, "x", {}, io.BytesIO(json.dumps(body).encode()))


def call(fn, resp):
    with mock.patch("urllib.request.urlopen", side_effect=[resp] if not isinstance(resp, Exception) else resp) as m:
        out = fn(Client(URL, TOKEN))
    return out, m.call_args[0][0]


def test_error_message():
    with mock.patch("urllib.request.urlopen", side_effect=http_error(404, {"error": "not found"})):
        with pytest.raises(CloudError) as e:
            Client(URL, TOKEN).cancel("login_fix")
    assert str(e.value) == "login_fix: not found"


def test_no_network():
    with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError(ConnectionRefusedError(f"{TOKEN}"))):
        with pytest.raises(CloudError) as e:
            Client(URL, TOKEN).me()
    assert str(e.value) == f"cloud unreachable: {URL}"


def test_token_not_in_errors():
    cases = [
        http_error(500, {"error": f"bad {TOKEN}"}),
        http_error(401, {}),
        urllib.error.URLError(TOKEN),
        TimeoutError(TOKEN),
        OSError(TOKEN),
    ]
    for exc in cases:
        with mock.patch("urllib.request.urlopen", side_effect=exc):
            with pytest.raises(CloudError) as e:
                Client(URL, TOKEN).nudge("a", "b")
        assert TOKEN not in str(e.value)
    with mock.patch("urllib.request.urlopen", return_value=Resp(b"not json")):
        with pytest.raises(CloudError) as e:
            Client(URL, TOKEN).me()
    assert TOKEN not in str(e.value)


def test_ref_quoted():
    _, req = call(lambda c: c.cancel("chris/login_fix"), Resp({}))
    assert req.full_url == f"{URL}/api/tasks/chris/login_fix/cancel"
    _, req = call(lambda c: c.cancel("a b"), Resp({}))
    assert "a%20b" in req.full_url


def test_respond_body():
    _, req = call(lambda c: c.respond("login_fix", "approve"), Resp({}))
    assert json.loads(req.data) == {"answer": "approve"}
    assert req.get_method() == "POST"


def test_runs_all():
    out, req = call(lambda c: c.runs(all=True), Resp([{"name": "x"}]))
    assert req.full_url == f"{URL}/api/tasks?all=1"
    assert out == [{"name": "x"}]
    _, req = call(lambda c: c.runs(), Resp([]))
    assert req.full_url == f"{URL}/api/tasks"


def test_auth_header_and_timeout():
    with mock.patch("urllib.request.urlopen", return_value=Resp({"handle": "c"})) as m:
        assert Client(URL, TOKEN, timeout=2.0).me() == {"handle": "c"}
    req = m.call_args[0][0]
    assert req.get_header("Authorization") == f"Bearer {TOKEN}"
    assert m.call_args[1]["timeout"] == 2.0


def test_nudge_expects_202():
    _, req = call(lambda c: c.nudge("r", "hi"), Resp({}, 202))
    assert json.loads(req.data) == {"text": "hi"}
    with mock.patch("urllib.request.urlopen", return_value=Resp({}, 200)):
        with pytest.raises(CloudError):
            Client(URL, TOKEN).nudge("r", "hi")


def test_give_attach_status():
    out, req = call(lambda c: c.give("login_fix", "pat@example.com"), Resp({"owner": "pat"}))
    assert out == {"owner": "pat"} and json.loads(req.data) == {"email": "pat@example.com"}
    out, _ = call(lambda c: c.attach("login_fix"), Resp({"run": "login_fix", "id": "t_1", "status": "running"}))
    assert out["id"] == "t_1"
    out, req = call(lambda c: c.status(), Resp({"runs": [], "notices": []}))
    assert req.full_url == f"{URL}/api/status"


def test_routes():
    cases = [
        (lambda c: c.me(), "GET", "/api/me"),
        (lambda c: c.status(), "GET", "/api/status"),
        (lambda c: c.runs(), "GET", "/api/tasks"),
        (lambda c: c.runs(all=True), "GET", "/api/tasks?all=1"),
        (lambda c: c.give("r", "a@b.c"), "POST", "/api/runs/r/give"),
        (lambda c: c.attach("r"), "GET", "/api/runs/r/attach"),
        (lambda c: c.nudge("r", "x"), "POST", "/api/runs/r/nudge"),
        (lambda c: c.respond("r", "x"), "POST", "/api/tasks/r/respond"),
        (lambda c: c.cancel("r"), "POST", "/api/tasks/r/cancel"),
    ]
    for fn, method, path in cases:
        _, req = call(fn, Resp({}, 202 if "nudge" in path else 200))
        assert (req.get_method(), req.full_url) == (method, URL + path)


def test_cloud_error_explicit_init():
    import inspect
    assert "__init__" in CloudError.__dict__
    assert list(inspect.signature(CloudError).parameters) == ["message"]
    assert str(CloudError("boom")) == "boom"
    assert CloudError("boom").status is None


def test_http_error_carries_status():
    with mock.patch("urllib.request.urlopen", side_effect=http_error(404, {"error": "not found"})):
        with pytest.raises(CloudError) as e:
            Client(URL, TOKEN).task("t_1")
    assert e.value.status == 404
