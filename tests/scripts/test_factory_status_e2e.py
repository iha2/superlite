"""E2E: factory_status segment, entries and delegates against memkind and the cloud fake."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "swarm" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cloud_entries  # noqa: E402
import cloud_fake  # noqa: E402
import factory_e2e as e2e  # noqa: E402

RID = "1a2b3c4d5e6f"
MARK = ".superlite-factory-entry"

# A test-local kind: memkind plus a one-shot notice when a run turns done (memkind itself raises none).
NOTICE_KIND = '''
import importlib.util, os
from pathlib import Path


def create(config):
    spec = importlib.util.spec_from_file_location("memkind_nk", Path(os.environ["MEMKIND_PY"]))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    class P(m.MemProvider):
        kind = "nmem"

        def status(self):
            st = super().status()
            d = self._load()
            fresh = [r for r in d.get("runs", []) if r.get("status") == "done" and not r.get("noticed")]
            for r in fresh:
                r["noticed"] = True
            if fresh:
                self._save(d)
            st["notices"] = [{"text": r["id"][:8] + " done"} for r in fresh]
            return st
    return P(config)
'''


def _login(env, name="lab", kind="mem"):
    p = Path(env["SUPERLITE_HOME"]) / "factories.json"
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"active": name, "factories": {name: {
            "kind": kind, "token": "mk-test", "target": "t1", "target_name": name, "superlite": False}}}, f)


def _state(env, status="running", name="lab"):
    p = Path(env["SUPERLITE_HOME"]) / f"memkind-{name}.json"
    p.write_text(json.dumps({"runs": [{"id": RID, "status": status, "title": "Fix"}]}))


def _attach(env, factory="lab"):
    (Path(env["SUPERLITE_HOME"]) / "attach.json").write_text(
        json.dumps({"run": "1a2b3c4d", "id": RID, "factory": factory}))


def _skills(env):
    return Path(env["CLAUDE_HOME"]) / "skills"


def _cloud(env):
    routes = {("GET", "/api/me"): {"handle": "chris", "role": "member"},
              ("GET", "/api/status"): {"runs": [{"name": "login_fix", "status": "running", "step": 14,
                                                 "doing": "editing api.py"}], "notices": []}}
    url, stop = cloud_fake.serve(routes)
    e2e.cloud_login(env, url)
    return stop


def test_cloud_only_identical(tmp_path):
    env = e2e.env(tmp_path)
    stop = _cloud(env)
    try:
        a = e2e.status_line(env, via="cloud")
        b = e2e.status_line(env)
        assert a == b == "login_fix running · step 14 · editing api.py"
    finally:
        stop()


def test_cloud_hook_delegates(tmp_path):
    env = e2e.env(tmp_path)
    stop = _cloud(env)
    try:
        assert e2e.hook("hello there", env, via="cloud") is None
        assert e2e.hook("hello there", env, via="cloud") == e2e.hook("hello there", env)
        r = subprocess.run(["bash", str(e2e.CLI), "cloud", "hook"], input=json.dumps({"prompt": "x"}),
                           text=True, capture_output=True, cwd=env["_cwd"], timeout=20,
                           env={k: v for k, v in env.items() if k != "_cwd"})
        assert r.returncode == 0 and "Traceback" not in r.stderr
    finally:
        stop()


def test_cloud_delegates_table():
    import cloud
    assert cloud._DELEGATES == {"watch": "cloud_watch", "statusline": "factory_status", "hook": "factory_hook"}


def test_plugin_segment(tmp_path):
    env = e2e.env(tmp_path)
    _login(env)
    _state(env)
    assert e2e.status_line(env) == "1a2b3c4d running"
    _attach(env)
    assert e2e.status_line(env) == "ATTACHED 1a2b3c4d"
    stop = _cloud(env)  # two logged in: the label carries the kind
    try:
        assert e2e.status_line(env) == \
            "login_fix running · step 14 · editing api.py · ATTACHED lab(mem) 1a2b3c4d"
        (Path(env["SUPERLITE_HOME"]) / "attach.json").unlink()
        assert e2e.status_line(env).endswith(" · lab(mem) 1a2b3c4d running")
    finally:
        stop()


def test_notice_once(tmp_path):
    env = e2e.env(tmp_path)
    kind = tmp_path / "nk"
    (kind / "factory").mkdir(parents=True)
    (kind / "superlite-factory.json").write_text(json.dumps(
        {"api": 1, "kind": "nmem", "label": "Notice mem", "module": "factory/k.py", "entry": "create"}))
    (kind / "factory" / "k.py").write_text(NOTICE_KIND)
    env["MEMKIND_PY"] = str(e2e.MEMKIND / "factory" / "memkind.py")
    env["SUPERLITE_FACTORY_PATH"] = os.pathsep.join([env["SUPERLITE_FACTORY_PATH"], str(kind)])
    _login(env, kind="nmem")
    _state(env)
    assert e2e.status_line(env) == "1a2b3c4d running"
    _state(env, "done")
    assert e2e.status_line(env) == "new: 1a2b3c4d done"
    assert e2e.status_line(env) == ""


def test_ended_detaches(tmp_path):
    env = e2e.env(tmp_path)
    _login(env)
    _state(env, "done")
    _attach(env)
    e2e.status_line(env)
    assert not (Path(env["SUPERLITE_HOME"]) / "attach.json").exists()
    _attach(env)
    (Path(env["SUPERLITE_HOME"]) / "memkind-lab.json").write_text(json.dumps({"runs": []}))
    assert e2e.status_line(env) == ""
    assert not (Path(env["SUPERLITE_HOME"]) / "attach.json").exists()


def test_entries_created_and_removed(tmp_path):
    env = e2e.env(tmp_path)
    _login(env)
    _state(env)
    e2e.status_line(env)
    for kind in ("attach", "watch"):
        d = _skills(env) / f"superlite-{kind}-lab-1a2b3c4d"
        assert (d / MARK).exists()
        assert f"superlite-cli factory {kind} lab:1a2b3c4d" in (d / "SKILL.md").read_text()
    _state(env, "done")
    e2e.status_line(env)
    assert not (_skills(env) / "superlite-attach-lab-1a2b3c4d").exists()
    assert not (_skills(env) / "superlite-watch-lab-1a2b3c4d").exists()


def test_cloud_entries_untouched(tmp_path):
    env = e2e.env(tmp_path)
    _login(env)
    cloud_dir = _skills(env) / "superlite-attach-login_fix"
    cloud_dir.mkdir(parents=True)
    (cloud_dir / cloud_entries.MARKER).write_text("")
    (cloud_dir / "SKILL.md").write_text("cloud")
    _state(env)
    e2e.status_line(env)
    assert (cloud_dir / "SKILL.md").read_text() == "cloud"
    assert (cloud_dir / cloud_entries.MARKER).exists()
