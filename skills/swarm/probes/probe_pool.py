"""The parent side of the probe runner: worker processes, the pool, and one probe run (size steps)."""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

from probe_common import ENTRY_TIMEOUT, HERE, IS_WIN, frames_from_text

import base  # noqa: E402 | scripts dir is on sys.path through probe_common



class Cfg:
    def __init__(self, root: Path, adapter: Path, probes_dir: Path | None, timeout: float):
        self.root, self.adapter, self.probes_dir, self.timeout = root, adapter, probes_dir, timeout
        self.stats_lock = threading.Lock()
        self.killed_pids: list[int] = []
        self.server_starts: dict[int, int] = {}
        self.server_ports: set[int] = set()
        self.workers_started = 0

    def in_repo(self, filename: str) -> bool:  # O(parts) + resolve syscall
        try:
            p = Path(filename).resolve()
        except (OSError, ValueError):
            return False
        if not p.is_relative_to(self.root) or p.is_relative_to(HERE) or p == self.adapter:
            return False
        if self.probes_dir and p.is_relative_to(self.probes_dir):
            return False
        return not ({"site-packages", ".venv", "venv", "node_modules", "__pycache__"} & set(p.parts))

    def rel(self, filename: str) -> str:  # O(parts) + resolve syscall
        try:
            return Path(filename).resolve().relative_to(self.root).as_posix()
        except ValueError:
            return Path(filename).as_posix()


class Worker:
    def __init__(self, cfg: Cfg):
        self.cfg = cfg
        self.dead = False
        self.errf = tempfile.NamedTemporaryFile(prefix="probe-worker-", suffix=".err", delete=False)
        cmd = [sys.executable, "-X", "utf8", str(HERE / "probe_run.py"), "--worker",
               "--root", str(cfg.root), "--adapter", str(cfg.adapter)]
        kw: dict = {}
        if IS_WIN:
            kw["creationflags"] = 0x08000000 | 0x00000200
        else:
            kw["start_new_session"] = True
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1",
                   SUPERLITE_PROBE_TIMEOUT=str(min(cfg.timeout, ENTRY_TIMEOUT["run"])))
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=self.errf, text=True, encoding="utf-8", bufsize=1,
                                  cwd=str(cfg.root), env=env, **kw)
        self.q: queue.Queue = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        self.tmp = None
        self.real_pid = None
        self.entries: dict = {}
        with cfg.stats_lock:
            cfg.workers_started += 1
        try:
            ready = self.q.get(timeout=60)
        except queue.Empty:
            ready = None
        if not ready or not ready.get("ready"):
            err = (ready or {}).get("error") or self._stderr_tail()
            self.close(kill=True)
            raise RuntimeError(f"adapter failed to load: {err.strip()[-400:]}")
        self.real_pid, self.tmp, self.entries = ready["pid"], ready["tmp"], ready["entries"]

    def _read(self):  # O(output lines) total
        try:
            for line in self.p.stdout:
                try:
                    self.q.put(json.loads(line))
                except ValueError:
                    pass
        except (OSError, ValueError):  # the pipe closed or sent bytes that are not text: the worker is gone
            pass
        self.q.put(None)

    def _err_size(self) -> int:
        try:
            return os.path.getsize(self.errf.name)
        except OSError:
            return 0

    def _stderr_tail(self, off: int = 0) -> str:  # O(stderr bytes from off) total
        try:
            with open(self.errf.name, "rb") as f:
                f.seek(off)
                return f.read().decode("utf-8", "replace")
        except OSError:
            return ""

    def _kill(self):
        pids = [x for x in (self.p.pid, self.real_pid) if x]
        base.kill_tree(self.p.pid)
        try:
            self.p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        with self.cfg.stats_lock:
            self.cfg.killed_pids.extend(pids)
        self.dead = True

    def step(self, meta: dict, size, timeout: float) -> dict:  # O(msg + reply + stderr tail) total, blocks <=timeout
        off = self._err_size()
        msg = {"op": "step", "module": meta["module"], "name": meta["name"], "size": size,
               "timeout": timeout}
        t0 = time.perf_counter()
        try:
            self.p.stdin.write(json.dumps(msg) + "\n")
            self.p.stdin.flush()
            rep = self.q.get(timeout=timeout)
        except queue.Empty:
            tail = self._stderr_tail(off)
            self._kill()
            return {"status": "hang", "detail": f"no response within {timeout:g}s",
                    "timeout": timeout, "size": size, "frames": frames_from_text(tail),
                    "elapsed": round(time.perf_counter() - t0, 3),
                    "killed_pids": [x for x in (self.p.pid, self.real_pid) if x]}
        except OSError:
            rep = None
        if rep is None:
            code = self.p.poll()
            tail = self._stderr_tail(off)
            self._kill()
            return {"status": "crash", "exc": "WorkerDied", "size": size,
                    "detail": f"worker process exited ({code}) {tail.strip()[-150:]}",
                    "frames": frames_from_text(tail), "elapsed": round(time.perf_counter() - t0, 3)}
        srv = rep.pop("server", None)
        if srv:
            with self.cfg.stats_lock:
                self.cfg.server_starts[self.p.pid] = srv["starts"]
                if srv["port"]:
                    self.cfg.server_ports.add(srv["port"])
        if rep["status"] in ("crash", "wrong-status", "leak") and not any(
                self.cfg.in_repo(f) for f, _ in rep.get("frames", [])):
            rep["frames"] = rep.get("frames", []) + frames_from_text(self._stderr_tail(off))
        return rep

    def close(self, kill: bool = False):
        if not kill:
            try:
                self.p.stdin.write(json.dumps({"op": "quit"}) + "\n")
                self.p.stdin.flush()
                self.p.wait(timeout=4)
            except (OSError, ValueError, subprocess.TimeoutExpired):  # pipe gone, file closed, or no clean exit
                kill = True
        if kill and self.p.poll() is None:
            self._kill()
        try:
            self.errf.close()
            os.unlink(self.errf.name)
        except OSError:
            pass
        if self.tmp:
            shutil.rmtree(self.tmp, ignore_errors=True)


class Pool:
    def __init__(self, cfg: Cfg, size: int):
        self.cfg, self.max = cfg, max(1, size)
        self.idle: queue.Queue = queue.Queue()
        self.all: list[Worker] = []
        self.lock = threading.Lock()

    def acquire(self) -> Worker:  # O(a) a = workers ever spawned, + worker spawn
        try:
            return self.idle.get_nowait()
        except queue.Empty:
            pass
        with self.lock:
            live = sum(1 for w in self.all if w is None or not w.dead)
            spawn = live < self.max
            if spawn:
                live_slot = len(self.all)
                self.all.append(None)
        if spawn:
            w = None
            try:
                w = Worker(self.cfg)
            finally:  # a worker that failed to start leaves a dead placeholder; the error goes on
                with self.lock:
                    self.all[live_slot] = w or types.SimpleNamespace(dead=True, close=lambda kill=False: None)
            return w
        return self.idle.get()

    def release(self, w: Worker):
        if not w.dead:
            self.idle.put(w)

    def close(self):  # O(a) + per-worker close
        for w in self.all:
            if w is not None and not w.dead:
                w.close()
            elif w is not None and hasattr(w, "errf"):
                w.close(kill=True)


class Lease:
    def __init__(self, pool: Pool):
        self.pool, self.w = pool, None

    def step(self, meta, size, timeout):
        try:
            if self.w is None:
                self.w = self.pool.acquire()
        except RuntimeError as e:
            return {"status": "error", "detail": str(e)[:200], "size": size}
        out = self.w.step(meta, size, timeout)
        if self.w.dead:
            self.w.close(kill=True)
            self.w = None
        return out

    def close(self):
        if self.w is not None:
            self.pool.release(self.w)
            self.w = None


def timeout_for(meta: dict, cfg: Cfg) -> float:
    return min(ENTRY_TIMEOUT[meta["entry"]], cfg.timeout)


def run_probe(pool: Pool, cfg: Cfg, meta: dict) -> dict:  # O(z * step) total, z = size steps
    lease = Lease(pool)
    res = {"id": meta["id"], "category": meta["category"], "entry": meta["entry"],
           "status": "pass", "size": None, "detail": "", "elapsed": 0.0}
    t0 = time.perf_counter()
    prev = None
    try:
        for size in meta["sizes"]:
            out = lease.step(meta, size, timeout_for(meta, cfg))
            if out["status"] != "pass":
                res.update(out)
                res["prev_pass_size"] = prev
                res["probe_meta"] = meta
                break
            prev = size
            res.update(status="pass", detail=out.get("detail", ""), size=size)
    finally:
        lease.close()
    res["elapsed"] = round(time.perf_counter() - t0, 3)
    return res


def group_key(res: dict, cfg: Cfg) -> tuple[str, tuple[str, int] | None]:  # O(frames * in_repo)
    st = res["status"]
    top = next(((f, n) for f, n in res.get("frames", []) if cfg.in_repo(f)), None)
    where = f"{cfg.rel(top[0])}:{top[1]}" if top else f"{res['category']}/{res['entry']}"
    if st == "crash":
        return f"crash|{res.get('exc')}|{where}", top
    if st == "hang":
        return f"hang|{where}", top
    if st == "leak":
        return f"leak|{res.get('leak')}|{where}", top
    if st == "wrong-status":
        return f"wrong-status|{res.get('http')}|{where}", top
    return f"{st}|{res['id']}", top
