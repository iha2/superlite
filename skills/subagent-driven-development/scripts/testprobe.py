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
and `hard_timeout_s`. Any kill is a process-tree kill by PID (`_pyrun.kill_tree`), never by name.

CPU time of a process tree, first that works: psutil if importable; Windows: ctypes (Toolhelp32 snapshot for the
tree, GetProcessTimes for the CPU); Linux: /proc/<pid>/stat; macOS and others: `ps`. If none works the probe
degrades to output-and-children only and never raises. Thresholds: config/testprobe.json (or
SUPERLITE_TESTPROBE_CONFIG=<path>; SUPERLITE_TESTPROBE=0 disables; SUPERLITE_TESTPROBE_BACKEND=
psutil|ctypes|proc|ps|none forces one CPU method). Stdlib only.
"""
# Complexity notation:
#   N = processes on the machine, D = descendants of the test process, S = samples taken, o = bytes of test output
import importlib.util  # O(1)
import json  # O(1)
import os  # O(1)
import subprocess  # O(1)
import sys  # O(1)
import tempfile  # O(1)
import time  # O(1)
import types  # O(1)
from dataclasses import dataclass  # O(1)
from pathlib import Path  # O(1)

sys.path.insert(0, str(Path(__file__).resolve().parent))  # O(path list)
import _pyrun  # noqa: E402  # O(1) module load

CONFIG = Path(__file__).resolve().parent.parent / "config" / "testprobe.json"  # O(1)
DEFAULTS = {"enabled": True, "multiplier": 3, "fallback_expected_s": 60, "min_start_s": 30,  # O(1)
            "sample_interval_s": 12, "min_flat_s": 30, "cpu_idle_pct": 2, "cpu_busy_pct": 20,
            "auto_kill_blocked": False, "auto_kill_after_s": 300, "hard_timeout_s": 0, "max_tree_listed": 8}
FLAT_KINDS = ("blocked", "spinning", "stalled")  # O(1)
HINTS = {"blocked": "deadlock or wait, no CPU and no output",  # O(1)
         "spinning": "likely an infinite loop, CPU busy and no output",
         "stalled": "no output and no child changes; CPU time unavailable here"}


def load_config(path=None):  # O(file)
    """Defaults, overlaid by config/testprobe.json (or SUPERLITE_TESTPROBE_CONFIG); a bad file means defaults."""
    cfg = dict(DEFAULTS)  # O(1)
    p = Path(path or os.environ.get("SUPERLITE_TESTPROBE_CONFIG") or CONFIG)  # O(1)
    try:  # O(1)
        data = json.loads(p.read_text(encoding="utf-8-sig"))  # O(file)
        cfg.update({k: data[k] for k in DEFAULTS if k in data})  # O(1)
    except (OSError, ValueError, TypeError):  # O(1)
        pass  # O(1)
    if os.environ.get("SUPERLITE_TESTPROBE", "") == "0":  # O(1)
        cfg["enabled"] = False  # O(1)
    return cfg  # O(1)


def start_after(cfg, expected_s=None):  # O(1)
    """Seconds into the run before probing starts: multiplier x expected (fallback when unknown), at least min_start_s."""
    exp = expected_s if expected_s and expected_s > 0 else cfg["fallback_expected_s"]  # O(1)
    return max(float(cfg["min_start_s"]), float(cfg["multiplier"]) * float(exp))  # O(1)


# ------------------------------------------------------------------ process table and CPU time

def _table_psutil():  # O(N)
    import psutil  # noqa: PLC0415 | O(1)
    out = {}  # O(1)
    for p in psutil.process_iter(["pid", "ppid", "name", "cpu_times"]):  # O(N)
        i = p.info  # O(1)
        t = i.get("cpu_times")  # O(1)
        out[i["pid"]] = (i.get("ppid") or 0, i.get("name") or "?", (t.user + t.system) if t else None)  # O(1)
    return out  # O(N)


def _table_ctypes():  # O(N) Windows: Toolhelp32 snapshot, GetProcessTimes per process
    import ctypes  # noqa: PLC0415 | O(1)
    from ctypes import wintypes  # noqa: PLC0415 | O(1)

    class ENTRY(ctypes.Structure):  # O(1)
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

    k = ctypes.WinDLL("kernel32", use_last_error=True)  # O(1)
    k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE  # O(1)
    k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]  # O(1)
    k.Process32FirstW.argtypes = k.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ENTRY)]  # O(1)
    k.OpenProcess.restype = wintypes.HANDLE  # O(1)
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]  # O(1)
    k.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4  # O(1)
    k.CloseHandle.argtypes = [wintypes.HANDLE]  # O(1)
    snap = k.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS | O(N)
    if not snap or snap == ctypes.c_void_p(-1).value:  # O(1)
        raise OSError("CreateToolhelp32Snapshot failed")  # O(1)
    out = {}  # O(1)
    try:  # O(1)
        e = ENTRY()  # O(1)
        e.dwSize = ctypes.sizeof(ENTRY)  # O(1)
        ok = k.Process32FirstW(snap, ctypes.byref(e))  # O(1)
        while ok:  # O(N)
            out[e.th32ProcessID] = [e.th32ParentProcessID, e.szExeFile, None]  # O(1)
            ok = k.Process32NextW(snap, ctypes.byref(e))  # O(1)
    finally:  # O(1)
        k.CloseHandle(snap)  # O(1)
    for pid, rec in out.items():  # O(N) GetProcessTimes calls
        h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION | O(1)
        if not h:  # access denied or exited: CPU stays unknown | O(1)
            continue  # O(1)
        try:  # O(1)
            c, x, kern, user = (wintypes.FILETIME() for _ in range(4))  # O(1)
            if k.GetProcessTimes(h, *(ctypes.byref(f) for f in (c, x, kern, user))):  # O(1)
                rec[2] = (((kern.dwHighDateTime << 32) | kern.dwLowDateTime)  # O(1) 100 ns units
                          + ((user.dwHighDateTime << 32) | user.dwLowDateTime)) / 1e7
        finally:  # O(1)
            k.CloseHandle(h)  # O(1)
    return {pid: tuple(rec) for pid, rec in out.items()}  # O(N)


def _table_proc():  # O(N) Linux: /proc/<pid>/stat
    tick = os.sysconf("SC_CLK_TCK")  # O(1)
    out = {}  # O(1)
    for d in os.listdir("/proc"):  # O(N)
        if not d.isdigit():  # O(1)
            continue  # O(1)
        try:  # a process can vanish mid-read | O(1)
            raw = Path(f"/proc/{d}/stat").read_text(encoding="utf-8", errors="replace")  # O(1)
        except OSError:  # O(1)
            continue  # O(1)
        head, _, tail = raw.rpartition(")")  # the comm field may hold spaces and parens | O(1)
        f = tail.split()  # O(1)
        out[int(d)] = (int(f[1]), head.partition("(")[2], (int(f[11]) + int(f[12])) / tick)  # ppid, comm, utime+stime | O(1)
    if not out:  # O(1)
        raise OSError("/proc is empty")  # O(1)
    return out  # O(N)


def _ps_seconds(text):  # O(1) `[dd-][hh:]mm:ss[.cc]` -> seconds
    days, _, rest = text.rpartition("-")  # O(1)
    secs = 0.0  # O(1)
    for part in rest.split(":"):  # O(1)
        secs = secs * 60 + float(part)  # O(1)
    return secs + (int(days) * 86400 if days else 0)  # O(1)


def _table_ps():  # O(N) macOS and other Unix: one `ps` spawn
    cp = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,time=,comm="], capture_output=True, text=True, timeout=10)  # O(N) + spawn
    out = {}  # O(1)
    for ln in cp.stdout.splitlines():  # O(N)
        f = ln.split(None, 3)  # O(1)
        if len(f) == 4 and f[0].isdigit():  # O(1)
            out[int(f[0])] = (int(f[1]), os.path.basename(f[3]), _ps_seconds(f[2]))  # O(1)
    if not out:  # O(1)
        raise OSError("ps printed nothing")  # O(1)
    return out  # O(N)


def _order():  # O(1)
    forced = os.environ.get("SUPERLITE_TESTPROBE_BACKEND", "")  # O(1)
    names = [("psutil", _table_psutil)] if importlib.util.find_spec("psutil") else []  # O(1)
    if os.name == "nt":  # O(1)
        names.append(("ctypes GetProcessTimes", _table_ctypes))  # O(1)
    elif os.path.isdir("/proc"):  # O(1)
        names.append(("/proc", _table_proc))  # O(1)
    if os.name != "nt":  # O(1)
        names.append(("ps", _table_ps))  # O(1)
    if forced == "none":  # O(1)
        return []  # O(1)
    return [n for n in names if not forced or n[0].startswith(forced)] or names  # O(1)


_BACKEND = []  # the first backend that worked: [(name, fn)] | O(1)


def read_table():  # O(N) -> ({pid: (ppid, name, cpu_s or None)}, method name); raises when no backend works
    for name, fn in (_BACKEND or _order()):  # O(1) backends
        try:  # O(1)
            table = fn()  # O(N)
        except Exception:  # noqa: BLE001 | next backend
            _BACKEND.clear()  # O(1)
            continue  # O(1)
        _BACKEND[:] = [(name, fn)]  # O(1)
        return table, name  # O(1)
    raise OSError("no process table backend works")  # O(1)


def cpu_method():  # O(1) the method in use (or "none: ..."); for docs, tests and the summary
    try:  # O(1)
        return read_table()[1]  # O(N)
    except Exception:  # noqa: BLE001 | O(1)
        return "none (output and children only)"  # O(1)


@dataclass
class Sample:  # O(1)
    t: float  # monotonic seconds
    out: int  # bytes written by the process tree so far
    cpu: "float | None"  # tree CPU seconds, None when unknown
    pids: "frozenset | None"  # pids of the tree, None when unknown
    names: dict  # pid -> process name
    method: str = "none"  # which CPU method produced this sample


def tree_of(table, root):  # O(D) breadth-first over the ppid links
    kids = {}  # O(1)
    for pid, rec in table.items():  # O(N)
        kids.setdefault(rec[0], []).append(pid)  # O(1)
    seen, todo = [], [root]  # O(1)
    while todo:  # O(D)
        pid = todo.pop()  # O(1)
        if pid in seen:  # a reused ppid can loop | O(D)
            continue  # O(D)
        seen.append(pid)  # O(1)
        todo.extend(kids.get(pid, ()))  # O(1)
    return [p for p in seen if p in table]  # O(D)


def take_sample(root_pid, out_paths, now=None):  # O(N + o stat) never raises
    """One sample of the tree under root_pid; any failure of the OS call leaves cpu/pids None."""
    out = 0  # O(1)
    for p in out_paths:  # O(1) files
        try:  # O(1)
            out += os.path.getsize(p)  # O(1)
        except OSError:  # O(1)
            pass  # O(1)
    cpu, pids, names, method = None, None, {}, "none"  # O(1)
    try:  # O(1)
        table, method = read_table()  # O(N)
        members = tree_of(table, root_pid)  # O(N)
        pids = frozenset(members)  # O(D)
        names = {p: table[p][1] for p in members}  # O(D)
        known = [table[p][2] for p in members if table[p][2] is not None]  # O(D)
        cpu = sum(known) if known else None  # O(D)
    except Exception:  # noqa: BLE001 | the probe degrades, never errors
        cpu, pids, names, method = None, None, {}, "none"  # O(1)
    return Sample(time.monotonic() if now is None else now, out, cpu, pids, names, method)  # O(1)


# ------------------------------------------------------------------ classification

def judge(prev, cur, cfg):  # O(1) -> (kind, cpu_pct or None)
    dt = cur.t - prev.t  # O(1)
    if dt <= 0 or cur.out != prev.out:  # output moved: alive | O(1)
        return "waiting", None  # O(1)
    if prev.pids is not None and cur.pids is not None and cur.pids != prev.pids:  # children came or went | O(D)
        return "waiting", None  # O(D)
    if cur.cpu is None or prev.cpu is None:  # O(1)
        return "stalled", None  # O(1)
    pct = max(0.0, cur.cpu - prev.cpu) / dt * 100.0  # of one core | O(1)
    if pct <= cfg["cpu_idle_pct"]:  # O(1)
        return "blocked", pct  # O(1)
    if pct >= cfg["cpu_busy_pct"]:  # O(1)
        return "spinning", pct  # O(1)
    return "waiting", pct  # O(1)


def classify(prev, cur, cfg=None):  # O(1) -> "blocked" | "spinning" | "stalled" | "waiting"
    return judge(prev, cur, cfg or DEFAULTS)[0]  # O(1)


def tree_text(names, limit):  # O(D log D) "node.exe(123), cmd.exe(45)"
    items = [f"{n}({p})" for p, n in sorted(names.items())]  # O(D log D)
    return ", ".join(items[:limit]) + (f", +{len(items) - limit} more" if len(items) > limit else "")  # O(D)


class Probe:  # O(1) state; feed is O(D) per sample
    """Feeds on samples; `feed` returns the suspect dict the first time a verdict has held for min_flat_s."""

    def __init__(self, cfg, test_file=None):  # O(1)
        self.cfg, self.test_file = cfg, test_file  # O(1)
        self.prev = None  # O(1)
        self.kind = self.start = None  # the current flat streak | O(1)
        self.pct = None  # O(1)
        self.suspect = None  # first report, updated while it holds | O(1)

    def feed(self, s):  # O(D)
        prev, self.prev = self.prev, s  # O(1)
        if prev is None:  # O(1)
            return None  # O(1)
        kind, self.pct = judge(prev, s, self.cfg)  # O(1)
        if kind in FLAT_KINDS:  # O(1)
            if kind != self.kind:  # O(1)
                self.kind, self.start = kind, prev.t  # O(1)
        else:  # O(1)
            if self.suspect and s.out != prev.out:  # the run moved again after the report | O(1)
                self.suspect["recovered"] = True  # O(1)
            self.kind = self.start = None  # O(1)
        if self.kind is None or s.t - self.start < self.cfg["min_flat_s"]:  # O(1)
            return None  # O(1)
        first = self.suspect is None  # O(1)
        info = {"verdict": self.kind, "flat_s": round(s.t - self.start, 1),  # O(D)
                "cpu_pct": None if self.pct is None else round(self.pct, 1), "cpu_method": s.method,
                "test_file": self.test_file, "recovered": False,
                "tree": [{"pid": p, "name": n} for p, n in sorted(s.names.items())]}
        self.suspect = info  # O(1)
        return info if first else None  # O(1)

    def autokill_due(self):  # O(1) only a BLOCKED streak at idle CPU for auto_kill_after_s
        c = self.cfg  # O(1)
        return bool(c["auto_kill_blocked"] and self.kind == "blocked" and self.prev is not None  # O(1)
                    and self.start is not None and self.prev.t - self.start >= c["auto_kill_after_s"])


def suspect_line(info, elapsed=None, limit=8):  # O(D) one line for stdout and the summary
    names = {t["pid"]: t["name"] for t in info.get("tree", [])}  # O(D)
    what = info.get("test_file") or "test run"  # O(1)
    cpu = "" if info.get("cpu_pct") is None else f", cpu {info['cpu_pct']:.0f}%"  # O(1)
    kill = f"; killed the process tree after {info['killed_after_s']:.0f}s" if info.get("killed_after_s") else ""  # O(1)
    return (f"SUSPECT HANG: {info['verdict'].upper()} {what} - no output for {info['flat_s']:.0f}s{cpu} "  # O(1)
            f"({HINTS[info['verdict']]}){kill}; process tree: {tree_text(names, limit) or 'unknown'}")


def exit_message(rc):  # O(1) a readable reason, never a bare exit code
    if rc in (-1, 0xFFFFFFFF):  # Windows TerminateProcess / unhandled crash | O(1)
        return "process killed or crashed (exit -1)"  # O(1)
    if rc is not None and rc < 0:  # O(1)
        return f"process killed by signal {-rc}"  # O(1)
    if rc is not None and rc >= 0xC0000000:  # an NTSTATUS crash code | O(1)
        return f"process crashed (exit 0x{rc:08X})"  # O(1)
    return f"exit code {rc}"  # O(1)


# ------------------------------------------------------------------ the watched run

def run_watched(cmd, *, shell=False, env=None, cfg=None, expected_s=None, test_file=None, on_suspect=None,  # O(S * (N + D)) + the run
                poll_s=0.25, sampler=take_sample, **kw):
    """Run cmd to the end, probing while it is alive. Output goes to temp files (their size is the output byte
    count), so a grandchild holding a pipe can never block the read. -> SimpleNamespace(returncode, stdout, stderr,
    probe (Probe), killed (None | "auto-kill" | "timeout"), elapsed)."""
    cfg = cfg or load_config()  # O(file)
    start_s = start_after(cfg, expected_s)  # O(1)
    probe = Probe(cfg, test_file)  # O(1)
    if os.name == "nt":  # O(1)
        kw["creationflags"] = kw.get("creationflags", 0) | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW  # O(1)
    else:  # O(1)
        kw["start_new_session"] = True  # O(1)
    killed, next_at = None, None  # O(1)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:  # O(1)
        op, ep = Path(tmp) / "out.txt", Path(tmp) / "err.txt"  # O(1)
        with open(op, "wb") as fo, open(ep, "wb") as fe:  # O(1)
            proc = subprocess.Popen(cmd, shell=shell, env=env, stdout=fo, stderr=fe, **kw)  # O(1) + spawn
        t0 = time.monotonic()  # O(1)
        try:  # O(1)
            while True:  # O(run time / poll_s)
                try:  # O(1)
                    proc.wait(timeout=poll_s)  # O(1) returns at once on exit
                    break  # O(1)
                except subprocess.TimeoutExpired:  # O(1)
                    pass  # O(1)
                el = time.monotonic() - t0  # O(1)
                if cfg["hard_timeout_s"] and el >= cfg["hard_timeout_s"]:  # O(1)
                    killed = "timeout"  # O(1)
                elif cfg["enabled"] and el >= start_s and (next_at is None or time.monotonic() >= next_at):  # O(1)
                    next_at = time.monotonic() + max(0.05, float(cfg["sample_interval_s"]))  # O(1)
                    try:  # the probe must never break the run | O(S)
                        info = probe.feed(sampler(proc.pid, (op, ep)))  # O(N + D)
                        if info is not None and on_suspect:  # O(1)
                            info["elapsed_s"] = round(el, 1)  # O(1)
                            on_suspect(info)  # O(1) caller output
                        if probe.autokill_due():  # O(1)
                            killed = "auto-kill"  # O(1)
                    except Exception as e:  # noqa: BLE001 | O(1)
                        print(f"testprobe: sample failed ({e!r}); continuing", file=sys.stderr)  # O(1)
                if killed:  # O(1)
                    _pyrun.kill_tree(proc)  # O(D) taskkill /T /F /PID or killpg, by PID
                    proc.wait()  # O(1)
                    break  # O(1)
        except BaseException:  # O(1)
            _pyrun.kill_tree(proc)  # O(D)
            raise  # O(1)
        elapsed = time.monotonic() - t0  # O(1)
        read = lambda p: p.read_bytes().decode("utf-8", errors="replace").replace("\r\n", "\n")  # noqa: E731 | O(o)
        out, err = read(op), read(ep)  # O(o)
    if probe.suspect is not None:  # O(1)
        probe.suspect["elapsed_s"] = probe.suspect.get("elapsed_s", round(elapsed, 1))  # O(1)
        if killed:  # O(1)
            probe.suspect["killed_after_s"] = round(elapsed, 1)  # O(1)
    return types.SimpleNamespace(returncode=proc.returncode, stdout=out, stderr=err, probe=probe,  # O(1)
                                 killed=killed, elapsed=elapsed)


def summary_fields(r):  # O(D) ADDITIVE summary fields; {} when the probe saw nothing (the summary keeps its old shape)
    s = getattr(getattr(r, "probe", None), "suspect", None)  # O(1)
    if s is None and not getattr(r, "killed", None):  # O(1)
        return {}  # O(1)
    out = {}  # O(1)
    if s is not None:  # O(1)
        out.update(verdict="SUSPECT HANG", suspect_hang=dict(s, line=suspect_line(s, limit=r.probe.cfg["max_tree_listed"])))  # O(D)
    if r.killed == "timeout":  # O(1)
        out.update(timeout_s=round(r.elapsed, 1))  # O(1)
        out.setdefault("verdict", "TIMEOUT")  # a hang verdict, when there is one, wins | O(1)
    return out  # O(1)


def hang_text(r):  # O(D) the failure text for a killed or hung run, "" when there is none
    s = getattr(getattr(r, "probe", None), "suspect", None)  # O(1)
    parts = []  # O(1)
    if r.killed == "timeout":  # O(1)
        parts.append(f"TIMEOUT: test process tree killed after {r.elapsed:.0f}s (hard_timeout_s)")  # O(1)
    if s is not None:  # O(1)
        parts.append(suspect_line(s, limit=r.probe.cfg["max_tree_listed"]))  # O(D)
    return " | ".join(parts)  # O(1)


def main(argv=None):  # O(N)
    print(json.dumps({"cpu_method": cpu_method(), "config": load_config()}, indent=1))  # O(N)
    return 0  # O(1)


if __name__ == "__main__":  # O(1)
    sys.exit(main())  # O(1)
