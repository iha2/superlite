"""Hook routing across factories, e2e: subprocess `superlite-cli factory_hook` against cloud_fake and memkind `lab`."""
import json
import os
import shutil
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


def test_active_logged_out_plugin_does_not_fall_back_to_cloud(world):
    w = world(routes={("GET", "/api/runs"): {"runs": []}})
    s = json.loads((w.home / "factories.json").read_text())
    s["active"] = "lab"
    del s["factories"]["lab"]["token"]
    (w.home / "factories.json").write_text(json.dumps(s), encoding="utf-8")
    for line in ("/superlite:runs", ">> hello", "/superlite:attach lab:x", "/superlite:attach lab-x"):
        out = e2e.hook(line, w.env)
        assert out and "lab: unavailable" in json.dumps(out), line
    s["active"] = "superlite-cloud"  # the cloud stays the target when it is the active factory
    (w.home / "factories.json").write_text(json.dumps(s), encoding="utf-8")
    assert "lab: unavailable" not in json.dumps(e2e.hook("/superlite:runs", w.env))


LEAK = "leaky-secret-token-value"


def leaky_world(world, tmp_path, **kw):
    """A world whose attached factory `bad` (kind `leaky`) raises a FactoryError carrying its token from create()."""
    root = tmp_path / "plugins" / "leaky"
    (root / "factory").mkdir(parents=True)
    (root / "superlite-factory.json").write_text(json.dumps(
        {"api": 1, "kind": "leaky", "label": "Leaky", "module": "factory/leaky.py", "entry": "create"}), encoding="utf-8")
    (root / "factory" / "leaky.py").write_text(
        "from factory_api import FactoryError\n\n\ndef create(config):\n"
        "    raise FactoryError('create failed for ' + config['token'])\n", encoding="utf-8")
    w = world(**kw)
    w.env["SUPERLITE_FACTORY_PATH"] += os.pathsep + str(root)
    write_store(w.env, {"lab": LAB, "bad": dict(LAB, kind="leaky", token=LEAK)})
    put_att(w, run="x", id=RID, factory="bad")
    return w


def test_provider_creation_error_shows_type_not_token(world, tmp_path):
    w = leaky_world(world, tmp_path)
    r = e2e._run(["factory_hook"], w.env, json.dumps({"prompt": "hello there"}))
    out = json.loads(r.stdout)
    assert out["decision"] == "block" and "bad: unavailable (FactoryError)" in out["reason"]
    assert LEAK not in r.stdout + r.stderr


def test_local_escapes_work_with_broken_attached_provider(world, tmp_path):
    w = leaky_world(world, tmp_path)
    out = e2e.hook("\\add a test myself", w.env)
    assert out["hookSpecificOutput"]["additionalContext"].endswith("add a test myself")
    assert att(w)["factory"] == "bad"
    out = e2e.hook("/superlite:detach", w.env)
    assert out["reason"] == "detached" and att(w) is None


def test_cloud_only_explicit_address_attach(world):
    w = world(store=False, routes={("GET", "/api/runs/run1/attach"): {"run": "run1", "id": "id1"}})
    out = e2e.hook("/superlite:attach superlite-cloud:run1", w.env)
    assert out["reason"] == "attached run1"
    assert att(w) == {"run": "run1", "id": "id1"}
    assert ("GET", "/api/runs/run1/attach", None) in w.cstop.requests


def test_cloud_only_explicit_address_nudge(world):
    w = world(store=False, routes={("GET", "/api/tasks/run1"): {"status": "running", "name": "run1"},
                                   ("POST", "/api/runs/run2/nudge"): (202, {})})
    put_att(w, run="run1", id="run1")
    out = e2e.hook(">> superlite-cloud:run2 hello", w.env)
    assert out["reason"] == "→ run2"
    assert w.cstop.requests[-1] == ("POST", "/api/runs/run2/nudge", {"text": "hello"})
    assert att(w) == {"run": "run1", "id": "run1"}


def test_plugin_watch_hands_off_without_blocking(world):
    w = world()
    env = {**w.env, "TMUX": "/tmp/tmux-1/default,1,0"}
    env.pop("HERDR_ENV", None)
    out = e2e.hook("/superlite:watch lab:%s" % R8, env)
    assert "decision" not in out
    text = out["hookSpecificOutput"]["additionalContext"]
    assert "factory watch lab:%s" % R8 in text and "tmux split-window -h" in text
    assert "mem-secret-token-value" not in text
    assert w.cstop.requests == [] and mem_reqs(w) == []


def _fault_plugin(w, tmp_path, body):
    """A copy of memkind whose nudge runs `body` (a statement), pointed at by SUPERLITE_FACTORY_PATH."""
    dst = Path(tmp_path) / "faultkind"
    shutil.copytree(e2e.MEMKIND, dst)
    mod = dst / "factory" / "memkind.py"
    mod.write_text(mod.read_text(encoding="utf-8") + "\n_n = MemProvider.nudge\n\n\ndef _bad(self, ref, text):\n    "
                   + body + "\n\n\nMemProvider.nudge = _bad\n", encoding="utf-8")
    w.env["SUPERLITE_FACTORY_PATH"] = str(dst)


def test_cloud_entry_named_like_factory_prefix_stays_cloud(world, tmp_path):
    """A cloud run `lab-fix` has the entry /superlite-attach-lab-fix: it is not run `fix` of factory `lab`."""
    w = world(routes={("GET", "/api/runs/lab-fix/attach"): {"run": "lab-fix", "id": "c1", "status": "running"},
                      ("GET", "/api/tasks/c1"): {"status": "running", "name": "lab-fix"}})
    d = Path(w.env["CLAUDE_HOME"]) / "skills" / "superlite-attach-lab-fix"
    d.mkdir(parents=True)
    (d / ".superlite-entry").write_text("")
    out = e2e.hook("/superlite-attach-lab-fix", w.env)
    assert "lab-fix" in out["reason"] and "(mem)" not in out["reason"]
    assert ("GET", "/api/runs/lab-fix/attach", None) in w.cstop.requests
    assert att(w) == {"run": "lab-fix", "id": "c1"}


def test_bare_dashed_cloud_entry_ignores_attached_factory(world):
    """A cloud entry carries no factory prefix: while attached to `lab` it still addresses superlite-cloud."""
    w = world(routes={("GET", "/api/runs/fix1/attach"): {"run": "fix1", "id": "c1", "status": "running"},
                      ("GET", "/api/tasks/c1"): {"status": "running", "name": "fix1"}})
    put_att(w, run=R8, id=RID, factory="lab")
    out = e2e.hook("/superlite-attach-fix1", w.env)
    assert ("GET", "/api/runs/fix1/attach", None) in w.cstop.requests
    assert mem_reqs(w) == []
    assert att(w) == {"run": "fix1", "id": "c1"} and "(mem)" not in out["reason"]


def test_uncaught_plugin_error_blocks_attached_line(world, tmp_path):
    w = world()
    _fault_plugin(w, tmp_path, "raise StopIteration")
    put_att(w, run=R8, id=RID, factory="lab")
    out = e2e.hook("also add a test", w.env)
    assert out["decision"] == "block" and "pass" not in out and "StopIteration" in out["reason"]
    assert LAB["token"] not in out["reason"]
    assert w.cstop.requests == []
    out = e2e.hook(">> hi there", w.env)
    assert out["decision"] == "block" and "StopIteration" in out["reason"]
    assert e2e.hook("/some-other-command", w.env) is None  # unrelated: fails open, not a run line


def test_keyboard_interrupt_in_plugin_reaches_the_outer_guard(world, tmp_path):
    """Not a PLUGIN_ERRORS member: only _guarded_main catches it, and the line is still blocked."""
    w = world()
    _fault_plugin(w, tmp_path, "raise KeyboardInterrupt")
    put_att(w, run=R8, id=RID, factory="lab")
    out = e2e.hook("hello", w.env)
    assert out["decision"] == "block" and "KeyboardInterrupt" in out["reason"]
    assert LAB["token"] not in out["reason"] and w.cstop.requests == []


def test_hung_plugin_blocks_attached_line(world, tmp_path):
    w = world()
    _fault_plugin(w, tmp_path, "import time; time.sleep(60)")
    w.env["SUPERLITE_HOOK_TIMEOUT"] = "2"
    put_att(w, run=R8, id=RID, factory="lab")
    out = e2e.hook("hello", w.env)
    assert out["decision"] == "block" and "timed out" in out["reason"]
    assert "delivery not confirmed" in out["reason"] and "did not reach" not in out["reason"]
    assert w.cstop.requests == []


def test_failed_send_after_rename_keeps_factory_key(world, tmp_path):
    """refresh_attached rewrites attach.json without `factory`; a failing send must not leave it unkeyed."""
    w = world()
    _fault_plugin(w, tmp_path, "raise StopIteration")
    put_att(w, run="old-name", id=RID, factory="lab")
    e2e.hook("hello", w.env)
    assert att(w)["factory"] == "lab"


def test_registry_failure_with_a_plugin_attachment_never_guesses_cloud(world):
    w = world()
    put_att(w, run=R8, id=RID, factory="lab")
    (w.home / "factories.json").write_text("{not json", encoding="utf-8")
    for line in ("hello there", ">> hi", "/cancel"):
        out = e2e.hook(line, w.env)
        assert out["decision"] == "block" and "lab: unavailable" in out["reason"], line
    assert w.cstop.requests == [] and att(w)["factory"] == "lab"
    assert e2e.hook("\\add a test myself", w.env)["hookSpecificOutput"]["additionalContext"].endswith("myself")
    assert att(w)["factory"] == "lab"
    assert e2e.hook("/superlite:detach", w.env)["reason"] == "detached" and att(w) is None


def test_registry_failure_with_a_cloud_attachment_keeps_the_cloud_path(world):
    w = world(routes={("GET", "/api/tasks/" + RID): {"status": "running", "name": R8},
                      ("POST", "/api/runs/" + RID + "/nudge"): {"ok": True}})
    put_att(w, run=R8, id=RID)
    (w.home / "factories.json").write_text("{not json", encoding="utf-8")
    e2e.hook("hello there", w.env)
    assert any(r[1].startswith("/api/") and "nudge" in r[1] or r[1] == "/api/tasks/" + RID for r in w.cstop.requests)


def test_attached_cloud_not_logged_in_blocks_plain_line(world):
    w = world(cloud=False, store=False)
    put_att(w, run="r", id="r")
    out = e2e.hook("a plain line", w.env)
    assert out["decision"] == "block" and "not logged in" in out["reason"]
    assert e2e.hook("/some-other-command", w.env) is None
    assert e2e.hook("\\x", w.env) is None


def test_gone_factory_blocks_attached_line(world):
    w = world(store=False)
    put_att(w, run="x", id="x", factory="vanished")
    out = e2e.hook("hello", w.env)
    assert out["decision"] == "block" and "vanished" in out["reason"]
    assert w.cstop.requests == []
    assert e2e.hook("/superlite:detach", w.env)["reason"] == "detached"


def test_plugin_watch_without_a_run_shows_usage(world):
    w = world()
    put_att(w, run=R8, id=RID, factory="lab")
    out = e2e.hook("/superlite:watch", w.env)
    assert out["decision"] == "block" and "usage: /superlite:watch <run>" in out["reason"]


def test_cloud_give_keeps_an_attachment_on_another_factory(world):
    w = world(routes={("POST", "/api/runs/chris/gift/give"): {"id": RID, "name": "gift"}})
    put_att(w, run=R8, id=RID, factory="lab")
    out = e2e.hook("/superlite:give superlite-cloud:chris/gift a@b.co", w.env)
    assert "gift given to" in out["reason"]
    assert att(w) == {"run": R8, "id": RID, "factory": "lab"}
