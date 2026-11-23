"""Hook routing across factories, e2e: subprocess `superlite-cli factory_hook` against cloud_fake and memkind `lab`."""
import json
import os
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cloud_fake  # noqa: E402
import factory_e2e as e2e  # noqa: E402

RID = "1a2b3c4d5e6f"
R8 = RID[:8]
LAB = {"kind": "mem", "token": "mem-secret-token-value", "target": None, "target_name": "lab", "superlite": False}


def write_store(env, factories, active="superlite-cloud"):
    p = Path(env["SUPERLITE_HOME"]) / "factories.json"
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"active": active, "factories": factories}, f)


@pytest.fixture
def world(tmp_path):
    stops = []

    def build(cloud=True, routes=None, store=True, status="running"):
        curl, cstop = cloud_fake.serve(routes or {})
        stops.append(cstop)
        env = e2e.env(tmp_path, curl)
        home = Path(env["SUPERLITE_HOME"])
        if store:
            write_store(env, {"lab": LAB})
            (home / "memkind-lab.json").write_text(
                json.dumps({"runs": [{"id": RID, "status": status, "title": "t"}], "requests": []}), encoding="utf-8")
        if cloud:
            e2e.cloud_login(env, curl)
        return types.SimpleNamespace(env=env, cstop=cstop, home=home)
    yield build
    for s in stops:
        s()


def att(w):
    p = w.home / "attach.json"
    return json.loads(p.read_text()) if p.exists() else None


def put_att(w, **d):
    (w.home / "attach.json").write_text(json.dumps(d), encoding="utf-8")


def mem_reqs(w):
    return json.loads((w.home / "memkind-lab.json").read_text()).get("requests", [])


def test_cloud_only_identical(world):
    w = world(store=False, routes={("GET", "/api/tasks/run1"): {"status": "running", "name": "run1"},
                                   ("POST", "/api/runs/run1/nudge"): (202, {})})
    for line in ("also add a test", "\\literal text", ">> hello", "/superlite:detach"):
        put_att(w, run="run1", id="run1")
        via_f = e2e.hook(line, w.env, via="factory")
        put_att(w, run="run1", id="run1")
        via_c = e2e.hook(line, w.env, via="cloud")
        assert via_f == via_c
    put_att(w, run="run1", id="run1")
    out = e2e.hook("also add a test", w.env)
    assert (out["decision"], out["reason"]) == ("block", "→ run1")
    assert att(w) == {"run": "run1", "id": "run1"}


def test_cloud_only_identical_no_login(world):
    w = world(cloud=False, store=False)
    for line in (">> x", "/superlite:attach r", "/superlite:runs", "a plain line", "\\x", "/superlite:detach"):
        assert e2e.hook(line, w.env, via="factory") == e2e.hook(line, w.env, via="cloud"), line
    for line in ("\\x", "a plain line", ">> x"):
        put_att(w, run="r", id="r")
        assert e2e.hook(line, w.env, via="factory") == e2e.hook(line, w.env, via="cloud"), line


@pytest.mark.parametrize("routes", [{}, {("GET", "/api/runs/sl_run/attach"): {}}])
def test_bare_attach_unknown_run_blocked(world, routes):
    w = world(store=False, routes=routes)
    out = e2e.hook("/superlite:attach sl_run", w.env)
    assert out["decision"] == "block" and "sl_run" in out["reason"]
    assert att(w) is None


def test_attach_by_address(world):
    w = world()
    out = e2e.hook("/superlite:attach lab:" + R8, w.env)
    assert out["reason"] == "→ lab (mem) %s: attached" % R8
    assert att(w) == {"run": R8, "id": RID, "factory": "lab"}
    assert w.cstop.requests == []


def test_plain_line_followup(world):
    w = world()
    put_att(w, run=R8, id=RID, factory="lab")
    out = e2e.hook("also add a regression test", w.env)
    assert out["reason"] == "→ lab (mem) %s: follow-up sent" % R8
    assert mem_reqs(w)[-1] == {"verb": "nudge", "id": RID, "text": "also add a regression test"}
    assert w.cstop.requests == []
    assert att(w)["factory"] == "lab"


def test_cross_factory_nudge_keeps_attachment(world):
    w = world(routes={("POST", "/api/runs/chris/login_fix/nudge"): (202, {})})
    put_att(w, run=R8, id=RID, factory="lab")
    out = e2e.hook(">> superlite-cloud:chris/login_fix check the tests", w.env)
    assert out["reason"] == "→ superlite-cloud (superlite-cloud) chris/login_fix: nudge sent"
    assert w.cstop.requests[-1] == ("POST", "/api/runs/chris/login_fix/nudge", {"text": "check the tests"})
    assert att(w) == {"run": R8, "id": RID, "factory": "lab"}
    assert mem_reqs(w) == []


def test_attach_unknown_run_blocked(world):
    w = world(routes={("GET", "/api/runs/sl_run/attach"): {}})
    out = e2e.hook("/superlite:attach lab:nope", w.env)
    assert out["decision"] == "block" and "not found" in out["reason"]
    assert att(w) is None
    out = e2e.hook("/superlite:attach superlite-cloud:sl_run", w.env)
    assert out["reason"] == "not attached: sl_run: not found"
    assert att(w) is None


def test_auto_detach_on_404(world):
    w = world()
    put_att(w, run=R8, id=RID, factory="lab")
    (w.home / "memkind-lab.json").write_text(json.dumps({"runs": [], "requests": []}), encoding="utf-8")
    out = e2e.hook("hello", w.env)
    assert out["hookSpecificOutput"]["additionalContext"] == "%s is no longer available to you; detached" % R8
    assert att(w) is None
    assert e2e.hook("hello", w.env) is None


def test_backslash_passthrough(world):
    w = world()
    put_att(w, run=R8, id=RID, factory="lab")
    out = e2e.hook("\\add a test myself", w.env)
    assert out["hookSpecificOutput"]["additionalContext"].endswith("add a test myself")
    assert mem_reqs(w) == [] and w.cstop.requests == []
    assert att(w)["factory"] == "lab"


def test_dashed_entry_labelled(world):
    w = world()
    out = e2e.hook("/superlite-attach-lab-" + R8, w.env)
    assert out["reason"] == "→ lab (mem) %s: attached" % R8
    assert att(w)["factory"] == "lab"


def test_dashed_entry_single_login_unlabelled(world):
    w = world(cloud=False)
    out = e2e.hook("/superlite-attach-lab-" + R8, w.env)
    assert out["reason"] == "attached " + R8


def test_cancel_and_detach(world):
    w = world()
    put_att(w, run=R8, id=RID, factory="lab")
    assert e2e.hook("/cancel", w.env)["reason"] == "cancelled " + R8
    assert mem_reqs(w)[-1] == {"verb": "cancel", "id": RID}
    assert e2e.hook("/superlite:detach", w.env)["reason"] == "detached"
    assert att(w) is None


def test_runs_slash(world):
    w = world()
    out = e2e.hook("/superlite:runs lab", w.env)
    assert R8 in out["reason"]
    assert w.cstop.requests == []


def test_give_unsupported(world):
    w = world()
    out = e2e.hook("/superlite:give lab:%s a@b.co" % R8, w.env)
    assert out["reason"] == "lab: give is not supported"
    assert mem_reqs(w) == []


def test_bare_ref_goes_to_attached_factory(world):
    w = world()
    put_att(w, run=R8, id=RID, factory="lab")
    out = e2e.hook(">>%s hello there" % R8, w.env)
    assert out["reason"] == "→ lab (mem) %s: follow-up sent" % R8
    assert w.cstop.requests == []


def test_fail_open_and_no_token_leak(world):
    w = world()
    (w.home / "factories.json").write_text("{not json", encoding="utf-8")
    r = e2e._run(["factory_hook"], w.env, "not json")
    assert r.returncode == 0 and r.stdout.strip() == ""
    r = e2e._run(["factory_hook"], w.env, json.dumps({"prompt": "/superlite:attach lab:x"}))
    assert r.returncode == 0 and LAB["token"] not in r.stdout + r.stderr


def test_broken_attached_factory_blocks_not_passes(world):
    w = world()
    put_att(w, run="x", id=RID, factory="lab")
    s = json.loads((w.home / "factories.json").read_text())
    s["factories"]["lab"]["kind"] = "nokind"
    (w.home / "factories.json").write_text(json.dumps(s), encoding="utf-8")
    r = e2e._run(["factory_hook"], w.env, json.dumps({"prompt": "hello there"}))
    assert r.returncode == 0 and '"block"' in r.stdout and "lab: unavailable" in r.stdout
    assert LAB["token"] not in r.stdout + r.stderr
