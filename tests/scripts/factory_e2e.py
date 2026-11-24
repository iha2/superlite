"""E2E helpers: hermetic env and subprocess runners for the superlite-cli factory commands."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
CLI = ROOT / "skills" / "swarm" / "scripts" / "superlite-cli"
MEMKIND = HERE / "fixtures" / "memkind"
_GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"
BASH = _GIT_BASH if os.name == "nt" and os.path.exists(_GIT_BASH) else shutil.which("bash")


def _git(cwd, *a):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


def env(tmp_path, cloud_url: str | None = None) -> dict:
    tmp = Path(tmp_path)
    e = {k: v for k, v in os.environ.items()
         if not k.startswith(("SUPERLITE_", "CLAUDE", "MEM_")) and k != "HOME"}
    home = tmp / "home"
    cwd = tmp / "repo"
    for d in (home, home / "sl", home / "claude", cwd):
        d.mkdir(parents=True, exist_ok=True)
    _git(cwd, "init", "-q")
    _git(cwd, "remote", "add", "origin", "git@github.com:acme/app.git")
    e.update(HOME=str(home), USERPROFILE=str(home), SUPERLITE_HOME=str(home / "sl"),
             CLAUDE_HOME=str(home / "claude"), SUPERLITE_FACTORY_PATH=str(MEMKIND),
             PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1", PYTHONIOENCODING="utf-8", _cwd=str(cwd))
    if cloud_url:
        e["SUPERLITE_CLOUD_URL"] = cloud_url
    return e


def _run(args, env, input=None, timeout=20):
    e = {k: v for k, v in env.items() if k != "_cwd"}
    return subprocess.run([BASH, str(CLI), *args], input=input, env=e, cwd=env.get("_cwd"),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def cli(args: list, env: dict, input: str | None = None, timeout: float = 20):
    return _run(["factory", *args], env, input, timeout)


def hook(prompt: str, env: dict, via: str = "factory"):
    args = ["cloud", "hook"] if via == "cloud" else ["factory_hook"]
    out = _run(args, env, json.dumps({"prompt": prompt})).stdout.strip()
    return json.loads(out) if out else None


def status_line(env: dict, via: str = "factory") -> str:
    args = ["cloud", "statusline"] if via == "cloud" else ["factory_status"]
    return _run(args, env).stdout.replace("\r", "").strip()


def cloud_login(env: dict, url: str, token: str = "slc_test_tok") -> None:
    p = Path(env["SUPERLITE_HOME"]) / "cloud.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"url": url, "token": token}, f)
    os.chmod(p, 0o600)
