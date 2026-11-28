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
    assert os.name == "nt" or os.name == "nt" or oct(store.stat().st_mode & 0o777) == "0o600"
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
    att = json.loads((Path(env["SUPERLITE_HOME"]) / "attach" / "test-session.json").read_text())
    assert att["run"] == name and att["factory"] == "lab"
    r = e2e.cli(["nudge", name, "please", "hurry"], env)
    assert r.returncode == 0 and r.stdout.strip() == f"{name} follow-up sent"
    assert _mem_state(env)["requests"][-1]["text"] == "please hurry"
    assert e2e.cli(["cancel", name], env).returncode == 0
    assert _mem_state(env)["runs"][0]["status"] == "cancelled"
    assert e2e.cli(["detach"], env).returncode == 0
    assert not (Path(env["SUPERLITE_HOME"]) / "attach" / "test-session.json").exists()


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


# ---- review fixes: redaction, env-only logins, attachments, status-line wrapper --------------------------

_LEAKY = '''
import sys

from factory_api import FactoryError


class OwnError(Exception):
    pass


class P:
    caps = frozenset({"me", "runs", "cancel"})
    nudge_word = "nudge"

    def __init__(self, c):
        self.name, self.t = c["name"], c.get("token") or ""

    def me(self):
        if self.t.startswith("bad-"):
            raise FactoryError("rejected " + self.t)
        if self.t.startswith("boom-"):
            raise OwnError("boom with " + self.t)
        return {"handle": "h", "role": "member"}

    def resolve_target(self, q):
        return {"uid": q, "name": q}

    def runs(self, all=False):
        raise FactoryError("runs failed with " + self.t)

    def cancel(self, ref):
        if ref == "quit":
            sys.exit("cancel quit with " + self.t)
        raise RuntimeError("cancel failed with " + self.t)


def create(c):
    return P(c)
'''


def _leaky_env(tmp_path):
    root = tmp_path / "leakykind"
    (root / "factory").mkdir(parents=True)
    (root / "superlite-factory.json").write_text(json.dumps(
        {"api": 1, "kind": "leaky", "module": "factory/leaky.py", "entry": "create", "token_env": "LEAKY_KEY"}))
    (root / "factory" / "leaky.py").write_text(_LEAKY)
    env = e2e.env(tmp_path)
    env["SUPERLITE_FACTORY_PATH"] += os.pathsep + str(root)
    assert e2e.cli(["add", "lk", "--kind", "leaky"], env).returncode == 0
    return env


def test_env_only_login_works_and_is_redacted(tmp_path):
    env = _leaky_env(tmp_path)
    env["LEAKY_KEY"] = "env-secret-77"
    assert "lk  leaky  -  logged in" in e2e.cli(["list"], env).stdout
    r = e2e.cli(["use", "lk", "--target", "t1"], env)
    assert (r.returncode, r.stdout.strip()) == (0, "lk: t1 (t1)"), r.stderr
    r = e2e.cli(["runs"], env)
    assert r.returncode == 1 and "env-secret-77" not in r.stdout + r.stderr and r.stderr.startswith("lk:")
    r = e2e.cli(["cancel", "zzz"], env)  # a plugin's ordinary exception: the type only, never a traceback
    assert (r.returncode, r.stderr.strip()) == (1, "lk: RuntimeError")
    r = e2e.cli(["cancel", "quit"], env)  # sys.exit(<message>) from a plugin: the type only, the message never printed
    assert (r.returncode, r.stderr.strip()) == (1, "lk: SystemExit")
    assert "env-secret-77" not in r.stdout + r.stderr


def test_failed_clipboard_login_never_prints_the_token(tmp_path):
    if os.name == "nt":
        return
    env = _leaky_env(tmp_path)
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    for tool in ("pbpaste", "wl-paste", "xclip"):  # the first one found wins, so shadow all of them
        clip = bindir / tool
        clip.write_text("#!/bin/sh\nprintf 'bad-clip-token'\n")
        clip.chmod(0o755)
    env["PATH"] = str(bindir) + os.pathsep + env["PATH"]
    r = e2e.cli(["login", "lk"], env)
    assert r.returncode == 1 and r.stderr.strip() == "lk: rejected ***"
    assert "bad-clip-token" not in r.stdout + r.stderr
    assert not json.loads((Path(env["SUPERLITE_HOME"]) / "factories.json").read_text())["factories"]["lk"].get("token")


def test_missing_plan_file_is_an_error_line(tmp_path):
    env = _lab(tmp_path)
    r = e2e.cli(["send", "--plan", str(tmp_path / "nope.md")], env)
    assert (r.returncode, r.stderr.strip()) == (1, "lab: FileNotFoundError")


def _settings(env):
    return Path(env["CLAUDE_HOME"]) / "settings.json"


def test_plugin_login_installs_wrapper_and_logout_restores(tmp_path):
    env = _lab(tmp_path)
    assert "cloud statusline" in _settings(env).read_text()
    name = e2e.cli(["send", "x"], env).stdout.split()[0]
    assert e2e.cli(["attach", "lab:" + name], env).returncode == 0
    assert e2e.cli(["logout", "lab"], env).returncode == 0
    assert not _settings(env).exists()
    assert not (Path(env["SUPERLITE_HOME"]) / "attach" / "test-session.json").exists()


def test_last_logout_removes_generated_entries_with_the_wrapper(tmp_path):
    env = _lab(tmp_path)
    skills = Path(env["CLAUDE_HOME"]) / "skills"
    mine = skills / "superlite-attach-lab-run1"
    theirs = skills / "superlite-attach-mine"
    for d in (mine, theirs):
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("x")
    (mine / ".superlite-factory-entry").write_text("")
    assert e2e.cli(["logout", "lab"], env).returncode == 0
    assert not _settings(env).exists()
    assert not mine.exists() and theirs.exists()


def test_cloud_logout_keeps_plugin_attachment_and_wrapper(tmp_path):
    env = _lab(tmp_path)
    name = e2e.cli(["send", "x"], env).stdout.split()[0]
    assert e2e.cli(["attach", "lab:" + name], env).returncode == 0
    e2e.cloud_login(env, "http://127.0.0.1:9")
    r = e2e.cli(["logout", "superlite-cloud"], env)
    assert r.returncode == 0, r.stderr
    att = json.loads((Path(env["SUPERLITE_HOME"]) / "attach" / "test-session.json").read_text())
    assert att["run"] == name and att["factory"] == "lab"
    assert "cloud statusline" in _settings(env).read_text()


def test_cloud_attach_missing_run_leaves_nothing(tmp_path):
    url, stop = cloud_fake.serve({("GET", "/api/me"): {"handle": "chris", "email": "c@x.io", "role": "member"},
                                  ("GET", "/api/runs/sl_run/attach"): {}})
    try:
        env = _cloud_env(tmp_path, url)
        r = e2e.cli(["attach", "superlite-cloud:sl_run"], env)
        assert r.returncode == 1 and "not attached: sl_run: not found" in r.stderr
        assert "attached sl_run" not in r.stdout
        assert not (Path(env["SUPERLITE_HOME"]) / "attach" / "test-session.json").exists()
    finally:
        stop()


def test_cloud_attach_known_run(tmp_path):
    url, stop = cloud_fake.serve({("GET", "/api/runs/login_fix/attach"):
                                  {"run": "login_fix", "id": "t1", "status": "running"}})
    try:
        env = _cloud_env(tmp_path, url)
        r = e2e.cli(["attach", "superlite-cloud:login_fix"], env)
        assert (r.returncode, r.stdout.strip()) == (0, "attached login_fix"), r.stderr
        assert json.loads((Path(env["SUPERLITE_HOME"]) / "attach" / "test-session.json").read_text())["id"] == "t1"
    finally:
        stop()


def test_failed_relogin_keeps_the_previous_credential(tmp_path):
    env = _leaky_env(tmp_path)
    assert e2e.cli(["login", "lk", "--token", "good-1"], env).returncode == 0
    r = e2e.cli(["login", "lk", "--token", "bad-2"], env)
    assert r.returncode == 1 and "bad-2" not in r.stdout + r.stderr
    assert _lk_token(env) == "good-1"
    r = e2e.cli(["login", "lk", "--token", "boom-3"], env)  # a plugin's own exception, not a FactoryError
    assert (r.returncode, r.stderr.strip()) == (1, "lk: OwnError")
    assert _lk_token(env) == "good-1"


def _lk_token(env):
    store = json.loads((Path(env["SUPERLITE_HOME"]) / "factories.json").read_text())
    return store["factories"]["lk"].get("token")


_BARE = _LEAKY.replace('caps = frozenset({"me", "runs", "cancel"})', 'caps = frozenset({"me"})')


def test_plugin_runs_attach_watch_need_their_caps(tmp_path):
    env = _leaky_env(tmp_path)
    (tmp_path / "leakykind" / "factory" / "leaky.py").write_text(_BARE)
    assert e2e.cli(["login", "lk", "--token", "t-1"], env).returncode == 0
    assert e2e.cli(["use", "lk"], env).returncode == 0
    env["SUPERLITE_WATCH_INLINE"] = "0"  # an agent without a tty would get a watch handoff
    env["CLAUDECODE"] = "1"
    for verb in ("runs", "attach r1", "watch r1"):
        r = e2e.cli(verb.split(), env, input="")
        assert (r.returncode, r.stderr.strip()) == (2, f"lk: {verb.split()[0].replace('watch', 'events')} "
                                                       "is not supported"), verb
        assert r.stdout == ""


def test_attach_refuses_an_ended_run(tmp_path):
    env = _lab(tmp_path)
    name = e2e.cli(["send", "x"], env).stdout.split()[0]
    assert e2e.cli(["cancel", name], env).returncode == 0
    r = e2e.cli(["attach", "lab:" + name], env)
    assert (r.returncode, r.stderr.strip()) == (1, f"lab: {name} has ended")
    assert not (Path(env["SUPERLITE_HOME"]) / "attach" / "test-session.json").exists()


def test_use_orchestrator_and_list(tmp_path):
    env = _lab(tmp_path, superlite=True)
    r = e2e.cli(["use", "lab", "--orchestrator", "human"], env)
    assert r.returncode == 0 and "orchestrator human" in r.stdout, r.stderr
    assert "[human]" in e2e.cli(["list"], env).stdout
    e2e.cli(["use", "lab", "--orchestrator", "ai"], env)
    assert "[human]" not in e2e.cli(["list"], env).stdout


def test_send_flag_beats_default(tmp_path):
    env = _lab(tmp_path, superlite=True)
    e2e.cli(["use", "lab", "--orchestrator", "human"], env)
    r = e2e.cli(["send", "--superlite", "Fix"], env)  # plugin ignores the flag, preamble follows the default
    assert r.returncode == 0, r.stderr
    assert _mem_state(env)["requests"][-1]["prompt"] == factory_api.superlite_prompt("Fix", "human")
    r = e2e.cli(["send", "--superlite", "--orchestrator", "ai", "Fix"], env)
    assert r.returncode == 0, r.stderr
    assert _mem_state(env)["requests"][-1]["prompt"] == factory_api.superlite_prompt("Fix")


def test_bad_orchestrator_value(tmp_path):
    env = _lab(tmp_path)
    r = e2e.cli(["send", "--orchestrator", "robot", "Fix"], env)
    assert r.returncode == 2 and "robot" in r.stderr and "Traceback" not in r.stderr
