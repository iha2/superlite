import io
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/swarm/scripts"))
import cloud_stream as cs  # noqa: E402
from cloud_client import CloudError  # noqa: E402

TOKEN = "sekrit-token"


def frame(i, kind="status", data='{"run": "login_fix", "to": "running"}'):
    return f"id: {i}\nevent: {kind}\ndata: {data}\n\n".encode()


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def fake(monkeypatch):
    calls, queue = [], []

    def urlopen(req, timeout=None):
        calls.append(req)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return Resp(item)

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    return calls, queue


def run(**kw):
    sleeps = []
    out = []
    try:
        for e in cs.events("http://c", TOKEN, "login_fix", sleep=sleeps.append, **kw):
            out.append(e)
    finally:
        run.sleeps = sleeps
    return out


def test_first_item(fake):
    fake[1].append(frame(1) + frame(2, data='{"run": "login_fix", "to": "done"}'))
    out = run()
    assert out[0] == {"id": 1, "kind": "status", "data": {"run": "login_fix", "to": "running"}}
    assert fake[0][0].get_header("Authorization") == f"Bearer {TOKEN}"


def test_parse_multiline_data(fake):
    fake[1].append(b": keepalive\n\nid: 1\nevent: log\ndata: a\ndata: b\n\n"
                   + frame(2, data='{"to": "done"}'))
    out = run()
    assert out[0]["data"] == {"text": "a\nb"} and out[0]["kind"] == "log"
    assert list(cs._frames([b"id: 1\n", b"data: a\n", b"data: b\n", b"\n"]))[0]["data"] == "a\nb"


def test_reconnect_resumes(fake):
    calls, q = fake
    q.append(frame(1) + frame(2))
    q.append(frame(2) + frame(3, data='{"to": "done"}'))
    out = run()
    assert [e["id"] for e in out] == [1, 2, 3]
    assert calls[1].full_url.endswith("?after=2")
    assert calls[1].get_header("Last-event-id") == "2"
    assert run.sleeps == [0.5]


def test_backoff(fake):
    calls, q = fake
    q.extend(urllib.error.URLError("x") for _ in range(7))
    q.append(frame(1, data='{"to": "done"}'))
    run()
    assert run.sleeps == [0.5, 1, 2, 4, 8, 10, 10]


def test_gives_up_and_hides_token(fake):
    fake[1].extend(urllib.error.URLError(TOKEN) for _ in range(4))
    with pytest.raises(CloudError) as ei:
        run(max_retries=3)
    assert TOKEN not in str(ei.value)
    assert len(run.sleeps) == 3


def test_stops_on_terminal(fake):
    fake[1].append(frame(1, data='{"to": "done"}') + frame(2))
    assert [e["id"] for e in run()] == [1, 2]  # rest of the open connection is drained
    assert len(fake[0]) == 1
    assert len(fake[0]) == 1


def test_404_raises(fake):
    fake[1].append(urllib.error.HTTPError("u", 404, "nf", {}, None))
    with pytest.raises(CloudError, match="^login_fix: not found$"):
        run()
    assert run.sleeps == []


def test_route(fake):
    fake[1].append(frame(1, data='{"to": "done"}'))
    run()
    assert fake[0][0].full_url == "http://c/api/runs/login_fix/events"


def test_result_after_terminal_status_yielded_without_reconnect(fake):
    calls, queue = fake
    queue.append(frame(1) + frame(2, data='{"run": "login_fix", "to": "done"}')
                 + frame(3, kind="result", data='{"text": "ok"}'))
    out = run()
    assert [(e["id"], e["kind"]) for e in out] == [(1, "status"), (2, "status"), (3, "result")]
    assert out[2]["data"] == {"text": "ok"}
    assert len(calls) == 1


def test_terminal_without_result_does_not_reconnect(fake):
    calls, queue = fake
    queue.append(frame(1, data='{"run": "login_fix", "to": "failed"}'))
    assert len(run()) == 1
    assert len(calls) == 1
