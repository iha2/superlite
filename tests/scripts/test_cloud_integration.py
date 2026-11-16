"""Live cloud runs, end to end against a fake server: login, hook, watch, logout, stdlib only."""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cloud_fake  # noqa: E402

_GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"
# A bare "bash" on Windows finds System32\bash.exe (WSL) first, so ask Git Bash by path.
BASH = _GIT_BASH if os.name == "nt" and os.path.exists(_GIT_BASH) else shutil.which("bash")
ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "swarm" / "scripts"
CLI = SCRIPTS / "superlite-cli"
HOOK = ROOT / "hooks" / "prompt"
TOKEN = "slc_" + "a1b2c3d4e5" * 3
ME = {"handle": "chris", "email": "chris@example.com", "role": "member", "workspace": "acme"}
RUN = {"name": "login_fix", "owner": "chris", "status": "running", "agent": "claude"}
EVENTS = [
    {"id": 1, "kind": "status", "data": {"run": "login_fix", "to": "running"}},
    {"id": 2, "kind": "nudge", "data": {"by": "sam", "text": "run the tests first"}},
    {"id": 3, "kind": "status", "data": {"run": "login_fix", "to": "done", "result": "PR opened"}},
]


def routes():
    return {
        ("GET", "/api/me"): ME,
        ("GET", "/api/status"): {"runs": [RUN], "notices": []},
        ("GET", "/api/tasks"): [RUN],
        ("POST", "/api/runs/login_fix/nudge"): (202, {"id": 1, "status": "queued"}),
        ("GET", "/api/runs/login_fix/events"): EVENTS,
    }


@pytest.fixture
def world(tmp_path):
    home, claude, proj = tmp_path / "home", tmp_path / "claude", tmp_path / "proj"
    for d in (home, claude, proj):
        d.mkdir()
    env = dict(os.environ, SUPERLITE_HOME=str(home), CLAUDE_HOME=str(claude), HOME=str(tmp_path),
               CLAUDE_PLUGIN_ROOT=str(ROOT), SUPERLITE_NO_SESSION_HUD="1", SUPERLITE_NO_COST_REPORT="1")
    for k in ("SUPERLITE_CLOUD_URL", "SUPERLITE_CLOUD_TOKEN"):
        env.pop(k, None)
    stops = []

    def start(r=None):
        url, stop = cloud_fake.serve(routes() if r is None else r)
        stops.append(stop)
        return url, stop

    yield env, home, claude, proj, start
    for s in stops:
        try:
            s()
        except Exception:
            pass


def cli(env, proj, *args, stdin=None):
    return subprocess.run([BASH, str(CLI), "cloud", *args], capture_output=True, text=True, env=env,
                          cwd=proj, timeout=30, input=stdin if stdin is not None else "")


def login(env, proj, url):
    r = cli(env, proj, "login", "--url", url, "--token", TOKEN)
    assert r.returncode == 0, r.stderr
    return r


def hook(env, proj, prompt):
    data = json.dumps({"prompt": prompt, "cwd": str(proj), "session_id": "s1"})
    return subprocess.run([BASH, str(HOOK)], input=data, capture_output=True, text=True, env=env,
                          cwd=proj, timeout=30)


def test_login_then_nudge(world):
    env, home, claude, proj, start = world
    url, stop = start()
    r = login(env, proj, url)
    assert TOKEN not in r.stdout + r.stderr
    out = hook(env, proj, ">> hi")
    assert out.returncode == 0
    j = json.loads(out.stdout)
    assert j["decision"] == "block" and "→ login_fix" in j["reason"]
    assert ("POST", "/api/runs/login_fix/nudge", {"text": "hi"}) in stop.requests
    assert TOKEN not in out.stdout + out.stderr


def test_no_network_nudge(world):
    env, home, claude, proj, start = world
    url, stop = start()
    login(env, proj, url)
    (home / "attach.json").write_text(json.dumps({"run": "login_fix", "id": "t_1"}))
    stop()
    out = hook(env, proj, ">> hi")
    assert out.returncode == 0
    j = json.loads(out.stdout)
    assert j["decision"] == "block" and "not sent" in j["reason"] and "unreachable" in j["reason"]
    assert TOKEN not in out.stdout
    hello = hook(env, proj, "hello")  # attached: a plain line is a send, so it is blocked with the error too
    assert hello.returncode == 0 and "unreachable" in json.loads(hello.stdout)["reason"]
    esc = hook(env, proj, "\\hello")
    assert esc.returncode == 0 and "block" not in esc.stdout


def test_watch_replay(world):
    env, home, claude, proj, start = world
    url, stop = start()
    login(env, proj, url)
    r = cli(env, proj, "watch", "login_fix")
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    assert "login_fix running" in lines[0]
    assert "nudge from sam: run the tests first" in lines[1]
    assert lines[-1] == "login_fix done: PR opened"
    assert TOKEN not in r.stdout + r.stderr


def test_logout_clean(world):
    env, home, claude, proj, start = world
    url, stop = start()
    settings = claude / "settings.json"
    original = '{"theme": "dark"}\n'
    settings.write_text(original)
    login(env, proj, url)
    assert "statusLine" in json.loads(settings.read_text())
    sys.path.insert(0, str(SCRIPTS))
    import cloud_entries
    cloud_entries.sync([RUN], claude / "skills", "chris", False)
    assert any((claude / "skills").iterdir())
    r = cli(env, proj, "logout")
    assert r.returncode == 0
    assert settings.read_text() == original
    assert not list((claude / "skills").glob("superlite-*"))
    assert not (home / "cloud.json").exists()


def test_stdlib_only():
    mods = sorted(SCRIPTS.glob("cloud*.py")) + [SCRIPTS / "session_rename.py"]
    local = {p.stem for p in SCRIPTS.glob("*.py")}
    std = set(sys.stdlib_module_names)
    assert len(mods) >= 8
    for p in mods:
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for n in names:
                top = n.split(".")[0]
                assert top in std or top in local, f"{p.name} imports non-stdlib {n}"
