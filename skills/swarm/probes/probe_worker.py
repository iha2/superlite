"""The worker side of the probe runner: a persistent subprocess that imports the adapter and runs probes."""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
import traceback
import types
from pathlib import Path

from probe_common import BENIGN_5XX, LEAK_MARKERS, TRACE_MARK, exc_from_text, frames_from_text, load_module



class _WorkerState:
    adapter = None
    ctx = None
    server_starts = 0
    port = None
    stop = None


def _start_server(st: _WorkerState) -> None:
    ad = st.adapter
    r = ad.start_server()
    stop = None
    if isinstance(r, tuple):
        port, stop = r[0], (r[1] if len(r) > 1 else None)
    elif hasattr(r, "server_address"):
        port, stop = r.server_address[1], getattr(r, "shutdown", None)
    else:
        port = int(r)
    st.port, st.stop = port, stop
    st.server_starts += 1
    ad.PORT = port
    os.environ["SUPERLITE_PROBE_PORT"] = str(port)


def _stop_server(st: _WorkerState) -> None:
    if st.stop:
        t = threading.Thread(target=lambda: _safe(st.stop), daemon=True)
        t.start()
        t.join(2)
    st.stop = None


def _untrusted(fn, *args):
    """Run project code (the adapter, a probe's make or check) -> (result, None) or (None, the exception).
    The one place the worker lets any failure of that code, even SystemExit, be data; the caller records it."""
    try:
        return fn(*args), None
    except BaseException as e:
        return None, e


def _safe(fn):
    _untrusted(fn)


def _healthy(st: _WorkerState) -> bool:
    if hasattr(st.adapter, "health"):
        ok, exc = _untrusted(st.adapter.health)
        return exc is None and bool(ok)
    try:
        socket.create_connection(("127.0.0.1", st.port), 1.0).close()
        return True
    except OSError:
        return False


def _recv_head(s: socket.socket) -> bytes:  # O(response<=64KB) network I/O
    buf = b""
    try:
        while len(buf) < 65536:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
            if b"\r\n" in buf and len(buf) > 12:
                break
    except (ConnectionResetError, ConnectionAbortedError):
        pass
    return buf


def _raw_request(port: int, data: bytes, timeout: float) -> int:  # O(len data + response<=64KB) total + network I/O
    with socket.create_connection(("127.0.0.1", port), timeout) as s:
        s.settimeout(timeout)
        try:
            s.sendall(data)
            s.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        buf = _recv_head(s)
    m = re.match(rb"HTTP/\d\.\d (\d{3})", buf)
    return int(m.group(1)) if m else 0


def _bad_status(status: int, expect) -> str | None:  # O(k log k) k = expected statuses
    if expect and expect.get("status"):
        return None if status in expect["status"] else f"status {status} not in {sorted(expect['status'])}"
    if 500 <= status < 600 and status not in BENIGN_5XX:
        return f"status {status}"
    return None


def _dispatch(st: _WorkerState, entry: str, item, expect, timeout: float):
    """Run one item through the adapter's entry point -> (result, problem|None, text|None). text None: no leak check."""
    ad = st.adapter
    expected = tuple(getattr(ad, "EXPECTED_ERRORS", (ValueError,)))
    if entry in ("request", "raw") and (st.port is None or not _healthy(st)):
        if st.port is not None:
            _stop_server(st)
        _start_server(st)
    if entry == "call":
        try:
            r = ad.call(item)
        except expected as e:
            return ("err", type(e).__name__), None, ""
        return ("ok", repr(r)[:500]), None, repr(r)
    if entry == "run":
        try:
            rc, out, err = ad.run(*item)
        except expected as e:
            return ("err", type(e).__name__), None, None
        out = out.decode("utf-8", "replace") if isinstance(out, bytes) else str(out)
        err = err.decode("utf-8", "replace") if isinstance(err, bytes) else str(err)
        res = ("rc", rc, out[:500])
        if rc < 0 or rc >= 0xC0000000 or TRACE_MARK in err:
            return res, {"status": "crash", "exc": exc_from_text(err) if TRACE_MARK in err
                         else f"exit {rc}", "detail": err.strip()[-200:] or f"exit code {rc}",
                         "frames": frames_from_text(err)}, None
        return res, None, out
    if entry == "request":
        try:
            status, js = ad.request(*item)
        except expected as e:
            return ("err", type(e).__name__), None, None
        text = "" if js is None else json.dumps(js, default=str)
        bad = _bad_status(status, expect)
    else:
        status = _raw_request(st.port, item, timeout)
        text = ""
        bad = None if status == 0 else _bad_status(status, expect)
    res = ("status", status)
    if bad:
        return res, {"status": "wrong-status", "detail": bad, "http": status}, None
    return res, None, text


def _exec_one(st: _WorkerState, entry: str, item, expect, timeout: float):  # O(adapter call + input + output bytes) total
    """Return (normalized_result, problem|None). problem = dict(status, detail, ...)."""
    out, exc = _untrusted(_dispatch, st, entry, item, expect, timeout)
    if exc is not None:
        frames = [(f.filename, f.lineno) for f in traceback.extract_tb(exc.__traceback__)][::-1]
        return None, {"status": "crash", "exc": type(exc).__name__, "detail": str(exc)[:200],
                      "frames": frames}
    res, problem, text = out
    if problem or text is None:
        return res, problem
    if TRACE_MARK in text:
        return res, {"status": "leak", "detail": "stack trace in output", "leak": "traceback"}
    for mark in LEAK_MARKERS:
        if mark in text:
            return res, {"status": "leak", "detail": f"system file content served ({mark})",
                         "leak": "file-disclosure"}
    return res, None


def _make_input(st: _WorkerState, probe: dict, size):
    """-> (input, None), or (None, the reply that ends the step: skip or error)."""
    inp, exc = _untrusted(probe["make"], size, st.ctx)
    if exc is None:
        return inp, None
    if type(exc).__name__ == "Skip":
        return None, {"status": "skip", "detail": str(exc), "size": size}
    return None, {"status": "error", "detail": f"make failed: {type(exc).__name__}: {exc}"[:200], "size": size}


def _run_items(st: _WorkerState, probe: dict, inp, timeout: float, out: dict):
    """Run every item; -> the results, or None when an item failed (out holds the problem)."""
    items = list(inp) if type(inp).__name__ == "Seq" else [inp]
    results = []
    for item in items:
        res, problem = _exec_one(st, probe["entry"], item, probe.get("expect"), timeout)
        results.append(res)
        if problem:
            out.update(problem)
            return None
    return results


def _judge(st: _WorkerState, probe: dict, results: list, out: dict) -> None:
    """The state check, then the pass detail."""
    if probe.get("check"):
        why, exc = _untrusted(probe["check"], results, st.ctx)
        if exc is not None:
            why = f"check raised {type(exc).__name__}: {exc}"
        if why:
            out.update(status="inconsistent", detail=str(why)[:200])
    if out["status"] == "pass":
        out["detail"] = " ".join(str(x) for x in results[-1][:2]) if results and results[-1] else "ok"
        if probe["entry"] in ("request", "raw"):
            out["detail"] = f"status {results[-1][1]}"


def _recheck_server(st: _WorkerState, entry: str, out: dict) -> None:
    if entry in ("request", "raw") and st.port is not None and not _healthy(st):
        _stop_server(st)
        _start_server(st)
        if out["status"] == "pass":
            out.update(status="crash", exc="ServerDied",
                       detail="server stopped responding after this probe; restarted", frames=[])


def _step(st: _WorkerState, msg: dict, modules: dict) -> dict:  # O(items * exec cost) total
    import faulthandler
    path = msg["module"]
    if path not in modules:
        modules[path] = load_module(Path(path))
    probe = next(p for p in modules[path].PROBES if p["name"] == msg["name"])
    entry, size, timeout = probe["entry"], msg["size"], msg["timeout"]
    out = {"status": "pass", "detail": "", "size": size}
    faulthandler.dump_traceback_later(max(0.2, timeout * 0.8), file=sys.stderr)
    t0 = time.perf_counter()
    try:
        inp, ended = _make_input(st, probe, size)
        if ended:
            return ended
        results = _run_items(st, probe, inp, timeout, out)
        if results is not None:
            _judge(st, probe, results, out)
        _recheck_server(st, entry, out)
    finally:
        faulthandler.cancel_dump_traceback_later()
    if out["status"] not in ("pass", "skip") and inp is not None:
        r = repr(inp)
        out["input"] = r if len(r) <= 200_000 else None
    out["elapsed"] = round(time.perf_counter() - t0, 4)
    return out


def worker_main(a) -> int:  # O(steps * step cost) total, steps = messages received
    proto_in = os.fdopen(os.dup(0), "r", encoding="utf-8")
    proto_out = os.fdopen(os.dup(1), "w", encoding="utf-8")
    null = os.open(os.devnull, os.O_RDWR)
    os.dup2(null, 0)
    os.dup2(null, 1)
    sys.stdin = open(os.devnull, "r")
    sys.stdout = open(os.devnull, "w")
    import faulthandler
    faulthandler.enable(file=sys.stderr)

    def send(obj):  # O(len obj)
        proto_out.write(json.dumps(obj) + "\n")
        proto_out.flush()

    root = Path(a.root).resolve()
    tmp = Path(tempfile.mkdtemp(prefix="probe-"))
    st = _WorkerState()
    try:
        for p in (root / "src", root):
            if p.is_dir() and str(p) not in sys.path:
                sys.path.insert(1, str(p))
        adapter_path = Path(a.adapter).resolve()
        if str(adapter_path.parent) not in sys.path:
            sys.path.insert(1, str(adapter_path.parent))
        st.adapter = load_module(adapter_path)
        ad = st.adapter
        st.ctx = types.SimpleNamespace(
            adapter=ad, tmp=tmp, good_input=getattr(ad, "GOOD_INPUT", "1"),
            good_args=list(getattr(ad, "GOOD_ARGS", [])), good_stdin=getattr(ad, "GOOD_STDIN", ""),
            good_path=getattr(ad, "GOOD_PATH", "/"))
        entries = {"call": callable(getattr(ad, "call", None)),
                   "run": callable(getattr(ad, "run", None)),
                   "request": callable(getattr(ad, "request", None)),
                   "raw": callable(getattr(ad, "start_server", None))}
        send({"ready": True, "pid": os.getpid(), "tmp": str(tmp), "entries": entries})
    except BaseException as e:
        send({"ready": False, "error": f"{type(e).__name__}: {e}"})
        return 2
    modules: dict = {}
    for line in proto_in:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("op") == "quit":
            break
        try:
            rep = _step(st, msg, modules)
        except BaseException as e:
            rep = {"status": "error", "detail": f"worker failure: {type(e).__name__}: {e}"[:200],
                   "size": msg.get("size")}
        rep["server"] = {"starts": st.server_starts, "port": st.port}
        send(rep)
    _stop_server(st)
    shutil.rmtree(tmp, ignore_errors=True)
    return 0
