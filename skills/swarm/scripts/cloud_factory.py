"""cloud_factory: superlite-cloud as a factory provider, wrapping the real cloud_client.Client."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import cloud_client  # noqa: E402
import cloud_config  # noqa: E402
import cloud_stream  # noqa: E402
from factory_api import SUPERLITE_PREAMBLE, FactoryError, superlite_prompt  # noqa: E402

NAME = "superlite-cloud"


def server_name(title: str | None) -> str | None:
    """Server name rule: 1-3 words of a-z0-9 joined by _. None when the title has no usable word."""
    words = [w for w in re.split(r"[^a-z0-9]+", (title or "").lower()) if w]
    return "_".join(words[:3]) or None


_REMOTE = re.compile(r"[:/]([^/:\s]+/[^/\s]+?)(?:\.git)?/?$")


def origin_repo(cwd: str) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(cwd), "remote", "get-url", "origin"], capture_output=True,
                           text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    m = _REMOTE.search(r.stdout.strip())
    return m.group(1) if m else None


class CloudFactory(cloud_client.Client):
    name = NAME
    nudge_word = "nudge"
    caps = frozenset({"me", "status", "runs", "task", "nudge", "respond", "give", "cancel", "attach",
                      "events", "send"})

    def __repr__(self) -> str:
        return f"<CloudFactory {self.url}>"

    def send(self, prompt: str, title: str | None = None, repo: str | None = None, base: str | None = None,
             superlite: bool = False) -> dict:
        import os
        repo = repo or origin_repo(os.getcwd())
        if not repo:
            raise FactoryError(f"{NAME}: no origin repo")
        if superlite and not prompt.startswith(SUPERLITE_PREAMBLE):
            prompt = superlite_prompt(prompt)
        body = {"repo": repo, "prompt": prompt, "agent": "claude"}
        if base:
            body["base"] = base
        name = server_name(title)
        if name:
            body["name"] = name
        r = self._call("POST", "/api/tasks", body, expect=(200, 201, 202)) or {}
        r = r if isinstance(r, dict) else {}
        return {"id": r.get("id"), "name": r.get("name"), "status": r.get("status") or "queued",
                "owner": r.get("owner"), "agent": r.get("agent") or "claude", "title": r.get("title") or title or "",
                "url": r.get("url"), "created": r.get("created")}

    def events(self, ref: str, after: int = 0):
        return cloud_stream.events(self.url, self._token, ref, after)


def create(config: dict):
    cfg = cloud_config.load()
    if not cfg or not cfg.get("url") or not cfg.get("token"):
        raise FactoryError(f"{NAME}: not logged in")
    return CloudFactory(cfg["url"], cfg["token"])
