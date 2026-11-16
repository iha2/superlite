"""Find, save, load and clear the cloud url and token. Stdlib only; never prints the token."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import state  # noqa: E402

URL_KEY = "SUPERLITE_CLOUD_URL"
TOKEN_KEY = "SUPERLITE_CLOUD_TOKEN"
_TOKEN_SHAPE = re.compile(r"slc_\S{20,}")
_LOCAL = {"localhost", "127.0.0.1", "::1"}


def _home() -> Path:
    h = os.environ.get("SUPERLITE_HOME")
    return Path(h) if h else Path.home() / state.DIR_NAME


def _parse_env(path: Path) -> dict:
    out = {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        k, sep, v = line.partition("=")
        if not sep:
            continue
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def _read_clipboard() -> str:
    cmds = [["pbpaste"], ["wl-paste", "-n"], ["xclip", "-selection", "clipboard", "-o"],
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard"]]
    for cmd in cmds:
        if not shutil.which(cmd[0]):
            continue
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode == 0:
            return r.stdout
    return ""


def _git_exposed(project: Path) -> bool:
    """True when project .env is tracked, or sits in a git repo without being ignored."""
    def git(*a):
        return subprocess.run(["git", *a], cwd=project, capture_output=True, timeout=5).returncode
    try:
        if git("ls-files", "--error-unmatch", ".env") == 0:
            return True
        return git("check-ignore", "-q", ".env") == 1
    except (OSError, subprocess.SubprocessError):
        return False


def _merge(layers: list) -> tuple[dict, str | None, str | None]:
    """First value per key down the layers: (found, first source, source of the token)."""
    found = {URL_KEY: None, TOKEN_KEY: None}
    source = None
    token_src = None
    for name, vals in layers:
        for key in found:
            if found[key] is not None or not vals.get(key):
                continue
            found[key] = vals[key]
            source = source or name
            if key == TOKEN_KEY:
                token_src = name
    return found, source, token_src


def find(project: Path, url: str | None = None, token: str | None = None, clipboard=None) -> dict:
    project = Path(project)
    warnings = []
    penv = project / ".env"
    layers = [
        ("args", {URL_KEY: url, TOKEN_KEY: token}),
        ("project .env", _parse_env(penv) if penv.is_file() else {}),
        ("SUPERLITE_HOME .env", _parse_env(_home() / ".env")),
    ]
    found, source, token_src = _merge(layers)
    if found[TOKEN_KEY] is None:
        try:
            clip = (clipboard or _read_clipboard)() or ""
        except (OSError, subprocess.SubprocessError, ValueError):
            clip = ""
        clip = clip.strip()
        if _TOKEN_SHAPE.fullmatch(clip):
            found[TOKEN_KEY] = clip
            source = source or "clipboard"
            token_src = "clipboard"
    if token_src == "project .env" and _git_exposed(project):
        warnings.append("project .env holds a token git is not ignoring")
    u = found[URL_KEY]
    if u:
        parsed = urlparse(u)
        if parsed.scheme == "http" and (parsed.hostname or "") not in _LOCAL:
            warnings.append("plain http to a non-local address")
    return {"url": u, "token": found[TOKEN_KEY], "source": source, "warnings": warnings}


def _path() -> Path:
    return _home() / "cloud.json"


def save(cfg: dict) -> Path:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(cfg, indent=2)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(data)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return p


def load() -> dict | None:
    try:
        d = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    d.setdefault("statusline_prev", None)
    return d


def clear() -> None:
    try:
        _path().unlink()
    except FileNotFoundError:
        pass
