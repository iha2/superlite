"""T2: cloud provider (cloud_factory)."""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"))
import cloud_client  # noqa: E402
import cloud_config  # noqa: E402
import cloud_factory  # noqa: E402
import cloud_fake  # noqa: E402
import factory_api  # noqa: E402

TOK = "slc_test_tok_0123456789abcdef"


def _login(url):
    cloud_config.save({"url": url, "token": TOK})


RUN = {"id": "r1", "name": "fix-it", "status": "queued", "owner": "me", "agent": "claude", "title": "t",
       "url": "http://x/r1", "created": "now"}


def _routes():
    return {("GET", "/api/me"): {"handle": "me", "role": "admin"},
            ("GET", "/api/status"): {"runs": [], "notices": []},
            ("GET", "/api/tasks"): [RUN],
            ("GET", "/api/tasks/fix-it"): RUN,
            ("POST", "/api/tasks"): (201, RUN),
            ("POST", "/api/runs/fix-it/nudge"): (202, {}),
            ("POST", "/api/tasks/fix-it/cancel"): {},
            ("GET", "/api/runs/fix-it/attach"): {"run": "fix-it", "id": "r1"}}


def test_not_logged_in_raises():
    with pytest.raises(factory_api.FactoryError, match="^superlite-cloud: not logged in$"):
        cloud_factory.create({"name": "superlite-cloud"})


def test_create_attrs():
    url, stop = cloud_fake.serve(_routes())
    _login(url)
    p = cloud_factory.create({"name": "superlite-cloud"})
    assert isinstance(p, cloud_client.Client)
    assert p.name == "superlite-cloud" and p.nudge_word == "nudge" and p._token == TOK
    assert {"give", "send", "events", "nudge", "respond", "cancel", "attach", "runs", "task", "me",
            "status"} <= set(p.caps)
    assert TOK not in repr(p)


def test_same_as_client():
    url, stop = cloud_fake.serve(_routes())
    _login(url)
    p = cloud_factory.create({"name": "superlite-cloud"})
    c = cloud_client.Client(url, TOK)
    for obj in (p, c):
        obj.me(); obj.status(); obj.runs(); obj.runs(all=True); obj.task("fix-it")
        obj.nudge("fix-it", "go"); obj.cancel("fix-it"); obj.attach("fix-it")
    reqs = stop.requests
    half = len(reqs) // 2
    assert half == 8 and reqs[:half] == reqs[half:]


def test_cloud_send_body():
    url, stop = cloud_fake.serve(_routes())
    _login(url)
    p = cloud_factory.create({"name": "superlite-cloud"})
    run = p.send("Fix flaky test", base="main", repo="acme/app", title="flaky")
    assert stop.requests[-1] == ("POST", "/api/tasks", {"repo": "acme/app", "prompt": "Fix flaky test",
                                                        "agent": "claude", "base": "main", "name": "flaky"})
    assert run["id"] == "r1" and run["name"] == "fix-it" and run["status"] == "queued"
    assert set(run) >= {"id", "name", "status", "owner", "agent", "title", "url", "created"}
    p.send("Plain", repo="acme/app")
    assert stop.requests[-1][2] == {"repo": "acme/app", "prompt": "Plain", "agent": "claude"}


def test_send_uses_origin(tmp_path, monkeypatch):
    import subprocess
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "origin", "git@github.com:acme/app.git"], check=True)
    monkeypatch.chdir(tmp_path)
    url, stop = cloud_fake.serve(_routes())
    _login(url)
    cloud_factory.create({"name": "superlite-cloud"}).send("x")
    assert stop.requests[-1][2]["repo"] == "acme/app"


def test_origin_repo(tmp_path):
    import subprocess
    assert cloud_factory.origin_repo(str(tmp_path)) is None
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert cloud_factory.origin_repo(str(tmp_path)) is None
    for remote in ("git@github.com:acme/app.git", "https://github.com/acme/app.git", "https://github.com/acme/app"):
        subprocess.run(["git", "-C", str(tmp_path), "remote", "remove", "origin"], capture_output=True)
        subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "origin", remote], check=True)
        assert cloud_factory.origin_repo(str(tmp_path)) == "acme/app"


def test_send_no_origin(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    url, stop = cloud_fake.serve(_routes())
    _login(url)
    with pytest.raises(factory_api.FactoryError, match="superlite-cloud: no origin repo"):
        cloud_factory.create({"name": "superlite-cloud"}).send("x")
    assert not any(r[0] == "POST" for r in stop.requests)


def test_events_via_stream():
    evs = [{"id": 1, "kind": "status", "data": {"to": "running"}},
           {"id": 2, "kind": "status", "data": {"to": "done", "result": "ok"}}]
    url, stop = cloud_fake.serve({("GET", "/api/runs/fix-it/events"): evs})
    _login(url)
    p = cloud_factory.create({"name": "superlite-cloud"})
    assert [e["id"] for e in p.events("fix-it")] == [1, 2]
    assert [e["id"] for e in p.events("fix-it", after=1)] == [2]


def test_superlite_preamble_once():
    url, stop = cloud_fake.serve(_routes())
    try:
        _login(url)
        p = cloud_factory.create({"name": "superlite-cloud"})
        p.send("Fix", repo="a/b", superlite=True)
        p.send(factory_api.superlite_prompt("Fix"), repo="a/b", superlite=True)
        p.send("Plain", repo="a/b")
        posts = [q[2]["prompt"] for q in stop.requests if q[0] == "POST" and q[1] == "/api/tasks"]
        assert posts == [factory_api.superlite_prompt("Fix")] * 2 + ["Plain"]
    finally:
        stop()


def test_server_name():
    assert cloud_factory.server_name("SL Live Two!") == "sl_live_two"
    assert cloud_factory.server_name("one two three four") == "one_two_three"
    assert cloud_factory.server_name("!!") is None
    assert cloud_factory.server_name(None) is None


def test_server_name_in_body():
    url, stop = cloud_fake.serve(_routes())
    _login(url)
    p = cloud_factory.create({"name": "superlite-cloud"})
    p.send("x", repo="a/b", title="SL Live Two!")
    assert stop.requests[-1][2]["name"] == "sl_live_two"
    p.send("x", repo="a/b", title="!!")
    assert "name" not in stop.requests[-1][2]
