"""T3: cloud client. Small stdlib HTTP client; the token never appears in errors."""
from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request


class CloudError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class Client:
    def __init__(self, url: str, token: str, timeout: float = 5.0) -> None:
        self.url = url.rstrip("/")
        self._token = token
        self.timeout = timeout

    def _scrub(self, s: str) -> str:
        s = str(s)
        return s.replace(self._token, "***") if self._token else s

    def _call(self, method: str, path: str, body: dict | None = None, ref: str | None = None,
              expect: tuple = (200, 201, 202, 204)):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self._token}")
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                status = getattr(r, "status", 200)
                raw = r.read()
        except urllib.error.HTTPError as e:
            msg = ""
            try:
                msg = str(json.loads(e.read() or b"{}").get("error") or "")
            except (OSError, ValueError, AttributeError, http.client.HTTPException):
                pass
            msg = self._scrub(msg) or f"HTTP {e.code}"
            raise CloudError(f"{ref}: {msg}" if ref else msg) from None
        except (urllib.error.URLError, OSError, ValueError):
            raise CloudError(f"cloud unreachable: {self.url}") from None
        if status not in expect:
            raise CloudError(self._scrub(f"{ref}: " if ref else "") + f"unexpected status {status}")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            raise CloudError("bad response from cloud") from None

    @staticmethod
    def _task(ref: str, suffix: str = "", base: str = "tasks") -> str:
        return f"/api/{base}/" + urllib.parse.quote(ref, safe="/") + suffix

    def me(self) -> dict:
        return self._call("GET", "/api/me")

    def status(self) -> dict:
        return self._call("GET", "/api/status")

    def runs(self, all: bool = False) -> list[dict]:
        return self._call("GET", "/api/tasks?all=1" if all else "/api/tasks")

    def nudge(self, ref: str, text: str) -> None:
        self._call("POST", self._task(ref, "/nudge", "runs"), {"text": text}, ref, expect=(202,))

    def give(self, ref: str, email: str) -> dict:
        return self._call("POST", self._task(ref, "/give", "runs"), {"email": email}, ref)

    def respond(self, ref: str, text: str) -> None:
        self._call("POST", self._task(ref, "/respond"), {"answer": text}, ref)

    def cancel(self, ref: str) -> None:
        self._call("POST", self._task(ref, "/cancel"), {}, ref)

    def task(self, ref: str) -> dict:
        """GET /api/tasks/{ref}: one run by name or id. Unlike the list call it does not mark notices seen."""
        return self._call("GET", self._task(ref), None, ref)

    def attach(self, ref: str) -> dict:
        return self._call("GET", self._task(ref, "/attach", "runs"), None, ref)
