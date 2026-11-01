#!/usr/bin/env python3
"""Hung test run probe. Library for run_tests.py; `testprobe.py` alone prints the CPU method and the config.

While a test run is alive, `run_watched` samples the test process tree every `sample_interval_s`: output
bytes, tree CPU time and the set of child processes. It starts only after elapsed passes
`multiplier` x the expected test time (so normal runs pay nothing). Two consecutive samples classify:

    output flat and children unchanged, CPU flat      -> blocked   (deadlock or wait)
    output flat and children unchanged, CPU busy      -> spinning  (likely an infinite loop)
    output flat and children unchanged, CPU unknown   -> stalled   (no CPU call worked; degraded mode)
    anything else                                      -> waiting   (keep going)

A verdict that holds for `min_flat_s` is a SUSPECT HANG: reported once through `on_suspect`. The probe never
kills, except (both off by default) `auto_kill_blocked` for a BLOCKED run at ~0% CPU for `auto_kill_after_s`
and `hard_timeout_s`. Any kill is a process-tree kill by PID (`base.kill_tree`), never by name.

CPU time of a process tree, first that works: psutil if importable; Windows: ctypes (Toolhelp32 snapshot for the
tree, GetProcessTimes for the CPU); Linux: /proc/<pid>/stat; macOS and others: `ps`. If none works the probe
degrades to output-and-children only and never raises. Thresholds: config/testprobe.json (or
SUPERLITE_TESTPROBE_CONFIG=<path>; SUPERLITE_TESTPROBE=0 disables; SUPERLITE_TESTPROBE_BACKEND=
psutil|ctypes|proc|ps|none forces one CPU method). Stdlib only.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import types
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402

CONFIG = Path(__file__).resolve().parent.parent / "config" / "testprobe.json"
DEFAULTS = {"enabled": True, "multiplier": 3, "fallback_expected_s": 60, "min_start_s": 30,
            "sample_interval_s": 12, "min_flat_s": 30, "cpu_idle_pct": 2, "cpu_busy_pct": 20,
            "auto_kill_blocked": False, "auto_kill_after_s": 300, "hard_timeout_s": 0, "max_tree_listed": 8}
FLAT_KINDS = ("blocked", "spinning", "stalled")
HINTS = {"blocked": "deadlock or wait, no CPU and no output",
         "spinning": "likely an infinite loop, CPU busy and no output",
         "stalled": "no output and no child changes; CPU time unavailable here"}


def load_config(path=None):  # O(file)
    """Defaults, overlaid by config/testprobe.json (or SUPERLITE_TESTPROBE_CONFIG); a bad file means defaults.

    `superlite.json` `testprobe` keys merge only when `path` is omitted and SUPERLITE_TESTPROBE_CONFIG is unset.
    """
    cfg = dict(DEFAULTS)
    env = os.environ.get("SUPERLITE_TESTPROBE_CONFIG")
    p = Path(path or env or CONFIG)
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        cfg.update({k: data[k] for k in DEFAULTS if k in data})
    except (OSError, ValueError, TypeError):
        pass
    if path is None and not env:  # explicit path or env wins
        import project_config
        over = project_config.load(Path(".").resolve()).get("testprobe")
        if isinstance(over, dict):
            cfg.update({k: over[k] for k in DEFAULTS if k in over})
    if os.environ.get("SUPERLITE_TESTPROBE", "") == "0":
        cfg["enabled"] = False
    return cfg


def start_after(cfg, expected_s=None):
    """Seconds into the run before probing starts: multiplier x expected (fallback when unknown), at least min_start_s."""
    exp = expected_s if expected_s and expected_s > 0 else cfg["fallback_expected_s"]
    return max(float(cfg["min_start_s"]), float(cfg["multiplier"]) * float(exp))


# ------------------------------------------------------------------ process table and CPU time

def _table_psutil():  # O(N)
    import psutil  # noqa: PLC0415
    out = {}
    try:
        for p in psutil.process_iter(["pid", "ppid", "name", "cpu_times"]):
            i = p.info
            t = i.get("cpu_times")
            out[i["pid"]] = (i.get("ppid") or 0, i.get("name") or "?", (t.user + t.system) if t else None)
    except psutil.Error as e:
        raise OSError(f"psutil: {e}") from e
    return out


def _table_ctypes():  # O(N) Windows: Toolhelp32 snapshot, GetProcessTimes per process
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    class ENTRY(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k.Process32FirstW.argtypes = k.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ENTRY)]
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    snap = k.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snap or snap == ctypes.c_void_p(-1).value:
        raise OSError("CreateToolhelp32Snapshot failed")
    out = {}
    try:
        e = ENTRY()
        e.dwSize = ctypes.sizeof(ENTRY)
        ok = k.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            out[e.th32ProcessID] = [e.th32ParentProcessID, e.szExeFile, None]
            ok = k.Process32NextW(snap, ctypes.byref(e))
    finally:
        k.CloseHandle(snap)
    for pid, rec in out.items():
        h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:  # access denied or exited: CPU stays unknown
            continue
        try:
            c, x, kern, user = (wintypes.FILETIME() for _ in range(4))
            if k.GetProcessTimes(h, *(ctypes.byref(f) for f in (c, x, kern, user))):
                rec[2] = (((kern.dwHighDateTime << 32) | kern.dwLowDateTime)
                          + ((user.dwHighDateTime << 32) | user.dwLowDateTime)) / 1e7
        finally:
            k.CloseHandle(h)
    return {pid: tuple(rec) for pid, rec in out.items()}


def _table_proc():  # O(N) Linux: /proc/<pid>/stat
    tick = os.sysconf("SC_CLK_TCK")
    out = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:  # a process can vanish mid-read
            raw = Path(f"/proc/{d}/stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        head, _, tail = raw.rpartition(")")  # the comm field may hold spaces and parens
        f = tail.split()
        out[int(d)] = (int(f[1]), head.partition("(")[2], (int(f[11]) + int(f[12])) / tick)  # ppid, comm, utime+stime
    if not out:
        raise OSError("/proc is empty")
    return out


def _ps_seconds(text):
    days, _, rest = text.rpartition("-")
    secs = 0.0
    for part in rest.split(":"):
        secs = secs * 60 + float(part)
    return secs + (int(days) * 86400 if days else 0)


def _table_ps():  # O(N) macOS and other Unix: one `ps` spawn
    cp = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,time=,comm="], capture_output=True, text=True, timeout=10)
    out = {}
    for ln in cp.stdout.splitlines():
        f = ln.split(None, 3)
        if len(f) == 4 and f[0].isdigit():
            out[int(f[0])] = (int(f[1]), os.path.basename(f[3]), _ps_seconds(f[2]))
    if not out:
        raise OSError("ps printed nothing")
    return out


def _order():
    forced = os.environ.get("SUPERLITE_TESTPROBE_BACKEND", "")
    names = [("psutil", _table_psutil)] if importlib.util.find_spec("psutil") else []
    if os.name == "nt":
        names.append(("ctypes GetProcessTimes", _table_ctypes))
    elif os.path.isdir("/proc"):
        names.append(("/proc", _table_proc))
    if os.name != "nt":
        names.append(("ps", _table_ps))
    if forced == "none":
        return []
    return [n for n in names if not forced or n[0].startswith(forced)] or names


_BACKEND = []  # the first backend that worked: [(name, fn)]
# What a backend can raise: an OS call or file read failing, a missing module, a parse of odd output, a timed-out spawn.
_BACKEND_ERRORS = (OSError, ImportError, ValueError, LookupError, AttributeError, TypeError, ArithmeticError,
                   subprocess.SubprocessError)


def read_table():  # O(N) -> ({pid: (ppid, name, cpu_s or None)}, method name); raises when no backend works
    for name, fn in (_BACKEND or _order()):
        try:
            table = fn()
        except _BACKEND_ERRORS:  # next backend
            _BACKEND.clear()
            continue
        _BACKEND[:] = [(name, fn)]
        return table, name
    raise OSError("no process table backend works")


def cpu_method():
    try:
        return read_table()[1]
    except OSError:  # read_table raises OSError when no backend works
        return "none (output and children only)"


@dataclass
class Sample:
    t: float  # monotonic seconds
    out: int  # bytes written by the process tree so far
    cpu: "float | None"  # tree CPU seconds, None when unknown
    pids: "frozenset | None"  # pids of the tree, None when unknown
    names: dict  # pid -> process name
    method: str = "none"  # which CPU method produced this sample


def tree_of(table, root):  # O(D) breadth-first over the ppid links
    kids = {}
    for pid, rec in table.items():
        kids.setdefault(rec[0], []).append(pid)
    seen, todo = [], [root]
    while todo:
        pid = todo.pop()
        if pid in seen:  # a reused ppid can loop
            continue
        seen.append(pid)
        todo.extend(kids.get(pid, ()))
    return [p for p in seen if p in table]


def take_sample(root_pid, out_paths, now=None):  # O(N + o stat) never raises
    """One sample of the tree under root_pid; any failure of the OS call leaves cpu/pids None."""
    out = 0
    for p in out_paths:
        try:
            out += os.path.getsize(p)
        except OSError:
            pass
    cpu, pids, names, method = None, None, {}, "none"
    try:
        table, method = read_table()
        members = tree_of(table, root_pid)
        pids = frozenset(members)
        names = {p: table[p][1] for p in members}
        known = [table[p][2] for p in members if table[p][2] is not None]
        cpu = sum(known) if known else None
    except (OSError, LookupError, ValueError, TypeError):  # read_table: no backend works; tree_of/table: a process vanished. The probe degrades, never errors
        cpu, pids, names, method = None, None, {}, "none"
    return Sample(time.monotonic() if now is None else now, out, cpu, pids, names, method)


# ------------------------------------------------------------------ classification

def judge(prev, cur, cfg):
    dt = cur.t - prev.t
    if dt <= 0 or cur.out != prev.out:  # output moved: alive
        return "waiting", None
    if prev.pids is not None and cur.pids is not None and cur.pids != prev.pids:  # children came or went
        return "waiting", None
    if cur.cpu is None or prev.cpu is None:
        return "stalled", None
    pct = max(0.0, cur.cpu - prev.cpu) / dt * 100.0  # of one core
    if pct <= cfg["cpu_idle_pct"]:
        return "blocked", pct
    if pct >= cfg["cpu_busy_pct"]:
        return "spinning", pct
    return "waiting", pct


def classify(prev, cur, cfg=None):
    return judge(prev, cur, cfg or DEFAULTS)[0]


def tree_text(names, limit):  # O(D log D) "node.exe(123), cmd.exe(45)"
    items = [f"{n}({p})" for p, n in sorted(names.items())]
    return ", ".join(items[:limit]) + (f", +{len(items) - limit} more" if len(items) > limit else "")


class Probe:
    """Feeds on samples; `feed` returns the suspect dict the first time a verdict has held for min_flat_s."""

    def __init__(self, cfg, test_file=None):
        self.cfg, self.test_file = cfg, test_file
        self.prev = None
        self.kind = self.start = None  # the current flat streak
        self.pct = None
        self.suspect = None  # first report, updated while it holds

    def feed(self, s):  # O(D)
        prev, self.prev = self.prev, s
        if prev is None:
            return None
        kind, self.pct = judge(prev, s, self.cfg)
        if kind in FLAT_KINDS:
            if kind != self.kind:
                self.kind, self.start = kind, prev.t
        else:
            if self.suspect and s.out != prev.out:  # the run moved again after the report
                self.suspect["recovered"] = True
            self.kind = self.start = None
        if self.kind is None or s.t - self.start < self.cfg["min_flat_s"]:
            return None
        first = self.suspect is None
        info = {"verdict": self.kind, "flat_s": round(s.t - self.start, 1),
                "cpu_pct": None if self.pct is None else round(self.pct, 1), "cpu_method": s.method,
                "test_file": self.test_file, "recovered": False,
                "tree": [{"pid": p, "name": n} for p, n in sorted(s.names.items())]}
        self.suspect = info
        return info if first else None

    def autokill_due(self):
        c = self.cfg
        return bool(c["auto_kill_blocked"] and self.kind == "blocked" and self.prev is not None
                    and self.start is not None and self.prev.t - self.start >= c["auto_kill_after_s"])


def suspect_line(info, elapsed=None, limit=8):  # O(D) one line for stdout and the summary
    names = {t["pid"]: t["name"] for t in info.get("tree", [])}
    what = info.get("test_file") or "test run"
    cpu = "" if info.get("cpu_pct") is None else f", cpu {info['cpu_pct']:.0f}%"
    kill = f"; killed the process tree after {info['killed_after_s']:.0f}s" if info.get("killed_after_s") else ""
    return (f"SUSPECT HANG: {info['verdict'].upper()} {what} - no output for {info['flat_s']:.0f}s{cpu} "
            f"({HINTS[info['verdict']]}){kill}; process tree: {tree_text(names, limit) or 'unknown'}")


def exit_message(rc):
    if rc in (-1, 0xFFFFFFFF):  # Windows TerminateProcess / unhandled crash
        return "process killed or crashed (exit -1)"
    if rc is not None and rc < 0:
        return f"process killed by signal {-rc}"
    if rc is not None and rc >= 0xC0000000:  # an NTSTATUS crash code
        return f"process crashed (exit 0x{rc:08X})"
    return f"exit code {rc}"


# ------------------------------------------------------------------ the watched run

def _exited(proc, poll_s):
    try:
        proc.wait(timeout=poll_s)
    except subprocess.TimeoutExpired:
        return False
    return True


def _sample_due(cfg, el, start_s, sched):
    if not cfg["enabled"] or el < start_s:
        return False
    return sched["next"] is None or time.monotonic() >= sched["next"]


def _check(proc, paths, cfg, probe, start_s, sched, el, sampler, on_suspect):
    """One look at a live run: -> None (keep going), "timeout" or "auto-kill". The probe never breaks the run."""
    if cfg["hard_timeout_s"] and el >= cfg["hard_timeout_s"]:
        return "timeout"
    if not _sample_due(cfg, el, start_s, sched):
        return None
    sched["next"] = time.monotonic() + max(0.05, float(cfg["sample_interval_s"]))
    try:
        info = probe.feed(sampler(proc.pid, paths))
        if info is not None and on_suspect:
            info["elapsed_s"] = round(el, 1)
            on_suspect(info)
        return "auto-kill" if probe.autokill_due() else None
    except Exception as e:  # noqa: BLE001 | logged below, the run goes on
        print(f"testprobe: sample failed ({e!r}); continuing", file=sys.stderr)
        return None


def _watch(proc, paths, cfg, probe, start_s, sampler, on_suspect, poll_s, t0):
    """Wait for proc, probing while it lives. -> how it was killed (None | "auto-kill" | "timeout")."""
    sched = {"next": None}
    while not _exited(proc, poll_s):
        killed = _check(proc, paths, cfg, probe, start_s, sched, time.monotonic() - t0, sampler, on_suspect)
        if killed:
            base.kill_tree(proc.pid)
            proc.wait()
            return killed
    return None


def _new_group_kw(kw):
    if os.name == "nt":
        kw["creationflags"] = kw.get("creationflags", 0) | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kw["start_new_session"] = True
    return kw


def run_watched(cmd, *, shell=False, env=None, cfg=None, expected_s=None, test_file=None, on_suspect=None,  # O(S * (N + D)) + the run
                poll_s=0.25, sampler=take_sample, **kw):
    """Run cmd to the end, probing while it is alive. Output goes to temp files (their size is the output byte
    count), so a grandchild holding a pipe can never block the read. -> SimpleNamespace(returncode, stdout, stderr,
    probe (Probe), killed (None | "auto-kill" | "timeout"), elapsed)."""
    cfg = cfg or load_config()
    probe = Probe(cfg, test_file)
    kw = _new_group_kw(kw)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        op, ep = Path(tmp) / "out.txt", Path(tmp) / "err.txt"
        with open(op, "wb") as fo, open(ep, "wb") as fe:
            proc = subprocess.Popen(cmd, shell=shell, env=env, stdout=fo, stderr=fe, **kw)
        t0 = time.monotonic()
        try:
            killed = _watch(proc, (op, ep), cfg, probe, start_after(cfg, expected_s), sampler, on_suspect, poll_s, t0)
        finally:  # an interrupt or error in the watch leaves the run alive: kill its tree
            if proc.poll() is None:
                base.kill_tree(proc.pid)
                proc.wait()
        elapsed = time.monotonic() - t0
        out, err = (p.read_bytes().decode("utf-8", errors="replace").replace("\r\n", "\n") for p in (op, ep))
    if probe.suspect is not None:
        probe.suspect["elapsed_s"] = probe.suspect.get("elapsed_s", round(elapsed, 1))
        if killed:
            probe.suspect["killed_after_s"] = round(elapsed, 1)
    return types.SimpleNamespace(returncode=proc.returncode, stdout=out, stderr=err, probe=probe,
                                 killed=killed, elapsed=elapsed)


def summary_fields(r):  # O(D) ADDITIVE summary fields; {} when the probe saw nothing (the summary keeps its old shape)
    s = getattr(getattr(r, "probe", None), "suspect", None)
    if s is None and not getattr(r, "killed", None):
        return {}
    out = {}
    if s is not None:
        out.update(verdict="SUSPECT HANG", suspect_hang=dict(s, line=suspect_line(s, limit=r.probe.cfg["max_tree_listed"])))
    if r.killed == "timeout":
        out.update(timeout_s=round(r.elapsed, 1))
        out.setdefault("verdict", "TIMEOUT")  # a hang verdict, when there is one, wins
    return out


def hang_text(r):  # O(D) the failure text for a killed or hung run, "" when there is none
    s = getattr(getattr(r, "probe", None), "suspect", None)
    parts = []
    if r.killed == "timeout":
        parts.append(f"TIMEOUT: test process tree killed after {r.elapsed:.0f}s (hard_timeout_s)")
    if s is not None:
        parts.append(suspect_line(s, limit=r.probe.cfg["max_tree_listed"]))
    return " | ".join(parts)


def main(argv=None):  # O(N)
    print(json.dumps({"cpu_method": cpu_method(), "config": load_config()}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
