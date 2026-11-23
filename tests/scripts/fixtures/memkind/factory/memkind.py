"""In-memory factory kind for tests. Loaded by file path; create(config) -> MemProvider | None.

State lives in $SUPERLITE_HOME/memkind-<name>.json so separate subprocesses share it:
{"runs": [raw run dicts], "requests": [{"verb", ...}]}. Tests may edit the file to set a run's "status".
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[5] / "skills" / "swarm" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from factory_api import FactoryError  # noqa: E402

TERMINAL = ("done", "failed", "cancelled")


def _home() -> Path:
    h = os.environ.get("SUPERLITE_HOME")
    return Path(h) if h else Path.home() / ".superlite"


class MemProvider:
    kind = "mem"
    nudge_word = "follow-up"
    caps = frozenset({"me", "status", "runs", "task", "nudge", "respond", "cancel", "attach", "events",
                      "send", "target"})

    def __init__(self, config: dict):
        self.name = config.get("name") or "mem"
        self._token = config.get("token") or ""
        self.target = config.get("target")
        self.target_name = config.get("target_name")

    def __repr__(self) -> str:
        return f"<MemProvider {self.name}>"

    # ---- state ----
    def _path(self) -> Path:
        return _home() / f"memkind-{self.name}.json"

    def _load(self) -> dict:
        try:
            d = json.loads(self._path().read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return d
        except (OSError, ValueError):
            pass
        return {}

    def _save(self, st: dict) -> None:
        p = self._path()
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(st, f)

    def _handle(self) -> str:
        return self.target_name or self.name

    def _map(self, r: dict) -> dict:
        return {"id": r["id"], "name": r["id"][:8], "status": r.get("status", "queued"), "owner": self._handle(),
                "agent": "mem", "title": r.get("title", ""), "url": f"http://127.0.0.1:9/runs/{r['id']}",
                "created": r.get("created")}

    def _find(self, st: dict, ref: str) -> dict:
        hits = [r for r in st.get("runs", []) if ref and r["id"].startswith(ref)]
        exact = [r for r in hits if r["id"] == ref]
        hits = exact or hits
        if not hits:
            raise FactoryError(f"{self.name}: {ref}: not found", 404)
        if len(hits) > 1:
            raise FactoryError(f"{self.name}: {ref} matches more than one run; use more characters")
        return hits[0]

    # ---- verbs ----
    def me(self) -> dict:
        return {"handle": self._handle(), "role": "member"}

    def resolve_target(self, query: str) -> dict:
        return {"uid": query, "name": query}

    def send(self, prompt: str, title: str | None = None, repo: str | None = None, base: str | None = None,
             superlite: bool = False) -> dict:
        st = self._load()
        rid = uuid.uuid4().hex[:12]
        if not title:
            title = next((ln.strip() for ln in prompt.splitlines() if ln.strip()), "")[:80]
        run = {"id": rid, "status": "queued", "title": title}
        st.setdefault("runs", []).append(run)
        st.setdefault("requests", []).append({"verb": "send", "prompt": prompt, "superlite": superlite})
        self._save(st)
        return self._map(run)

    def runs(self, all: bool = False) -> list[dict]:
        return [self._map(r) for r in self._load().get("runs", [])]

    def task(self, ref: str) -> dict:
        return self._map(self._find(self._load(), ref))

    def attach(self, ref: str) -> dict:
        t = self.task(ref)
        return {"run": t["name"], "id": t["id"], "status": t["status"]}

    def _record(self, verb: str, ref: str, **extra) -> None:
        st = self._load()
        run = self._find(st, ref)
        st.setdefault("requests", []).append({"verb": verb, "id": run["id"], **extra})
        if verb == "cancel":
            run["status"] = "cancelled"
        self._save(st)

    def nudge(self, ref: str, text: str) -> None:
        self._record("nudge", ref, text=text)

    def respond(self, ref: str, text: str) -> None:
        self._record("respond", ref, text=text)

    def cancel(self, ref: str) -> None:
        self._record("cancel", ref)

    def status(self) -> dict:
        runs = self.runs()
        return {"runs": [r for r in runs if r["status"] not in TERMINAL], "notices": []}

    def events(self, ref: str, after: int = 0) -> Iterator[dict]:
        r8 = self._find(self._load(), ref)["id"][:8]
        script = [("status", {"run": r8, "to": "running"}),
                  ("text", {"text": "Looking at the test"}),
                  ("tool", {"tool": "run_command", "target": "pytest"}),
                  ("status", {"run": r8, "to": "done"})]
        for n, (kind, data) in enumerate(script, 1):
            if n > after:
                yield {"id": n, "kind": kind, "data": data}


def create(config: dict):
    if not (config or {}).get("token"):
        return None
    return MemProvider(config)
