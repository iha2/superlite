"""T4: cloud stream. Reads a run's SSE events, resuming after drops; the token never appears in errors."""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from cloud_client import CloudError  # noqa: E402

TERMINAL = ("done", "failed", "cancelled")


def _frames(lines) -> Iterator[dict]:
    """Yield {"id", "event", "data"} per SSE frame; comment lines are keepalives."""
    fid, ev, data = None, "message", []
    for raw in lines:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if line == "":
            if data or fid is not None:
                yield {"id": fid, "event": ev, "data": "\n".join(data)}
            fid, ev, data = None, "message", []
        elif line.startswith(":"):
            continue
        else:
            name, _, val = line.partition(":")
            val = val[1:] if val.startswith(" ") else val
            if name == "data":
                data.append(val)
            elif name == "id":
                fid = val
            elif name == "event":
                ev = val


def _open(url: str, token: str, ref: str, after: int):
    path = "/api/runs/" + urllib.parse.quote(ref, safe="/") + "/events"
    full = url.rstrip("/") + path + (f"?after={after}" if after else "")
    req = urllib.request.Request(full)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "text/event-stream")
    if after:
        req.add_header("Last-Event-ID", str(after))
    return urllib.request.urlopen(req, timeout=30)


def _scrub(s, token: str) -> str:
    s = str(s)
    return s.replace(token, "***") if token else s


def _decode(fr: dict, last: int):
    """(id, data) for a frame that is new after `last`, else None."""
    try:
        eid = int(fr["id"])
    except (TypeError, ValueError):
        return None
    if eid <= last:
        return None
    try:
        data = json.loads(fr["data"]) if fr["data"] else {}
    except ValueError:
        data = {"text": fr["data"]}
    return eid, data


def _read(url: str, token: str, ref: str, st: dict) -> Iterator[dict]:
    """Yield the new events of one connection, keeping `st` (last id, fails, ended) current; a result ends the run."""
    with _open(url, token, ref, st["last"]) as r:
        for fr in _frames(r):
            got = _decode(fr, st["last"])
            if got is None:
                continue
            eid, data = got
            st["last"], st["fails"] = eid, 0
            yield {"id": eid, "kind": fr["event"], "data": data}
            if fr["event"] == "result":
                st["result"] = True
                return
            if fr["event"] == "status" and isinstance(data, dict) and data.get("to") in TERMINAL:
                st["ended"] = True  # keep draining this connection for a trailing result; never reconnect


def _http_failure(e: urllib.error.HTTPError, ref: str, token: str) -> None:
    """Raise for a failure that retrying cannot fix (404, other 4xx); a 5xx returns and is retried."""
    if e.code == 404:
        raise CloudError(f"{ref}: not found") from None
    if e.code < 500:
        raise CloudError(_scrub(f"{ref}: HTTP {e.code}", token)) from None


def events(url: str, token: str, ref: str, after: int = 0, sleep=time.sleep, max_retries: int = 8) -> Iterator[dict]:
    st = {"last": after, "fails": 0, "ended": False, "result": False}
    while True:
        try:
            yield from _read(url, token, ref, st)
            if st["result"] or st["ended"]:
                return
        except urllib.error.HTTPError as e:
            if st["ended"]:
                return
            _http_failure(e, ref, token)
        except (urllib.error.URLError, OSError, ValueError):
            if st["ended"]:
                return
        st["fails"] += 1
        if st["fails"] > max_retries:
            raise CloudError(_scrub(f"{ref}: stream lost after {max_retries} retries ({url})", token))
        sleep(min(10, 0.5 * 2 ** (st["fails"] - 1)))
