#!/usr/bin/env python3
"""Parallel probe runner (no agents). Stdlib only, Python >= 3.12.

    probe_run.py --adapter PATH [--root DIR] [--app-type T] [--probes DIR]
                 [--categories deep,numeric,...] [--files a,b | --checkpoint SHA]
                 [--timeout 10] [--workers N] [--out .superlite/probes/probe-results.json]
                 [--log .superlite/incidents.jsonl] [--background]

Results file (--out, default <root>/.superlite/probes/probe-results.json):
    {"files": [...], "selected_categories": [...], "categories": {cat: {"status", "cached", "probes",
      "files", ...}}, "failures": [group, ...], "stats": {...}, "probes": [probe, ...]}
  probe: id, category, entry (call|run|request|raw), status, file, line, group_id, input_size,
         message (plus detail, size, elapsed, timeout). status is pass | crash | hang | leak |
         wrong-status; also inconsistent (state check failed), skip, error (probe bug).
         file and line are the innermost in-repo frame of the failure (null on a pass or when
         none is known); group_id points into "failures"; input_size is the size step reached.
  group (one incident each): group_id, key, category, kind, exc, count, probes, size,
         shrunk_size, file, line, regression_test, incident.

Exit code: 0 all probes passed, 1 at least one failure, 2 usage or adapter error.

How it works
  * Probe generators live in pack/<category>.py; project probes (written by the reviewer from the
    plan's Review Focus) go in a --probes DIR, same format (see pack/_common.py).
  * Workers are persistent subprocesses (one thread of a ThreadPoolExecutor drives each). A worker
    imports the adapter once, runs probes, and keeps one server (started on port 0) for all of its
    server probes. The server is health-checked after every probe and restarted only if it died.
  * A probe that does not answer within its category timeout (call 2 s, run 5 s, request 10 s) is a
    hang: that worker's process tree is killed by PID and the worker is replaced. Only PIDs this
    runner started are ever killed; never by image name.
  * Sized probes step up (e.g. 100, 2000, 10000) and stop at the first failure.
  * Failures are grouped by root cause (kind + exception + innermost in-repo file:line), shrunk by
    bisection (<= 5 s per group), turned into tests/regression/test_probe_<cat>_<n>.py and logged as
    one incident per group. Passing categories log a `verified` entry per file.
  * Passes are cached by (category, file, content hash) in <root>/.superlite/probes/cache.json. The
    hash covers the file, the adapter and the probe sources. Failures are always re-probed.
  * Categories with a `verified` entry (a reviewer's, or this runner's with the same hash) for a
    file are skipped for that file.
"""  # O(1)
# Complexity notation:
# p = probes in catalog, p_m = probes in one module, p_c = probes in one category, c = categories,
# f = files under test, m = probe module files, w = worker processes, z = size steps per probe,
# g = failure groups, t = existing regression tests, e = incident log entries, v = verified jobs, j = incident jobs,
# a = workers ever spawned (incl. dead), T = tree entries, n = names/entries in a list, d = dirs,
# B = bytes of one file, k = small set size, len x = size of text/bytes x,
# "size" = probe input size step (bisected in shrink_group).
from __future__ import annotations  # O(1)

import argparse  # O(1)
import concurrent.futures as cf  # O(1)
import hashlib  # O(1)
import importlib.util  # O(1)
import json  # O(1)
import os  # O(1)
import queue  # O(1)
import re  # O(1)
import shutil  # O(1)
import socket  # O(1)
import subprocess  # O(1)
import sys  # O(1)
import tempfile  # O(1)
import threading  # O(1)
import time  # O(1)
import traceback  # O(1)
import types  # O(1)
from pathlib import Path  # O(1)

HERE = Path(__file__).resolve().parent  # O(1)
if str(HERE) not in sys.path:  # O(len sys.path)
    sys.path.insert(0, str(HERE))  # O(1)

IS_WIN = sys.platform == "win32"  # O(1)
ENTRY_TIMEOUT = {"call": 2.0, "run": 5.0, "request": 10.0, "raw": 10.0}  # O(1)
CATEGORY_ORDER = ["deep", "numeric", "encoding", "protocol", "file", "state"]  # O(1)
APP_TYPES = {  # O(1)
    "library": ["deep", "numeric", "encoding", "state"],  # O(1)
    "cli": ["protocol", "encoding", "state", "file"],  # O(1)
    "http": ["protocol", "encoding", "numeric", "state"],  # O(1)
    "web-ui": ["protocol", "encoding"],  # O(1)
    "file-io": ["file", "encoding", "deep"],  # O(1)
    "pipeline": ["numeric", "encoding", "state", "deep"],  # O(1)
    "concurrency": ["state", "protocol"],  # O(1)
    "auth": ["protocol", "state"],  # O(1)
}  # O(1)
APP_ALIASES = {"parser": "library", "evaluator": "library", "server": "http", "service": "http",  # O(1)
               "http-service": "http", "web": "web-ui", "ui": "web-ui", "generator": "file-io",  # O(1)
               "file": "file-io", "data-pipeline": "pipeline"}  # O(1)
TRACE_MARK = "Traceback (most recent call last)"  # O(1)
LEAK_MARKERS = ("root:x:0:0", "for 16-bit app support", "[extensions]")  # O(1)
BENIGN_5XX = {501, 505}  # O(1)
CODE_EXT = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".go", ".rs", ".rb", ".java",  # O(1)
            ".cs", ".php", ".sh"}  # O(1)
SKIP_DIRS = {".git", ".superlite", "tests", "test", "__pycache__", "venv", ".venv", "node_modules",  # O(1)
             "regression", "site-packages", "dist", "build"}  # O(1)
SHRINK_BUDGET = 5.0  # O(1)
FRAME_RE = re.compile(r'File "([^"]+)", line (\d+)')  # O(1)


# ----------------------------------------------------------------------------- helpers
def kill_tree(proc: subprocess.Popen) -> None:  # O(1) total + taskkill spawn
    """Kill the process we started and its descendants, by PID only."""  # O(1)
    pid = proc.pid  # O(1)
    try:  # O(1)
        if IS_WIN:  # O(1)
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,  # O(1) + process spawn (taskkill, <=15s)
                           timeout=15)  # O(1)
        else:  # O(1)
            import signal  # O(1)
            try:  # O(1)
                os.killpg(pid, signal.SIGKILL)  # O(1) syscall
            except (ProcessLookupError, PermissionError):  # O(1)
                pass  # O(1)
    except Exception:  # O(1)
        pass  # O(1)
    try:  # O(1)
        proc.kill()  # O(1)
    except Exception:  # O(1)
        pass  # O(1)
    try:  # O(1)
        proc.wait(timeout=5)  # O(1) + wait <=5s
    except Exception:  # O(1)
        pass  # O(1)


def load_module(path: Path):  # O(src bytes) total (exec module)
    name = "_probemod_" + hashlib.md5(str(path).encode()).hexdigest()[:10]  # O(len path)
    if name in sys.modules:  # O(1)
        return sys.modules[name]  # O(1)
    spec = importlib.util.spec_from_file_location(name, path)  # O(1)
    mod = importlib.util.module_from_spec(spec)  # O(1)
    sys.modules[name] = mod  # O(1)
    spec.loader.exec_module(mod)  # O(module size) exec
    return mod  # O(1)


def probe_modules(extra: Path | None) -> list[Path]:  # O(m log m) total, m = probe module files
    mods = [HERE / "pack" / f"{c}.py" for c in CATEGORY_ORDER if (HERE / "pack" / f"{c}.py").exists()]  # O(c) (2 stat per category)
    if extra and extra.is_dir():  # O(1)
        mods += sorted(p for p in extra.glob("*.py") if not p.name.startswith("_"))  # O(m log m) glob + sort
    return mods  # O(1)


def load_catalog(extra: Path | None) -> tuple[list[dict], list[str]]:  # O(m + p) total + module exec
    catalog, errors = [], []  # O(1)
    for path in probe_modules(extra):  # O(m) iterations
        try:  # O(1)
            mod = load_module(path)  # O(module size) first load only
            cat = mod.CATEGORY  # O(1)
            for p in mod.PROBES:  # O(p_m) iterations
                catalog.append({"id": f"{cat}/{p['name']}", "category": cat, "name": p["name"],  # O(1)
                                "entry": p["entry"], "sizes": p["sizes"] or [None],  # O(1)
                                "module": str(path), "regress": p.get("regress", True),  # O(1)
                                "has_check": p.get("check") is not None})  # O(1)
        except Exception as e:  # O(1)
            errors.append(f"{path.name}: {type(e).__name__}: {e}")  # O(1)
    return catalog, errors  # O(1)


def frames_from_text(text: str) -> list[tuple[str, int]]:  # O(len text) total
    """Innermost-first frames from a Python traceback, a faulthandler dump or both."""  # O(1)
    if "most recent call first" in text:  # O(len text) substring search
        text = text[text.index("most recent call first"):]  # O(len text) slice
        return [(f, int(n)) for f, n in FRAME_RE.findall(text)]  # O(len text) regex
    if TRACE_MARK in text:  # O(len text) substring search
        chunk = text[text.rindex(TRACE_MARK):]  # O(len text) rindex + slice
        return [(f, int(n)) for f, n in FRAME_RE.findall(chunk)][::-1]  # O(len text) regex + reverse
    return []  # O(1)


def exc_from_text(text: str) -> str:  # O(len text) total
    for line in reversed([x for x in text.strip().splitlines() if x.strip()]):  # O(len text) split/filter
        m = re.match(r"^([A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt|Warning)?)\b:?", line)  # O(len line) regex per iteration
        if m and not line.startswith((" ", "\t")):  # O(1)
            return m.group(1)  # O(1)
    return "Exception"  # O(1)


# ----------------------------------------------------------------------------- worker side
class _WorkerState:  # O(1)
    adapter = None  # O(1)
    ctx = None  # O(1)
    server_starts = 0  # O(1)
    port = None  # O(1)
    stop = None  # O(1)


def _start_server(st: _WorkerState) -> None:  # O(1) + server start
    ad = st.adapter  # O(1)
    r = ad.start_server()  # O(1) + server start
    stop = None  # O(1)
    if isinstance(r, tuple):  # O(1)
        port, stop = r[0], (r[1] if len(r) > 1 else None)  # O(1)
    elif hasattr(r, "server_address"):  # O(1)
        port, stop = r.server_address[1], getattr(r, "shutdown", None)  # O(1)
    else:  # O(1)
        port = int(r)  # O(1)
    st.port, st.stop = port, stop  # O(1)
    st.server_starts += 1  # O(1)
    ad.PORT = port  # O(1)
    os.environ["SUPERLITE_PROBE_PORT"] = str(port)  # O(1)


def _stop_server(st: _WorkerState) -> None:  # O(1) + join wait <=2s
    if st.stop:  # O(1)
        t = threading.Thread(target=lambda: _safe(st.stop), daemon=True)  # O(1)
        t.start()  # O(1)
        t.join(2)  # O(1)
    st.stop = None  # O(1)


def _safe(fn):  # O(1) + callback cost
    try:  # O(1)
        fn()  # O(1)
    except Exception:  # O(1)
        pass  # O(1)


def _healthy(st: _WorkerState) -> bool:  # O(1) + health/connect wait <=1s
    if hasattr(st.adapter, "health"):  # O(1)
        try:  # O(1)
            return bool(st.adapter.health())  # O(1)
        except Exception:  # O(1)
            return False  # O(1)
    try:  # O(1)
        socket.create_connection(("127.0.0.1", st.port), 1.0).close()  # O(1) + connect wait <=1s
        return True  # O(1)
    except OSError:  # O(1)
        return False  # O(1)


def _raw_request(port: int, data: bytes, timeout: float) -> int:  # O(len data + response<=64KB) total + network I/O
    with socket.create_connection(("127.0.0.1", port), timeout) as s:  # O(1) + connect wait <=timeout
        s.settimeout(timeout)  # O(1)
        try:  # O(1)
            s.sendall(data)  # O(1)
            s.shutdown(socket.SHUT_WR)  # O(len data) send
        except OSError:  # O(1)
            pass  # O(1)
        buf = b""  # O(1)
        try:  # O(1)
            while len(buf) < 65536:  # O(16) iterations (64KB/4KB cap)
                chunk = s.recv(4096)  # O(4KB) recv, blocks <=timeout
                if not chunk:  # O(1)
                    break  # O(1)
                buf += chunk  # O(len buf) copy, buf <=64KB
                if b"\r\n" in buf and len(buf) > 12:  # O(len buf) rescan, bounded by 64KB
                    break  # O(1)
        except (ConnectionResetError, ConnectionAbortedError):  # O(1)
            pass  # O(1)
    m = re.match(rb"HTTP/\d\.\d (\d{3})", buf)  # O(1) anchored regex
    return int(m.group(1)) if m else 0  # O(1)


def _bad_status(status: int, expect) -> str | None:  # O(k log k) k = expected statuses
    if expect and expect.get("status"):  # O(1)
        return None if status in expect["status"] else f"status {status} not in {sorted(expect['status'])}"  # O(k) membership; sorted only on mismatch
    if 500 <= status < 600 and status not in BENIGN_5XX:  # O(1)
        return f"status {status}"  # O(1)
    return None  # O(1)


def _exec_one(st: _WorkerState, entry: str, item, expect, timeout: float):  # O(adapter call + input + output bytes) total
    """Return (normalized_result, problem|None). problem = dict(status, detail, ...)."""  # O(1)
    ad = st.adapter  # O(1)
    expected = tuple(getattr(ad, "EXPECTED_ERRORS", (ValueError,)))  # O(k) k = expected error types
    try:  # O(1)
        if entry in ("request", "raw") and (st.port is None or not _healthy(st)):  # O(1)
            if st.port is not None:  # O(1)
                _stop_server(st)  # O(1)
            _start_server(st)  # O(1)
        if entry == "call":  # O(1)
            try:  # O(1)
                r = ad.call(item)  # O(adapter call) + O(len result)
                res, text = ("ok", repr(r)[:500]), repr(r)  # O(len result) repr twice
            except expected as e:  # O(1)
                res, text = ("err", type(e).__name__), ""  # O(1)
        elif entry == "run":  # O(1)
            try:  # O(1)
                rc, out, err = ad.run(*item)  # O(adapter run) + process spawn
            except expected as e:  # O(1)
                return ("err", type(e).__name__), None  # O(1)
            out = out.decode("utf-8", "replace") if isinstance(out, bytes) else str(out)  # O(len out) decode
            err = err.decode("utf-8", "replace") if isinstance(err, bytes) else str(err)  # O(len err) decode
            res, text = ("rc", rc, out[:500]), out  # O(1) slice 500
            if rc < 0 or rc >= 0xC0000000 or TRACE_MARK in err:  # O(1)
                return res, {"status": "crash", "exc": exc_from_text(err) if TRACE_MARK in err  # O(len err) frames/exc parse
                             else f"exit {rc}", "detail": err.strip()[-200:] or f"exit code {rc}",  # O(len err)
                             "frames": frames_from_text(err)}  # O(len err)
        elif entry == "request":  # O(1)
            try:  # O(1)
                status, js = ad.request(*item)  # O(adapter request) + network I/O
            except expected as e:  # O(1)
                return ("err", type(e).__name__), None  # O(1)
            res, text = ("status", status), "" if js is None else json.dumps(js, default=str)  # O(len js) json.dumps
            bad = _bad_status(status, expect)  # O(1)
            if bad:  # O(1)
                return res, {"status": "wrong-status", "detail": bad, "http": status}  # O(1)
        else:  # O(1) | raw
            status = _raw_request(st.port, item, timeout)  # O(request + response) network I/O
            res, text = ("status", status), ""  # O(1)
            bad = None if status == 0 else _bad_status(status, expect)  # O(1)
            if bad:  # O(1)
                return res, {"status": "wrong-status", "detail": bad, "http": status}  # O(1)
    except BaseException as e:  # O(1) | noqa: BLE001 - any unexpected exception is a crash
        frames = [(f.filename, f.lineno) for f in traceback.extract_tb(e.__traceback__)][::-1]  # O(stack depth)
        return None, {"status": "crash", "exc": type(e).__name__, "detail": str(e)[:200],  # O(1)
                      "frames": frames}  # O(1)
    if TRACE_MARK in text:  # O(len text) substring search
        return res, {"status": "leak", "detail": "stack trace in output", "leak": "traceback"}  # O(1)
    for mark in LEAK_MARKERS:  # O(3) iterations
        if mark in text:  # O(len text) substring search
            return res, {"status": "leak", "detail": f"system file content served ({mark})",  # O(1)
                         "leak": "file-disclosure"}  # O(1)
    return res, None  # O(1)


def _step(st: _WorkerState, msg: dict, modules: dict) -> dict:  # O(items * exec cost) total
    import faulthandler  # O(1) cached import
    path = msg["module"]  # O(1)
    if path not in modules:  # O(1)
        modules[path] = load_module(Path(path))  # O(module size) first load per module
    probe = next(p for p in modules[path].PROBES if p["name"] == msg["name"])  # O(P_m) linear scan of module probes per step
    entry, size, timeout = probe["entry"], msg["size"], msg["timeout"]  # O(1)
    out = {"status": "pass", "detail": "", "size": size}  # O(1)
    faulthandler.dump_traceback_later(max(0.2, timeout * 0.8), file=sys.stderr)  # O(1)
    t0 = time.perf_counter()  # O(1)
    inp = None  # O(1)
    try:  # O(1)
        try:  # O(1)
            inp = probe["make"](size, st.ctx)  # O(make(size))
        except BaseException as e:  # O(1) | noqa: BLE001
            if type(e).__name__ == "Skip":  # O(1)
                return {"status": "skip", "detail": str(e), "size": size}  # O(1)
            return {"status": "error", "detail": f"make failed: {type(e).__name__}: {e}"[:200],  # O(1)
                    "size": size}  # O(1)
        items = list(inp) if type(inp).__name__ == "Seq" else [inp]  # O(items)
        results = []  # O(1)
        for item in items:  # O(items) iterations
            res, problem = _exec_one(st, entry, item, probe.get("expect"), timeout)  # O(exec cost) per item
            results.append(res)  # O(1)
            if problem:  # O(1)
                out.update(problem)  # O(1)
                break  # O(1)
        else:  # O(1)
            if probe.get("check"):  # O(1)
                try:  # O(1)
                    why = probe["check"](results, st.ctx)  # O(check cost)
                except BaseException as e:  # O(1) | noqa: BLE001
                    why = f"check raised {type(e).__name__}: {e}"  # O(1)
                if why:  # O(1)
                    out.update(status="inconsistent", detail=str(why)[:200])  # O(1)
            if out["status"] == "pass":  # O(1)
                out["detail"] = " ".join(str(x) for x in results[-1][:2]) if results and results[-1] else "ok"  # O(1)
                if entry == "request" or entry == "raw":  # O(1)
                    out["detail"] = f"status {results[-1][1]}"  # O(1)
        if entry in ("request", "raw") and st.port is not None:  # O(1)
            if not _healthy(st):  # O(1) + health check
                _stop_server(st)  # O(1) + stop wait <=2s
                _start_server(st)  # O(1) + server start | restart only on a crash
                if out["status"] == "pass":  # O(1)
                    out.update(status="crash", exc="ServerDied",  # O(1)
                               detail="server stopped responding after this probe; restarted", frames=[])  # O(1)
    finally:  # O(1)
        faulthandler.cancel_dump_traceback_later()  # O(1)
    if out["status"] not in ("pass", "skip") and inp is not None:  # O(1)
        r = repr(inp)  # O(input size) repr
        out["input"] = r if len(r) <= 200_000 else None  # O(1)
    out["elapsed"] = round(time.perf_counter() - t0, 4)  # O(1)
    return out  # O(1)


def worker_main(a) -> int:  # O(steps * step cost) total, steps = messages received
    proto_in = os.fdopen(os.dup(0), "r", encoding="utf-8")  # O(1)
    proto_out = os.fdopen(os.dup(1), "w", encoding="utf-8")  # O(1)
    null = os.open(os.devnull, os.O_RDWR)  # O(1)
    os.dup2(null, 0)  # O(1)
    os.dup2(null, 1)  # O(1)
    sys.stdin = open(os.devnull, "r")  # O(1)
    sys.stdout = open(os.devnull, "w")  # O(1)
    import faulthandler  # O(1)
    faulthandler.enable(file=sys.stderr)  # O(1)

    def send(obj):  # O(len obj)
        proto_out.write(json.dumps(obj) + "\n")  # O(len obj) serialize + write
        proto_out.flush()  # O(1)

    root = Path(a.root).resolve()  # O(1)
    tmp = Path(tempfile.mkdtemp(prefix="probe-"))  # O(1)
    st = _WorkerState()  # O(1)
    try:  # O(1)
        for p in (root / "src", root):  # O(2) iterations
            if p.is_dir() and str(p) not in sys.path:  # O(len sys.path)
                sys.path.insert(1, str(p))  # O(1)
        adapter_path = Path(a.adapter).resolve()  # O(1)
        if str(adapter_path.parent) not in sys.path:  # O(1)
            sys.path.insert(1, str(adapter_path.parent))  # O(1)
        st.adapter = load_module(adapter_path)  # O(adapter size) import
        ad = st.adapter  # O(1)
        st.ctx = types.SimpleNamespace(  # O(1)
            adapter=ad, tmp=tmp, good_input=getattr(ad, "GOOD_INPUT", "1"),  # O(1)
            good_args=list(getattr(ad, "GOOD_ARGS", [])), good_stdin=getattr(ad, "GOOD_STDIN", ""),  # O(1)
            good_path=getattr(ad, "GOOD_PATH", "/"))  # O(1)
        entries = {"call": callable(getattr(ad, "call", None)),  # O(1)
                   "run": callable(getattr(ad, "run", None)),  # O(1)
                   "request": callable(getattr(ad, "request", None)),  # O(1)
                   "raw": callable(getattr(ad, "start_server", None))}  # O(1)
        send({"ready": True, "pid": os.getpid(), "tmp": str(tmp), "entries": entries})  # O(1)
    except BaseException as e:  # O(1) | noqa: BLE001
        send({"ready": False, "error": f"{type(e).__name__}: {e}"})  # O(1)
        return 2  # O(1)
    modules: dict = {}  # O(1)
    for line in proto_in:  # O(steps) iterations
        try:  # O(1)
            msg = json.loads(line)  # O(len line) parse
        except ValueError:  # O(1)
            continue  # O(1)
        if msg.get("op") == "quit":  # O(1)
            break  # O(1)
        try:  # O(1)
            rep = _step(st, msg, modules)  # O(step cost)
        except BaseException as e:  # O(1) | noqa: BLE001
            rep = {"status": "error", "detail": f"worker failure: {type(e).__name__}: {e}"[:200],  # O(1)
                   "size": msg.get("size")}  # O(1)
        rep["server"] = {"starts": st.server_starts, "port": st.port}  # O(1)
        send(rep)  # O(len reply) serialize + write
    _stop_server(st)  # O(1)
    shutil.rmtree(tmp, ignore_errors=True)  # O(tmp files) rmtree
    return 0  # O(1)


# ----------------------------------------------------------------------------- parent side
class Cfg:  # O(1)
    def __init__(self, root: Path, adapter: Path, probes_dir: Path | None, timeout: float):  # O(1)
        self.root, self.adapter, self.probes_dir, self.timeout = root, adapter, probes_dir, timeout  # O(1)
        self.stats_lock = threading.Lock()  # O(1)
        self.killed_pids: list[int] = []  # O(1)
        self.server_starts: dict[int, int] = {}  # O(1)
        self.server_ports: set[int] = set()  # O(1)
        self.workers_started = 0  # O(1)

    def in_repo(self, filename: str) -> bool:  # O(parts) + resolve syscall
        try:  # O(1)
            p = Path(filename).resolve()  # O(parts) resolve syscall
        except (OSError, ValueError):  # O(1)
            return False  # O(1)
        if not p.is_relative_to(self.root) or p.is_relative_to(HERE) or p == self.adapter:  # O(parts)
            return False  # O(1)
        if self.probes_dir and p.is_relative_to(self.probes_dir):  # O(1)
            return False  # O(1)
        return not ({"site-packages", ".venv", "venv", "node_modules", "__pycache__"} & set(p.parts))  # O(parts) set build

    def rel(self, filename: str) -> str:  # O(parts) + resolve syscall
        try:  # O(1)
            return Path(filename).resolve().relative_to(self.root).as_posix()  # O(1)
        except ValueError:  # O(1)
            return Path(filename).as_posix()  # O(1)


class Worker:  # O(1) + process spawn + wait for ready <=60s
    def __init__(self, cfg: Cfg):  # O(1)
        self.cfg = cfg  # O(1)
        self.dead = False  # O(1)
        self.errf = tempfile.NamedTemporaryFile(prefix="probe-worker-", suffix=".err", delete=False)  # O(1)
        cmd = [sys.executable, "-X", "utf8", str(HERE / "probe_run.py"), "--worker",  # O(1)
               "--root", str(cfg.root), "--adapter", str(cfg.adapter)]  # O(1)
        kw: dict = {}  # O(1)
        if IS_WIN:  # O(1)
            kw["creationflags"] = 0x08000000 | 0x00000200  # O(1) | CREATE_NO_WINDOW | NEW_PROCESS_GROUP
        else:  # O(1)
            kw["start_new_session"] = True  # O(1)
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1",  # O(1)
                   SUPERLITE_PROBE_TIMEOUT=str(min(cfg.timeout, ENTRY_TIMEOUT["run"])))  # O(1)
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,  # O(1) + process spawn
                                  stderr=self.errf, text=True, encoding="utf-8", bufsize=1,  # O(1)
                                  cwd=str(cfg.root), env=env, **kw)  # O(1)
        self.q: queue.Queue = queue.Queue()  # O(1)
        threading.Thread(target=self._read, daemon=True).start()  # O(1)
        self.tmp = None  # O(1)
        self.real_pid = None  # O(1)
        self.entries: dict = {}  # O(1)
        with cfg.stats_lock:  # O(1)
            cfg.workers_started += 1  # O(1)
        try:  # O(1)
            ready = self.q.get(timeout=60)  # O(1) blocks <=60s
        except queue.Empty:  # O(1)
            ready = None  # O(1)
        if not ready or not ready.get("ready"):  # O(1)
            err = (ready or {}).get("error") or self._stderr_tail()  # O(len stderr) tail read
            self.close(kill=True)  # O(1)
            raise RuntimeError(f"adapter failed to load: {err.strip()[-400:]}")  # O(1) + kill/wait
        self.real_pid, self.tmp, self.entries = ready["pid"], ready["tmp"], ready["entries"]  # O(1)

    def _read(self):  # O(output lines) total
        try:  # O(1)
            for line in self.p.stdout:  # O(lines) iterations
                try:  # O(1)
                    self.q.put(json.loads(line))  # O(len line) json parse
                except ValueError:  # O(1)
                    pass  # O(1)
        except Exception:  # O(1)
            pass  # O(1)
        self.q.put(None)  # O(1)

    def _err_size(self) -> int:  # O(1)
        try:  # O(1)
            return os.path.getsize(self.errf.name)  # O(1)
        except OSError:  # O(1)
            return 0  # O(1)

    def _stderr_tail(self, off: int = 0) -> str:  # O(stderr bytes from off) total
        try:  # O(1)
            with open(self.errf.name, "rb") as f:  # O(1)
                f.seek(off)  # O(1)
                return f.read().decode("utf-8", "replace")  # O(stderr bytes from off) file read
        except OSError:  # O(1)
            return ""  # O(1)

    def _kill(self):  # O(1) + taskkill spawn
        pids = [x for x in (self.p.pid, self.real_pid) if x]  # O(1)
        kill_tree(self.p)  # O(1) + taskkill spawn
        with self.cfg.stats_lock:  # O(1)
            self.cfg.killed_pids.extend(pids)  # O(1)
        self.dead = True  # O(1)

    def step(self, meta: dict, size, timeout: float) -> dict:  # O(msg + reply + stderr tail) total, blocks <=timeout
        off = self._err_size()  # O(1)
        msg = {"op": "step", "module": meta["module"], "name": meta["name"], "size": size,  # O(1)
               "timeout": timeout}  # O(1)
        t0 = time.perf_counter()  # O(1)
        try:  # O(1)
            self.p.stdin.write(json.dumps(msg) + "\n")  # O(len msg) write
            self.p.stdin.flush()  # O(1)
            rep = self.q.get(timeout=timeout)  # O(1) blocks <=timeout
        except queue.Empty:  # O(1)
            tail = self._stderr_tail(off)  # O(stderr bytes from off) file read
            self._kill()  # O(1) + kill
            return {"status": "hang", "detail": f"no response within {timeout:g}s",  # O(1)
                    "timeout": timeout, "size": size, "frames": frames_from_text(tail),  # O(stderr bytes) frames parse
                    "elapsed": round(time.perf_counter() - t0, 3),  # O(1)
                    "killed_pids": [x for x in (self.p.pid, self.real_pid) if x]}  # O(1)
        except OSError:  # O(1)
            rep = None  # O(1)
        if rep is None:  # O(1)
            code = self.p.poll()  # O(1)
            tail = self._stderr_tail(off)  # O(1)
            self._kill()  # O(1)
            return {"status": "crash", "exc": "WorkerDied", "size": size,  # O(1)
                    "detail": f"worker process exited ({code}) {tail.strip()[-150:]}",  # O(len tail)
                    "frames": frames_from_text(tail), "elapsed": round(time.perf_counter() - t0, 3)}  # O(stderr bytes) frames parse
        srv = rep.pop("server", None)  # O(1)
        if srv:  # O(1)
            with self.cfg.stats_lock:  # O(1)
                self.cfg.server_starts[self.p.pid] = srv["starts"]  # O(1)
                if srv["port"]:  # O(1)
                    self.cfg.server_ports.add(srv["port"])  # O(1)
        if rep["status"] in ("crash", "wrong-status", "leak") and not any(  # O(frames * in_repo)
                self.cfg.in_repo(f) for f, _ in rep.get("frames", [])):  # O(1)
            rep["frames"] = rep.get("frames", []) + frames_from_text(self._stderr_tail(off))  # O(stderr bytes from off) file read + parse
        return rep  # O(1)

    def close(self, kill: bool = False):  # O(1) + wait <=4s + rmtree O(tmp files)
        if not kill:  # O(1)
            try:  # O(1)
                self.p.stdin.write(json.dumps({"op": "quit"}) + "\n")  # O(1)
                self.p.stdin.flush()  # O(1)
                self.p.wait(timeout=4)  # O(1) blocks <=4s
            except Exception:  # O(1)
                kill = True  # O(1)
        if kill and self.p.poll() is None:  # O(1)
            self._kill()  # O(1)
        try:  # O(1)
            self.errf.close()  # O(1)
            os.unlink(self.errf.name)  # O(1)
        except OSError:  # O(1)
            pass  # O(1)
        if self.tmp:  # O(1)
            shutil.rmtree(self.tmp, ignore_errors=True)  # O(tmp files) rmtree


class Pool:  # O(1)
    def __init__(self, cfg: Cfg, size: int):  # O(1)
        self.cfg, self.max = cfg, max(1, size)  # O(1)
        self.idle: queue.Queue = queue.Queue()  # O(1)
        self.all: list[Worker] = []  # O(1)
        self.lock = threading.Lock()  # O(1)

    def acquire(self) -> Worker:  # O(a) a = workers ever spawned, + worker spawn
        try:  # O(1)
            return self.idle.get_nowait()  # O(1)
        except queue.Empty:  # O(1)
            pass  # O(1)
        with self.lock:  # O(1)
            live = sum(1 for w in self.all if w is None or not w.dead)  # O(a) rescans all slots incl. dead workers
            spawn = live < self.max  # O(1)
            if spawn:  # O(1)
                live_slot = len(self.all)  # O(1)
                self.all.append(None)  # O(1) | reserve
        if spawn:  # O(1)
            try:  # O(1)
                w = Worker(self.cfg)  # O(1) + process spawn, <=60s
            except Exception:  # O(1)
                with self.lock:  # O(1)
                    self.all[live_slot] = types.SimpleNamespace(dead=True, close=lambda kill=False: None)  # O(1)
                raise  # O(1)
            with self.lock:  # O(1)
                self.all[live_slot] = w  # O(1)
            return w  # O(1)
        return self.idle.get()  # O(1) blocks until a worker is idle

    def release(self, w: Worker):  # O(1)
        if not w.dead:  # O(1)
            self.idle.put(w)  # O(1)

    def close(self):  # O(a) + per-worker close
        for w in self.all:  # O(a) iterations
            if w is not None and not w.dead:  # O(1)
                w.close()  # O(1) + wait <=4s
            elif w is not None and hasattr(w, "errf"):  # O(1)
                w.close(kill=True)  # O(1)


class Lease:  # O(1)
    def __init__(self, pool: Pool):  # O(1)
        self.pool, self.w = pool, None  # O(1)

    def step(self, meta, size, timeout):  # O(1) + step
        try:  # O(1)
            if self.w is None:  # O(1)
                self.w = self.pool.acquire()  # O(1)
        except RuntimeError as e:  # O(1)
            return {"status": "error", "detail": str(e)[:200], "size": size}  # O(1)
        out = self.w.step(meta, size, timeout)  # O(step)
        if self.w.dead:  # O(1)
            self.w.close(kill=True)  # O(1)
            self.w = None  # O(1)
        return out  # O(1)

    def close(self):  # O(1)
        if self.w is not None:  # O(1)
            self.pool.release(self.w)  # O(1)
            self.w = None  # O(1)


def timeout_for(meta: dict, cfg: Cfg) -> float:  # O(1)
    return min(ENTRY_TIMEOUT[meta["entry"]], cfg.timeout)  # O(1)


def run_probe(pool: Pool, cfg: Cfg, meta: dict) -> dict:  # O(z * step) total, z = size steps
    lease = Lease(pool)  # O(1)
    res = {"id": meta["id"], "category": meta["category"], "entry": meta["entry"],  # O(1)
           "status": "pass", "size": None, "detail": "", "elapsed": 0.0}  # O(1)
    t0 = time.perf_counter()  # O(1)
    prev = None  # O(1)
    try:  # O(1)
        for size in meta["sizes"]:  # O(z) iterations
            out = lease.step(meta, size, timeout_for(meta, cfg))  # O(step) per iteration
            if out["status"] != "pass":  # O(1)
                res.update(out)  # O(1)
                res["prev_pass_size"] = prev  # O(1)
                res["probe_meta"] = meta  # O(1)
                break  # O(1)
            prev = size  # O(1)
            res.update(status="pass", detail=out.get("detail", ""), size=size)  # O(1)
    finally:  # O(1)
        lease.close()  # O(1)
    res["elapsed"] = round(time.perf_counter() - t0, 3)  # O(1)
    return res  # O(1)


def group_key(res: dict, cfg: Cfg) -> tuple[str, tuple[str, int] | None]:  # O(frames * in_repo)
    st = res["status"]  # O(1)
    top = next(((f, n) for f, n in res.get("frames", []) if cfg.in_repo(f)), None)  # O(frames * path parts)
    where = f"{cfg.rel(top[0])}:{top[1]}" if top else f"{res['category']}/{res['entry']}"  # O(1)
    if st == "crash":  # O(1)
        return f"crash|{res.get('exc')}|{where}", top  # O(1)
    if st == "hang":  # O(1)
        return f"hang|{where}", top  # O(1)
    if st == "leak":  # O(1)
        return f"leak|{res.get('leak')}|{where}", top  # O(1)
    if st == "wrong-status":  # O(1)
        return f"wrong-status|{res.get('http')}|{where}", top  # O(1)
    return f"{st}|{res['id']}", top  # O(1)


# ----------------------------------------------------------------------------- regression tests
REGRESSION_TEMPLATE = '''"""Generated by probe_run.py: {category} {kind} ({key}).
Minimal input shrunk from {orig_size}; probe {probe_id}. Fix the code, keep this test."""
import importlib.util
import json
import pathlib
import socket
import sys
import threading

ROOT = pathlib.Path(__file__).resolve().parents[2]
ADAPTER = ROOT / {adapter_rel!r}
ENTRY = {entry!r}
INPUT = {input_text}
EXPECT = {expect!r}
TIMEOUT = {timeout!r}
TRACE = "Traceback (most recent call last)"


def _load():
    for p in (ROOT / "src", ROOT, ADAPTER.parent):
        sys.path.insert(0, str(p))
    spec = importlib.util.spec_from_file_location("_probe_adapter", ADAPTER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _server(ad):
    r = ad.start_server()
    if isinstance(r, tuple):
        port, stop = r[0], (r[1] if len(r) > 1 else None)
    elif hasattr(r, "server_address"):
        port, stop = r.server_address[1], getattr(r, "shutdown", None)
    else:
        port, stop = int(r), None
    ad.PORT = port
    return port, stop


def _problem(ad):
    expected = tuple(getattr(ad, "EXPECTED_ERRORS", (ValueError,)))
    stop = None
    try:
        if ENTRY == "call":
            ad.call(INPUT)
        elif ENTRY == "run":
            rc, out, err = ad.run(*INPUT)
            if rc < 0 or rc >= 0xC0000000 or TRACE in str(err):
                return f"CLI crashed: exit {{rc}} {{str(err)[-200:]}}"
        else:
            port, stop = _server(ad)
            if ENTRY == "request":
                status = ad.request(*INPUT)[0]
            else:
                with socket.create_connection(("127.0.0.1", port), 5) as s:
                    s.sendall(INPUT)
                    s.shutdown(socket.SHUT_WR)
                    head = s.recv(64)
                status = int(head[9:12]) if head[:5] == b"HTTP/" else 0
            allowed = EXPECT.get("status") if EXPECT else None
            if allowed and status not in allowed:
                return f"status {{status}} not in {{allowed}}"
            if not allowed and 500 <= status < 600 and status not in (501, 505):
                return f"status {{status}}"
    except expected:
        return None
    finally:
        if stop:
            threading.Thread(target=stop, daemon=True).start()
    return None


def test_{test_name}():
    ad = _load()
    box = {{}}

    def go():
        try:
            box["p"] = _problem(ad)
        except BaseException as e:  # noqa: BLE001
            box["p"] = f"{{type(e).__name__}}: {{e}}"

    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(TIMEOUT)
    assert not t.is_alive(), f"hang: no result within {{TIMEOUT}}s"
    assert box["p"] is None, box["p"]
'''  # O(1)


_reg_lock = threading.Lock()  # O(1)


def write_regression(cfg: Cfg, group: dict, rep: dict, shrunk_size) -> str | None:  # O(t * B + len text) total, serialized by global lock
    with _reg_lock:  # O(1)
        return _write_regression(cfg, group, rep)  # O(t * B + len text) under lock


def _write_regression(cfg: Cfg, group: dict, rep: dict) -> str | None:  # O(t log t + t * B) total, t = existing tests, B = bytes per test
    meta = rep.get("probe_meta") or {}  # O(1)
    if not meta.get("regress", True) or meta.get("has_check") or not rep.get("input"):  # O(1)
        return None  # O(1)
    if rep["status"] == "leak" or rep["status"] == "inconsistent":  # O(1)
        return None  # O(1)
    expect = None  # O(1)
    try:  # O(1)
        mod = load_module(Path(meta["module"]))  # O(module size) first load, cached after
        expect = next(p for p in mod.PROBES if p["name"] == meta["name"]).get("expect")  # O(P_m) linear scan of module probes
    except Exception:  # O(1)
        pass  # O(1)
    cat = group["category"]  # O(1)
    out_root = getattr(cfg, "out_root", cfg.root)  # O(1)
    outdir = out_root / "tests" / "regression"  # O(1)
    existing = sorted(outdir.glob(f"test_probe_{cat}_*.py")) if outdir.is_dir() else []  # O(t log t) glob + sort
    n = 1 + max([int(re.search(r"_(\d+)\.py$", p.name).group(1)) for p in existing] or [0])  # O(t) regex per existing test
    text = REGRESSION_TEMPLATE.format(  # O(len template + len input) format
        category=cat, kind=group["kind"], key=group["key"].replace('"', "'"),  # O(1)
        orig_size=group.get("size"), probe_id=group["probe"], adapter_rel=cfg.rel(str(cfg.adapter)),  # O(1)
        entry=meta["entry"], input_text=rep["input"], expect=expect,  # O(1)
        timeout=timeout_for(meta, cfg), test_name=f"probe_{cat}_{n}")  # O(1)
    def body(t):  # O(len t)
        return t[t.index("ROOT ="):t.index("def test_")]  # O(len t) two index scans

    for p in existing:  # O(t) iterations | same content already written: reuse it
        if body(p.read_text(encoding="utf-8")) == body(text):  # O(B) read file + O(len text) body(text) recomputed every iteration
            return p.relative_to(out_root).as_posix()  # O(1)
    outdir.mkdir(parents=True, exist_ok=True)  # O(1)
    path = outdir / f"test_probe_{cat}_{n}.py"  # O(1)
    path.write_text(text, encoding="utf-8")  # O(len text) write
    return path.relative_to(out_root).as_posix()  # O(1)


def shrink_group(pool: Pool, cfg: Cfg, group: dict, rep: dict) -> tuple[object, dict]:  # O(log size * step) total, <=5s budget
    """Bisect the failing size down; returns (smallest failing size, its failing result)."""  # O(1)
    meta, best, key = rep["probe_meta"], rep, group["key"]  # O(1)
    hi = rep["size"]  # O(1)
    if hi is None or not isinstance(hi, int) or rep["status"] in ("error",):  # O(1)
        return hi, best  # O(1)
    lo = rep.get("prev_pass_size")  # O(1)
    lo = lo if isinstance(lo, int) else 0  # O(1)
    lease = Lease(pool)  # O(1)
    t0 = time.perf_counter()  # O(1)
    try:  # O(1)
        while hi - lo > 1 and time.perf_counter() - t0 < SHRINK_BUDGET:  # O(log size) iterations
            mid = (lo + hi) // 2  # O(1)
            out = lease.step(meta, mid, timeout_for(meta, cfg))  # O(step) per iteration
            out.setdefault("id", rep["id"])  # O(1)
            out.update(category=rep["category"], entry=rep["entry"], probe_meta=meta)  # O(1)
            if out["status"] != "pass" and out["status"] != "skip" and group_key(out, cfg)[0] == key:  # O(frames * in_repo)
                hi, best = mid, out  # O(1)
            else:  # O(1)
                lo = mid  # O(1)
    finally:  # O(1)
        lease.close()  # O(1)
    return hi, best  # O(1)


# ----------------------------------------------------------------------------- incidents
INCIDENT_SL = HERE.parent / "scripts" / "sl"  # O(1)
_append_lock = threading.Lock()  # O(1)


def _append_incident(log: Path, file: str, line: int, context: str, kind: str,  # O(1) + file append
                     resolution: str | None) -> None:  # O(1)
    entry = {"file": file, "line": line, "context": context, "agent": "probe_run", "kind": kind}  # O(1)
    if resolution:  # O(1)
        entry["resolution"] = resolution  # O(1)
    entry["time"] = time.strftime("%H:%M:%S")  # O(1)
    log.parent.mkdir(parents=True, exist_ok=True)  # O(1)
    data = (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")  # O(len entry)
    with _append_lock:  # O(1)
        fd = os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)  # O(1) syscall
        try:  # O(1)
            os.write(fd, data)  # O(1)
        finally:  # O(1)
            os.close(fd)  # O(1)


def _log_incidents(log: Path, jobs: list[tuple]) -> None:  # O(j + bytes) total, j = jobs; one locked append, no spawns
    """Write incidents (file, line, context, kind, resolution) in one locked append through the
    incident module, imported in process (no sl or Python spawn per entry); fall back to direct
    appends when it cannot be imported or rejects an entry."""  # O(1)
    if not jobs:  # O(1)
        return  # O(1)
    try:  # O(1)
        spec = importlib.util.spec_from_file_location(  # O(1) by path, under a private name:
            "_superlite_incident", INCIDENT_SL.parent / "incident.py")  # no sys.path change, no clash
        incident = importlib.util.module_from_spec(spec)  # O(1)
        spec.loader.exec_module(incident)  # O(module size)
        incident.append(str(log), [incident.make_entry(f, line, ctx, agent="probe_run", kind=kind,  # O(j + bytes) one locked append
                                                       resolution=res)  # O(1)
                                   for f, line, ctx, kind, res in jobs])  # O(j) iterations
        return  # O(1)
    except Exception:  # missing or broken incident module, a rejected entry, I/O | O(1)
        pass  # O(1)
    for f, line, ctx, kind, res in jobs:  # O(j) iterations (fallback only)
        ctx = ctx if len(ctx) <= 200 else ctx[:199] + "…"  # O(len ctx)
        _append_incident(log, f, line, ctx, kind, res)  # O(1) + file append


def _log_incident(log: Path, file: str, line: int, context: str, kind: str,  # O(1) one append
                  resolution: str | None = None) -> None:  # O(1)
    """Write one incident (see _log_incidents)."""  # O(1)
    _log_incidents(log, [(file, line, context, kind, resolution)])  # O(1)


def read_log(log: Path) -> list[dict]:  # O(log bytes) total
    out = []  # O(1)
    if log.is_file():  # O(1)
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():  # O(log bytes) read + split; O(e) iterations
            try:  # O(1)
                out.append(json.loads(line))  # O(len line) parse
            except ValueError:  # O(1)
                pass  # O(1)
    return out  # O(1)


# ----------------------------------------------------------------------------- files, cache
def norm_file(root: Path, f: str) -> str:  # O(len f) + resolve syscall
    f = f.strip().replace("\\", "/")  # O(1)
    p = Path(f)  # O(1)
    if p.is_absolute():  # O(1)
        try:  # O(1)
            return p.resolve().relative_to(root).as_posix()  # O(1)
        except ValueError:  # O(1)
            return p.as_posix()  # O(1)
    return re.sub(r"^(\./)+", "", f)  # O(len f) regex


def discover_files(root: Path, adapter: Path, probes_dir: Path | None) -> list[str]:  # O(T log T) total, T = tree entries
    base = root / "src" if (root / "src").is_dir() else root  # O(1)
    found = []  # O(1)
    for dirpath, dirnames, filenames in os.walk(base):  # O(T) iterations (dirs)
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)  # O(d log d) sort dirs
        for fn in sorted(filenames):  # O(n log n) sort + O(n) iterations of files in dir
            p = Path(dirpath) / fn  # O(1)
            if p.suffix in CODE_EXT and p.resolve() != adapter and not (  # O(1) + 2 resolve syscalls per file
                    probes_dir and p.resolve().is_relative_to(probes_dir)):  # O(1)
                found.append(p.relative_to(root).as_posix())  # O(depth)
    return found  # O(1)


def commit_files(root: Path, sha: str, work: Path | None = None) -> list[str]:  # O(n) + git spawn
    r = subprocess.run(["git", "-C", str(root), "diff-tree", "--no-commit-id", "--name-only", "-r",  # O(1) + process spawn (git)
                        "--root", "-z", "--relative", sha], capture_output=True, timeout=60)  # O(1)
    if r.returncode != 0:  # O(1)
        raise RuntimeError(f"git diff-tree failed: {r.stderr.decode(errors='replace').strip()}")  # O(1)
    names = [n for n in r.stdout.decode("utf-8", "replace").split("\0") if n]  # O(output bytes) decode + split
    keep = []  # O(1)
    for n in names:  # O(n) iterations
        p = Path(n)  # O(1)
        if p.suffix in CODE_EXT and not (set(p.parts[:-1]) & SKIP_DIRS) and ((work or root) / n).exists():  # O(depth) + stat
            keep.append(p.as_posix())  # O(1)
    return keep  # O(1)


def sha_bytes(*parts: bytes) -> str:  # O(total bytes)
    h = hashlib.sha256()  # O(1)
    for p in parts:  # O(parts) iterations
        h.update(hashlib.sha256(p).digest())  # O(bytes) per part
    return h.hexdigest()  # O(1)


def load_cache(path: Path) -> dict:  # O(cache bytes)
    try:  # O(1)
        d = json.loads(path.read_text(encoding="utf-8"))  # O(cache bytes) read + parse
        return d if isinstance(d.get("entries"), dict) else {"version": 1, "entries": {}}  # O(1)
    except (OSError, ValueError, AttributeError):  # O(1)
        return {"version": 1, "entries": {}}  # O(1)


def save_cache(path: Path, new_entries: dict) -> None:  # O(cache bytes) read + merge + write
    cache = load_cache(path)  # O(cache bytes) re-read | re-read: another background run may have written meanwhile
    cache["entries"].update(new_entries)  # O(1)
    path.parent.mkdir(parents=True, exist_ok=True)  # O(1)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")  # O(1)
    tmp.write_text(json.dumps(cache, indent=1), encoding="utf-8")  # O(cache bytes) write
    os.replace(tmp, path)  # O(1)


# ----------------------------------------------------------------------------- main run
def select_categories(a, catalog) -> list[str]:  # O(c + p)
    if a.categories:  # O(1)
        return [c.strip() for c in a.categories.split(",") if c.strip()]  # O(len categories)
    if a.app_type:  # O(1)
        t = APP_ALIASES.get(a.app_type.lower(), a.app_type.lower())  # O(1)
        if t not in APP_TYPES:  # O(1)
            raise SystemExit(f"probe_run: unknown --app-type {a.app_type!r}; "  # O(1)
                             f"use one of {', '.join(APP_TYPES)}")  # O(1)
        return list(APP_TYPES[t])  # O(1)
    extra = sorted({p["category"] for p in catalog} - set(CATEGORY_ORDER))  # O(p) set comp + sort
    return CATEGORY_ORDER + extra  # O(c)


def verified_index(entries: list[dict], root: Path) -> dict:  # O(e) total, regex per entry
    idx: dict = {}  # O(1)
    for e in entries:  # O(e) iterations
        if e.get("kind") != "verified":  # O(1)
            continue  # O(1)
        m = re.match(r"probe (\w+)", str(e.get("context", "")))  # O(len ctx) regex
        if m:  # O(1)
            sm = re.search(r"\[sha:([0-9a-f]+)\]", e.get("context", ""))  # O(len ctx) regex
            idx.setdefault((m.group(1), norm_file(root, str(e.get("file", "")))), []).append(  # O(1)
                (e.get("agent"), sm.group(1) if sm else None))  # O(1)
    return idx  # O(1)


def is_verified(idx: dict, cat: str, f: str, sha: str) -> bool:  # O(agents per key)
    for agent, s in idx.get((cat, f), []):  # O(1)
        if agent != "probe_run" or (s and sha.startswith(s)):  # O(1)
            return True  # O(1)
    return False  # O(1)


def checkout_commit(root: Path, sha: str) -> tuple[Path, Path | None]:  # O(tree files) + git spawn (worktree add, else archive + tar extract)
    """Materialise `sha` in a fresh temp dir: `git worktree add --detach`, else `git archive | tar`.
    Returns (tree, worktree_repo_or_None); remove it with remove_checkout."""  # O(1)
    tree = Path(tempfile.mkdtemp(prefix="probe-commit-")).resolve() / "tree"  # O(1) mkdtemp
    r = subprocess.run(["git", "-C", str(root), "worktree", "add", "--detach", str(tree), sha],  # O(1) + process spawn (git worktree add, <=120s)
                       capture_output=True, timeout=120)  # O(1)
    if r.returncode == 0:  # O(1)
        return tree, root  # O(1)
    shutil.rmtree(tree.parent, ignore_errors=True)  # O(tree files) rmtree
    tree = Path(tempfile.mkdtemp(prefix="probe-commit-")).resolve() / "tree"  # O(1)
    tree.mkdir()  # O(1)
    arc = subprocess.run(["git", "-C", str(root), "archive", "--format=tar", sha],  # O(1) + process spawn (git archive, <=120s)
                         capture_output=True, timeout=120)  # O(1)
    if arc.returncode != 0:  # O(1)
        shutil.rmtree(tree.parent, ignore_errors=True)  # O(1)
        raise RuntimeError(f"cannot check out {sha}: {arc.stderr.decode(errors='replace').strip()}")  # O(1)
    import io  # O(1)
    import tarfile  # O(1)
    with tarfile.open(fileobj=io.BytesIO(arc.stdout)) as tf:  # O(archive bytes) open
        tf.extractall(tree, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))  # O(archive bytes) extract
    return tree, None  # O(1)


def remove_checkout(tree: Path, repo: Path | None) -> None:  # O(tree files) + 2 git spawns
    if repo is not None:  # O(1)
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(tree)],  # O(1) + process spawn (git worktree remove)
                       capture_output=True, timeout=120)  # O(1)
        subprocess.run(["git", "-C", str(repo), "worktree", "prune"], capture_output=True, timeout=60)  # O(1) + process spawn (git worktree prune)
    shutil.rmtree(tree.parent, ignore_errors=True)  # O(tree files) rmtree


def _python_adapter_required(root: Path) -> bool:  # O(file)
    """Missing adapter.py fails only for Python, or when test-cmd.json is absent."""
    cfg = root / ".superlite" / "test-cmd.json"  # O(1)
    if not cfg.is_file():  # O(1)
        return True  # O(1)
    try:  # O(1)
        data = json.loads(cfg.read_text(encoding="utf-8-sig"))  # O(file)
    except (OSError, ValueError):  # O(1)
        return True  # O(1)
    if not isinstance(data, dict):  # O(1)
        return True  # O(1)
    lang = str(data.get("language") or "python").strip().lower()  # O(1)
    return lang in ("", "python")  # O(1)


def run_main(a) -> int:  # O(checkout + _run_main) total; one git checkout per --checkpoint run
    root = Path(a.root or os.getcwd()).resolve()  # O(1)
    adapter = Path(a.adapter).resolve() if a.adapter else root / ".superlite" / "probes" / "adapter.py"  # O(1)
    if not adapter.is_file():  # O(1)
        if a.adapter or _python_adapter_required(root):  # an explicit --adapter still must exist | O(file)
            print(f"probe_run: adapter not found: {adapter}", file=sys.stderr)  # O(1)
            return 2  # O(1)
        return 0  # seam-only project; nothing to probe | O(1)
    if not a.commit:  # O(1)
        return _run_main(a, root, root, adapter)  # O(_run_main)
    # --checkpoint: probe that checkpoint's tree, not the working tree. The adapter is usually untracked
    # (.superlite/), so a copy goes to the same relative place in the checkout for its relative ROOT.
    try:  # O(1)
        tree, repo = checkout_commit(root, a.commit)  # O(tree files) + git spawn
    except (RuntimeError, OSError, subprocess.SubprocessError) as e:  # O(1)
        print(f"probe_run: {e}", file=sys.stderr)  # O(1)
        return 2  # O(1)
    try:  # O(1)
        run_adapter = adapter  # O(1)
        try:  # O(1)
            rel = adapter.relative_to(root)  # O(1)
            run_adapter = tree / rel  # O(1)
            run_adapter.parent.mkdir(parents=True, exist_ok=True)  # O(1)
            shutil.copy2(adapter, run_adapter)  # O(adapter bytes) copy
        except ValueError:  # O(1)
            pass  # O(1)
        return _run_main(a, root, tree, run_adapter)  # O(_run_main)
    finally:  # O(1)
        remove_checkout(tree, repo)  # O(tree files) + 2 git spawns


def _run_main(a, root: Path, work: Path, adapter: Path) -> int:  # O(c*p + f*bytes + p log p + g*(t*B + shrink)) total + process spawns and probe I/O
    """Probe the tree at `work` (== root, or a checkout of --checkpoint); logs, cache, results and
    regression tests always go under `root`."""  # O(1)
    t_start = time.time()  # O(1)
    probes_dir = Path(a.probes).resolve() if a.probes else None  # O(1)
    if probes_dir and not probes_dir.is_dir():  # O(1)
        print(f"probe_run: --probes is not a directory: {probes_dir}", file=sys.stderr)  # O(1)
        return 2  # O(1)

    def under_root(p: str) -> Path:  # O(1)
        return Path(p) if Path(p).is_absolute() else root / p  # O(1)

    out_path, log_path = under_root(a.out), under_root(a.log)  # O(1)
    # Cache beside the results file (session-local when --out is under sessions/<id>/).
    cache_path = out_path.parent / "cache.json"  # O(1)
    cfg = Cfg(work, adapter, probes_dir, a.timeout)  # O(1)
    cfg.out_root = root  # O(1)

    if a.commit:  # O(1)
        try:  # O(1)
            files = commit_files(root, a.commit, work)  # O(n) + git spawn
        except (RuntimeError, OSError) as e:  # O(1)
            print(f"probe_run: {e}", file=sys.stderr)  # O(1)
            return 2  # O(1)
    elif a.files:  # O(1)
        files = [norm_file(root, f) for f in a.files.split(",") if f.strip()]  # O(f) iterations
    else:  # O(1)
        files = discover_files(work, adapter, probes_dir) or [cfg.rel(str(adapter))]  # O(T log T) tree walk

    catalog, load_errors = load_catalog(probes_dir)  # O(m + p) + module exec
    try:  # O(1)
        selected = select_categories(a, catalog)  # O(1)
    except SystemExit as e:  # O(1)
        print(e, file=sys.stderr)  # O(1)
        return 2  # O(1)

    pack_digest = sha_bytes(*[p.read_bytes() for p in probe_modules(probes_dir)])  # O(probe module bytes) read + hash
    adapter_bytes = adapter.read_bytes()  # O(adapter bytes) read
    file_sha = {f: sha_bytes((work / f).read_bytes() if (work / f).is_file() else b"<missing>",  # O(f * (file + adapter + pack bytes)) adapter/pack rehashed per file
                             adapter_bytes, pack_digest.encode()) for f in files}  # O(f) iterations
    log_entries = read_log(log_path)  # O(log bytes)
    vidx = verified_index(log_entries, root)  # O(e)
    cache = load_cache(cache_path)["entries"]  # O(cache bytes)

    nworkers = a.workers or os.cpu_count() or 2  # O(1)
    pool = Pool(cfg, nworkers)  # O(1)
    results_run_seconds = 0.0  # O(1)
    results: dict = {"version": 1, "root": str(root), "adapter": cfg.rel(str(adapter)),  # O(1)
                     **({"commit": a.commit} if a.commit else {}),  # O(1)
                     "files": files, "selected_categories": selected, "categories": {},  # O(1)
                     "probes": [], "failures": [], "load_errors": load_errors}  # O(1)
    try:  # O(1)
        try:  # O(1)
            w0 = pool.acquire()  # O(1) + worker spawn
        except RuntimeError as e:  # O(1)
            print(f"probe_run: {e}", file=sys.stderr)  # O(1)
            return 2  # O(1)
        entries = dict(w0.entries)  # O(entries)
        pool.release(w0)  # O(1)

        pending: dict[str, list[str]] = {}  # O(1)
        for cat in selected:  # O(c) iterations
            cprobes = [p for p in catalog if p["category"] == cat and entries.get(p["entry"])]  # O(p) catalog scan per category
            if not cprobes:  # O(1)
                results["categories"][cat] = {"status": "n/a", "probes": 0,  # O(1)
                                              "reason": "no probe applies to this adapter's entries"}  # O(1)
                continue  # O(1)
            todo, skipped_v, cached = [], [], []  # O(1)
            for f in files:  # O(f) iterations
                sha = file_sha[f]  # O(1)
                if f"{cat}|{f}|{sha}" in cache:  # O(len key) dict lookup
                    cached.append(f)  # O(1)
                elif is_verified(vidx, cat, f, sha):  # O(1)
                    skipped_v.append(f)  # O(1)
                else:  # O(1)
                    todo.append(f)  # O(1)
            if todo:  # O(1)
                pending[cat] = todo  # O(1)
            elif cached:  # O(1)
                results["categories"][cat] = {"status": "pass", "cached": True, "probes": 0,  # O(1)
                                              "files": cached, "skipped_verified": skipped_v}  # O(1)
            else:  # O(1)
                results["categories"][cat] = {"status": "skipped", "probes": 0, "files": skipped_v,  # O(1)
                                              "reason": "verified" if skipped_v else "no files"}  # O(1)

        tasks = [p for cat in pending for p in catalog  # O(|pending| * p) catalog rescan per pending category
                 if p["category"] == cat and entries.get(p["entry"])]  # O(1)
        order = {p["id"]: i for i, p in enumerate(tasks)}  # O(p)
        done: list[dict] = []  # O(1)
        t_run = time.time()  # O(1)
        if tasks:  # O(1)
            with cf.ThreadPoolExecutor(max_workers=min(nworkers, len(tasks))) as ex:  # O(1) + thread start
                done = list(ex.map(lambda m: run_probe(pool, cfg, m), tasks))  # O(p * probe cost / w) wall-clock, probes run in parallel
        done.sort(key=lambda r: order[r["id"]])  # O(p log p)
        results_run_seconds = round(time.time() - t_run, 3)  # O(1)

        # group failures by root cause
        groups: dict[str, dict] = {}  # O(1)
        for r in done:  # O(p) iterations
            if r["status"] in ("pass", "skip"):  # O(1)
                continue  # O(1)
            key, top = group_key(r, cfg)  # O(frames * in_repo)
            g = groups.setdefault(key, {"group_id": f"g{len(groups) + 1}", "key": key, "category": r["category"], "kind": r["status"],  # O(1)
                                        "exc": r.get("exc"), "count": 0, "probe": r["id"],  # O(1)
                                        "size": r.get("size"), "detail": r.get("detail", ""),  # O(1)
                                        "file": cfg.rel(top[0]) if top else None,  # O(1)
                                        "line": top[1] if top else 0, "probes": [], "_rep": r})  # O(1)
            g["count"] += 1  # O(1)
            g["probes"].append(r["id"])  # O(1)
            r["_group"] = groups[key]  # O(1)

        # shrink + regression tests, in parallel (one lease per group)
        def finish_group(g):  # O(shrink + regression) per group
            rep = g.pop("_rep")  # O(1)
            if rep["status"] in ("crash", "hang", "wrong-status", "leak") and "probe_meta" in rep:  # O(1)
                size, best = shrink_group(pool, cfg, g, rep)  # O(log size * step) <=5s
            else:  # O(1)
                size, best = rep.get("size"), rep  # O(1)
            g["shrunk_size"] = size  # O(1)
            try:  # O(1)
                g["regression_test"] = write_regression(cfg, g, best, size)  # O(t * B) regression write
            except Exception as e:  # O(1) | a failed test write must not lose the incident
                g["regression_test"] = None  # O(1)
                g["regression_error"] = f"{type(e).__name__}: {e}"  # O(1)
            return g  # O(1)

        glist = list(groups.values())  # O(g)
        if glist:  # O(1)
            with cf.ThreadPoolExecutor(max_workers=min(nworkers, len(glist))) as ex:  # O(1) + thread start
                glist = list(ex.map(finish_group, glist))  # O(g * (shrink + regression) / w)
        results["failures"] = glist  # O(1)
    finally:  # O(1)
        pool.close()  # O(1)

    # per-category status, verified entries, cache
    failed_files: dict[str, set] = {}  # O(1)
    unattributed: set = set()  # O(1)
    for g in results["failures"]:  # O(g) iterations
        (failed_files.setdefault(g["category"], set()).add(g["file"]) if g["file"]  # O(1)
         else unattributed.add(g["category"]))  # O(1)
    new_cache, verified_jobs = {}, []  # O(1)
    for cat, todo in pending.items():  # O(c) iterations
        cres = [r for r in done if r["category"] == cat]  # O(p) rescan of done per category
        bad = [r for r in cres if r["status"] not in ("pass", "skip")]  # O(p_c)
        entry_kinds = sorted({r["entry"] for r in cres})  # O(p_c log p_c)
        status = "fail" if bad else "pass"  # O(1)
        results["categories"][cat] = {"status": status, "cached": False, "probes": len(cres),  # O(1)
                                      "skipped_probes": sum(r["status"] == "skip" for r in cres),  # O(p_c)
                                      "failed_probes": len(bad), "files": todo, "entries": entry_kinds}  # O(1)
        has_error = any(r["status"] == "error" for r in cres)  # O(p_c)
        for f in todo:  # O(f) iterations
            if has_error or cat in unattributed or f in failed_files.get(cat, set()):  # O(1)
                continue  # O(1)
            if any(r["status"] == "skip" for r in cres) and all(r["status"] == "skip" for r in cres):  # O(p_c) loop-invariant, repeated per file
                continue  # O(1)
            new_cache[f"{cat}|{f}|{file_sha[f]}"] = {"time": int(time.time()), "probes": len(cres)}  # O(1)
            maxsize = max([s for r in cres for s in [r.get("size")] if isinstance(s, int)] or [0])  # O(p_c) loop-invariant, repeated per file
            verified_jobs.append((  # O(1)
                f, f"probe {cat}: {len(cres)} probes pass ({'/'.join(entry_kinds)}), "  # O(1)
                   f"sizes up to {maxsize} [sha:{file_sha[f][:12]}]"))  # O(1)
    results["probes"] = []  # O(1)
    for r in done:  # O(p) iterations
        g = r.get("_group")  # O(1)
        entry = {k: v for k, v in r.items()  # O(keys) copy
                 if k not in ("probe_meta", "input", "frames", "prev_pass_size", "_group")}  # O(1)
        entry.update(file=g["file"] if g else None, line=g["line"] if g else None,  # O(1)
                     group_id=g["group_id"] if g else None, input_size=r.get("size"),  # O(1)
                     message=r.get("detail", ""))  # O(1)
        results["probes"].append(entry)  # O(1)

    # incidents: one per group (deduplicated against the log), one verified per category+file
    seen = {(e.get("file"), e.get("line"), e.get("context")) for e in log_entries}  # O(e) set build
    jobs = []  # O(1)
    for g in results["failures"]:  # O(g) iterations
        what = g.get("exc") or g["kind"]  # O(1)
        ctx = (f"probe {g['category']} {g['kind']} {what}: {g['detail'][:70]} "  # O(len detail)
               f"(size {g['shrunk_size']}, {g['count']} probe(s), e.g. {g['probe']})")  # O(1)
        if g.get("regression_test"):  # O(1)
            ctx += f"; test {g['regression_test']}"  # O(1)
        ctx = ctx if len(ctx) <= 200 else ctx[:199] + "…"  # O(1)
        f = g["file"] or cfg.rel(str(adapter))  # O(1)
        g["incident"] = (f, g["line"], ctx)  # O(1)
        kind = {"crash": "exception", "leak": "exception", "hang": "perf"}.get(g["kind"], "test-failure")  # O(1)
        if (f, g["line"], ctx) not in seen:  # O(1)
            jobs.append((f, g["line"], ctx, kind, "open"))  # O(1)
    for f, ctx in verified_jobs:  # O(v) iterations
        jobs.append((f, 0, ctx, "verified", None))  # O(1)
    if jobs:  # O(1)
        _log_incidents(log_path, jobs)  # O(j + bytes) one locked append, no spawns
    for g in results["failures"]:  # O(g) iterations
        g["incident"] = {"file": g["incident"][0], "line": g["incident"][1]}  # O(1)
    results["verified_logged"] = [{"file": f, "category": c.split(":")[0].split()[1]} for f, c in verified_jobs]  # O(v)
    if new_cache:  # O(1)
        save_cache(cache_path, new_cache)  # O(1)

    results["stats"] = {  # O(w + ports log ports)
        "elapsed": round(time.time() - t_start, 3), "run_seconds": results_run_seconds,  # O(1)
        "probes_run": len(done),  # O(1)
        "workers_started": cfg.workers_started, "server_starts": sum(cfg.server_starts.values()),  # O(1)
        "server_ports": sorted(cfg.server_ports), "killed_pids": cfg.killed_pids,  # O(1)
        "failure_groups": len(results["failures"]),  # O(1)
    }  # O(1)
    out_path.parent.mkdir(parents=True, exist_ok=True)  # O(1)
    out_path.write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")  # O(results size) json dump + write
    nfail = len(results["failures"]) + sum(r["status"] == "error" for r in done)  # O(p)
    print(f"probe_run: {len(done)} probes, {len(results['failures'])} failure group(s), "  # O(1)
          f"{results['stats']['elapsed']}s -> {out_path}")  # O(1)
    return 1 if nfail else 0  # O(1)


def spawn_background(argv: list[str]) -> int:  # O(argv) + process spawn
    args = [x for x in argv if x != "--background"]  # O(1)
    kw: dict = {}  # O(1)
    if IS_WIN:  # O(1)
        kw["creationflags"] = 0x00000200 | 0x08000000  # O(1) NEW_GROUP | CREATE_NO_WINDOW; never DETACHED, it overrides NO_WINDOW
    else:  # O(1)
        kw["start_new_session"] = True  # O(1)
    p = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), *args],  # O(1) + process spawn
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,  # O(1)
                         stderr=subprocess.DEVNULL, close_fds=True, **kw)  # O(1)
    print(json.dumps({"started": p.pid}))  # O(1)
    return 0  # O(1)


def main(argv: list[str] | None = None) -> int:  # O(argv) + run
    argv = sys.argv[1:] if argv is None else argv  # O(1)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)  # O(1)
    ap.add_argument("--adapter", help="adapter module (default <root>/.superlite/probes/adapter.py)")  # O(1)
    ap.add_argument("--root", help="project root (default: cwd)")  # O(1)
    ap.add_argument("--app-type", help=f"one of {', '.join(APP_TYPES)}")  # O(1)
    ap.add_argument("--probes", help="directory of project probe modules")  # O(1)
    ap.add_argument("--categories", help="comma list; overrides --app-type")  # O(1)
    ap.add_argument("--files", help="comma list of files under test, relative to --root")  # O(1)
    ap.add_argument("--checkpoint", "--commit", dest="commit",  # --commit: old name, kept as an alias | O(1)
                    help="probe only the files this checkpoint (or commit) touched")  # O(1)
    ap.add_argument("--timeout", type=float, default=10.0, help="cap on per-probe timeouts (s)")  # O(1)
    ap.add_argument("--workers", type=int, default=0, help="default: CPU count")  # O(1)
    ap.add_argument("--out", default=".superlite/probes/probe-results.json",  # O(1)
                    help="results file, relative to --root")  # O(1)
    ap.add_argument("--log", default=".superlite/incidents.jsonl")  # O(1)
    ap.add_argument("--plan", help="plan path; binds session-local --out/--log defaults")  # O(1)
    ap.add_argument("--background", action="store_true", help="detach and return at once")  # O(1)
    ap.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)  # O(1)
    a = ap.parse_args(argv)  # O(1)
    remapped = False  # O(1)
    if not a.worker:  # session-local defaults so packets/finish see this run's probes | O(1)
        try:  # O(1)
            scripts = HERE.parent / "scripts"  # O(1)
            if str(scripts) not in sys.path:  # O(1)
                sys.path.insert(0, str(scripts))  # O(1)
            import session as sl_session  # O(1)
            sd = sl_session.state_dir(getattr(a, "plan", None) or a.root).resolve()  # O(S)
            # Absolute paths: relative display_path + --root child would write child/child/.superlite/...
            if a.out == ".superlite/probes/probe-results.json":  # O(1)
                a.out = str(sd / "probes" / "probe-results.json")  # O(1)
                remapped = True  # O(1)
            if a.log == ".superlite/incidents.jsonl":  # O(1)
                a.log = str(sd / "incidents.jsonl")  # O(1)
                remapped = True  # O(1)
        except Exception:  # noqa: BLE001 | keep flat defaults | O(1)
            pass  # O(1)
    if a.worker:  # O(1)
        return worker_main(a)  # O(1)
    if a.background:  # O(1)
        bg = [x for x in argv if x != "--background"]  # O(1)
        if remapped:  # append so last-wins overrides defaults without dropping custom --out values | O(1)
            bg = bg + ["--out", a.out, "--log", a.log]  # O(1)
        return spawn_background(bg)  # O(1)
    return run_main(a)  # O(1)


if __name__ == "__main__":  # O(1)
    sys.exit(main())  # O(1)
