"""E2E tests for `superlite-cli factory ...` against the memkind fixture (instance `lab`) and cloud_fake."""
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "skills" / "swarm" / "scripts"))
import cloud_fake  # noqa: E402
import factory_api  # noqa: E402
import factory_e2e as e2e  # noqa: E402

KEY = "mk-test"


def _lab(tmp_path, superlite=False):
    env = e2e.env(tmp_path)
    r = e2e.cli(["add", "lab", "--kind", "mem"] + (["--superlite"] if superlite else []), env)
    assert r.returncode == 0, r.stderr
    r = e2e.cli(["login", "lab", "--token", KEY], env)
    assert r.returncode == 0, r.stderr
    r = e2e.cli(["use", "lab", "--target", "t1"], env)
    assert r.returncode == 0, r.stderr
    return env


def _mem_state(env):
    return json.loads((Path(env["SUPERLITE_HOME"]) / "memkind-lab.json").read_text())


def test_list(tmp_path):
    env = e2e.env(tmp_path)
    lines = e2e.cli(["list"], env).stdout.splitlines()
    assert lines[0] == "* superlite-cloud (built-in)  logged out"
    assert lines[1].startswith("  mem  ") and lines[1].endswith("  logged out")


def test_list_broken(tmp_path):
    bad = tmp_path / "badkind"
    bad.mkdir()
    (bad / "superlite-factory.json").write_text('{"api": 1, "kind": "badkind"}')
    env = e2e.env(tmp_path)
    env["SUPERLITE_FACTORY_PATH"] += os.pathsep + str(bad)
    r = e2e.cli(["list"], env)
    assert r.returncode == 0
    assert any(ln.startswith("  badkind  ") and "error: " in ln for ln in r.stdout.splitlines())
    assert "mem" in r.stdout


def test_login_use(tmp_path):
    env = _lab(tmp_path)
    r = e2e.cli(["list"], env)
    assert "* lab  mem  t1  logged in" in r.stdout
    assert KEY not in r.stdout + r.stderr
    store = Path(env["SUPERLITE_HOME"]) / "factories.json"
    assert oct(store.stat().st_mode & 0o777) == "0o600"
    assert json.loads(store.read_text())["factories"]["lab"]["target"] == "t1"
    assert e2e.cli(["logout", "lab"], env).stdout.strip() == "lab: logged out"
    r = e2e.cli(["login", "lab", "--token", KEY], env)
    assert r.stdout.strip() == "lab: logged in"


def test_add(tmp_path):
    env = e2e.env(tmp_path)
    r = e2e.cli(["add", "web", "--kind", "mem", "--target", "web-app"], env)
    assert r.stdout.strip() == "web: added (mem)"


def test_send_and_runs(tmp_path):
    env = _lab(tmp_path)
    assert e2e.cli(["runs"], env).stdout.strip() == "no runs"
    r = e2e.cli(["send", "Fix flaky test"], env)
    assert r.returncode == 0, r.stderr
    parts = r.stdout.split()
    assert parts[1] == "queued" and len(parts[0]) == 8 and parts[2].startswith("http://127.0.0.1:")
    assert _mem_state(env)["requests"][-1] == {"verb": "send", "prompt": "Fix flaky test", "superlite": False}
    plan = tmp_path / "plan.md"
    plan.write_text("Plan body\nline2")
    assert e2e.cli(["send", "--plan", str(plan)], env).returncode == 0
    assert _mem_state(env)["requests"][-1]["prompt"] == "Plan body\nline2"
    out = e2e.cli(["runs"], env).stdout.splitlines()
    assert len(out) == 2 and out[0].endswith('queued "Fix flaky test"') and len(out[0].split()[0]) == 8


def test_watch_once_each(tmp_path):
    env = _lab(tmp_path)
    name = e2e.cli(["send", "Fix flaky test"], env).stdout.split()[0]
    r = e2e.cli(["watch", name], env, input="")
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == ["status: running", "Looking at the test", "tool: run_command pytest",
                                     "status: done"]
    assert KEY not in r.stdout + r.stderr


def test_unsupported_exit_2(tmp_path):
    env = _lab(tmp_path)
    name = e2e.cli(["send", "x"], env).stdout.split()[0]
    r = e2e.cli(["give", name, "a@b.c"], env)
    assert r.returncode == 2
    assert r.stderr.strip() == "lab: give is not supported"
    assert "Traceback" not in r.stderr


def test_superlite_not_enabled_exit2(tmp_path):
    env = _lab(tmp_path)
    r = e2e.cli(["send", "--superlite", "Fix"], env)
    assert r.returncode == 2
    assert r.stderr.strip() == "lab: superlite is not enabled"
    assert not (Path(env["SUPERLITE_HOME"]) / "memkind-lab.json").exists()


def test_superlite_send_plugin(tmp_path):
    env = _lab(tmp_path, superlite=True)
    r = e2e.cli(["send", "--superlite", "Fix"], env)
    assert r.returncode == 0, r.stderr
    req = _mem_state(env)["requests"][-1]
    assert req["prompt"] == factory_api.superlite_prompt("Fix") and req["superlite"] is True


def test_attach_detach_nudge_cancel(tmp_path):
    env = _lab(tmp_path)
    name = e2e.cli(["send", "x"], env).stdout.split()[0]
    r = e2e.cli(["attach", "lab:" + name], env)
    assert r.returncode == 0, r.stderr
    att = json.loads((Path(env["SUPERLITE_HOME"]) / "attach.json").read_text())
    assert att["run"] == name and att["factory"] == "lab"
    r = e2e.cli(["nudge", name, "please", "hurry"], env)
    assert r.returncode == 0 and r.stdout.strip() == f"{name} follow-up sent"
    assert _mem_state(env)["requests"][-1]["text"] == "please hurry"
    assert e2e.cli(["cancel", name], env).returncode == 0
    assert _mem_state(env)["runs"][0]["status"] == "cancelled"
    assert e2e.cli(["detach"], env).returncode == 0
    assert not (Path(env["SUPERLITE_HOME"]) / "attach.json").exists()


def test_no_token_leak(tmp_path):
    env = e2e.env(tmp_path)
    secret = "mk-secret-zzz"
    outs = [e2e.cli(["add", "lab", "--kind", "mem"], env),
            e2e.cli(["login", "lab", "--token", secret], env),
            e2e.cli(["use", "lab", "--target", "t1"], env),
            e2e.cli(["send", "Fix"], env),
            e2e.cli(["list"], env),
            e2e.cli(["runs"], env),
            e2e.cli(["logout", "lab"], env)]
    for r in outs:
        assert secret not in r.stdout + r.stderr
    store = Path(env["SUPERLITE_HOME"]) / "factories.json"
    for f in Path(env["SUPERLITE_HOME"]).rglob("*"):
        if f.is_file() and f != store:
            assert secret not in f.read_text(errors="ignore")
    assert secret not in store.read_text() or oct(store.stat().st_mode & 0o777) == "0o600"


def _cloud_routes():
    return {("GET", "/api/me"): {"handle": "chris", "email": "c@x.io", "role": "member"},
            ("GET", "/api/status"): {"runs": [], "notices": []},
            ("GET", "/api/tasks"): [{"id": "t1", "name": "login_fix", "status": "running", "owner": "chris",
                                     "agent": "claude", "title": "t", "url": None, "created": None}],
            ("POST", "/api/tasks"): (201, {"id": "t2", "name": "fix_flaky", "status": "queued", "owner": "chris",
                                           "agent": "claude", "title": "Fix flaky test", "url": None,
                                           "created": None})}


def _cloud_env(tmp_path, url):
    env = e2e.env(tmp_path, cloud_url=url)
    e2e.cloud_login(env, url)
    return env


def _posts(stop):
    return [q for q in stop.requests if q[0] == "POST" and q[1] == "/api/tasks"]


def test_cloud_runs_identical(tmp_path):
    url, stop = cloud_fake.serve(_cloud_routes())
    try:
        env = _cloud_env(tmp_path, url)
        mine = e2e.cli(["runs"], env)
        ref = e2e._run(["cloud", "runs"], env)
        assert (mine.returncode, mine.stdout, mine.stderr) == (ref.returncode, ref.stdout, ref.stderr)
        assert "login_fix  running" in mine.stdout
        assert "slc_test_tok" not in mine.stdout + mine.stderr
    finally:
        stop()


def test_send_cloud(tmp_path):
    url, stop = cloud_fake.serve(_cloud_routes())
    try:
        env = _cloud_env(tmp_path, url)
        r = e2e.cli(["send", "Fix flaky test"], env)
        assert r.returncode == 0, r.stderr
        assert r.stdout.split()[:2] == ["fix_flaky", "queued"]
        post = _posts(stop)[-1]
        assert post[2]["repo"] == "acme/app" and post[2]["prompt"] == "Fix flaky test"
    finally:
        stop()


def test_superlite_send_preamble(tmp_path):
    url, stop = cloud_fake.serve(_cloud_routes())
    try:
        env = _cloud_env(tmp_path, url)
        r = e2e.cli(["send", "--superlite", "Fix"], env)
        assert (r.returncode, r.stderr.strip()) == (2, "superlite-cloud: superlite is not enabled")
        r = e2e.cli(["use", "superlite-cloud", "--superlite"], env)
        assert r.stdout.strip() == "superlite-cloud: superlite on"
        assert e2e.cli(["list"], env).stdout.splitlines()[0] == "* superlite-cloud (built-in)  logged in"
        r = e2e.cli(["send", "--superlite", "Fix"], env)
        assert r.returncode == 0, r.stderr
        assert _posts(stop)[-1][2]["prompt"] == factory_api.SUPERLITE_PREAMBLE + "\n\nFix"
        e2e.cli(["send", "Plain"], env)
        assert _posts(stop)[-1][2]["prompt"] == "Plain"
        r = e2e.cli(["use", "superlite-cloud", "--no-superlite"], env)
        assert r.stdout.strip() == "superlite-cloud: superlite off"
        assert e2e.cli(["send", "--superlite", "Fix"], env).returncode == 2
    finally:
        stop()


def test_cloud_send_normalizes_title(tmp_path):
    url, stop = cloud_fake.serve(_cloud_routes())
    try:
        env = _cloud_env(tmp_path, url)
        r = e2e.cli(["send", "--title", "Live smoke-test: extra words!", "Go"], env)
        assert r.returncode == 0, r.stderr
        assert _posts(stop)[-1][2]["name"] == "live_smoke_test"
        e2e.cli(["send", "--title", "!!!", "Go"], env)
        assert "name" not in _posts(stop)[-1][2]
    finally:
        stop()


def test_error_line_no_traceback(tmp_path):
    routes = _cloud_routes()
    routes[("POST", "/api/tasks")] = (400, {"error": "name must be 1-3 words of a-z0-9 joined by _"})
    url, stop = cloud_fake.serve(routes)
    try:
        env = _cloud_env(tmp_path, url)
        r = e2e.cli(["send", "Go"], env)
        assert r.returncode == 1
        assert r.stderr.strip() == "superlite-cloud: name must be 1-3 words of a-z0-9 joined by _"
        r = e2e.cli(["cancel", "give_me"], env)
        assert r.returncode == 1
        assert r.stderr.strip() == "superlite-cloud: give_me: not found"
        assert "Traceback" not in r.stderr and "slc_test_tok" not in r.stderr
    finally:
        stop()


def test_plugin_error_line(tmp_path):
    env = _lab(tmp_path)
    r = e2e.cli(["cancel", "zzzz"], env)
    assert r.returncode == 1
    assert r.stderr.strip() == "lab: zzzz: not found"


def test_imports_standalone():
    code = ("import importlib.util, sys; "
            f"s = importlib.util.spec_from_file_location('factory_cli', {str(ROOT / 'skills/swarm/scripts/factory.py')!r}); "
            "m = importlib.util.module_from_spec(s); s.loader.exec_module(m); print('ok')")
    r = subprocess.run([sys.executable, "-I", "-c", code], cwd=str(ROOT), capture_output=True, text=True,
                       timeout=30)
    assert r.returncode == 0 and r.stdout.strip() == "ok", r.stderr
