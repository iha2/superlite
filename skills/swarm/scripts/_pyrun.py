"""Shared helpers for running pytest from the superlite scripts. Stdlib only.

pytest_cmd(): command prefix that runs pytest. Uses the current interpreter when it can
import pytest, else `uv run --no-project --with pytest python -m pytest`, else raises
PytestUnavailable with a clear message (`sl` may have fallen back to a bare
`uv run --no-project python`, which has no pytest).

run_tree(cmd, timeout, **kw): subprocess.run that, on timeout, kills the whole process
tree of the child it started (POSIX: new session + killpg; Windows: new process group +
`taskkill /T /F /PID <child>`), never by name. Raises subprocess.TimeoutExpired.

spawn_detached(cmd, log): start cmd detached (own session / process group, stdin closed,
output to the log file, UTF-8 forced) and return the Popen without waiting. The child owns
its process tree (it runs run_tree itself); callers only ever kill the PID they got back.
"""
# Complexity notation:
#   o = bytes of child stdout + stderr, a = length of the command argv, D = descendants of the child process, P = directories on PATH
import importlib.util  # O(1)
import os  # O(1)
import shutil  # O(1)
import signal  # O(1)
import subprocess  # O(1)
import sys  # O(1)


class PytestUnavailable(RuntimeError):  # O(1)
    pass  # O(1)


def have_pytest():  # O(1) import-path lookup (cached after first call)
    return importlib.util.find_spec("pytest") is not None  # O(1) import-path lookup


def pytest_cmd():  # O(P) PATH search
    if have_pytest():  # O(1) import-path lookup
        return [sys.executable, "-m", "pytest"]  # O(1)
    uv = shutil.which("uv")  # O(P) PATH search
    if uv:  # O(1)
        return [uv, "run", "--no-project", "--with", "pytest", "python", "-m", "pytest"]  # O(1)
    raise PytestUnavailable("pytest is not installed for this interpreter "  # O(len(sys.executable))
                            f"({sys.executable}) and uv is not on PATH; "  # O(1)
                            "install pytest (pip install pytest) or uv")  # O(1)


def kill_tree(proc):  # O(D) + process spawn (taskkill)
    """Kill proc and all its descendants. Only the process we started."""  # O(1)
    try:  # O(1)
        if os.name == "nt":  # O(1)
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],  # O(D) + process spawn (taskkill), output O(1) bytes
                           capture_output=True, check=False)  # O(1)
        else:  # O(1)
            os.killpg(proc.pid, signal.SIGKILL)  # O(D) signal syscall
    except (OSError, ProcessLookupError):  # O(1)
        pass  # O(1)
    try:  # O(1)
        proc.kill()  # O(1)
    except OSError:  # O(1)
        pass  # O(1)


def run_tree(cmd, timeout=None, **kw):  # O(a + o) + process spawn; on timeout adds O(D) kill
    if os.name == "nt":  # O(1)
        kw["creationflags"] = kw.get("creationflags", 0) | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW  # O(1) hidden console
    else:  # O(1)
        kw["start_new_session"] = True  # O(1)
    kw.setdefault("stdout", subprocess.PIPE)  # O(1)
    kw.setdefault("stderr", subprocess.PIPE)  # O(1)
    if kw.pop("capture_output", False):  # O(1)
        kw["stdout"] = kw["stderr"] = subprocess.PIPE  # O(1)
    with subprocess.Popen(cmd, **kw) as proc:  # O(a) + process spawn
        try:  # O(1)
            out, err = proc.communicate(timeout=timeout)  # O(o) read until exit or timeout
        except subprocess.TimeoutExpired as e:  # O(1)
            kill_tree(proc)  # O(D) + process spawn (taskkill) on POSIX O(D) signal
            proc.communicate()  # O(o) drain remaining output
            raise subprocess.TimeoutExpired(cmd, timeout, e.output, e.stderr) from None  # O(o)
        except BaseException:  # O(1)
            kill_tree(proc)  # O(D) kill
            raise  # O(1)
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)  # O(1)


def spawn_detached(cmd, log, **kw):  # O(a) + process spawn; does not wait
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}  # O(env)
    if os.name == "nt":  # O(1)
        kw["creationflags"] = (kw.get("creationflags", 0) | subprocess.CREATE_NEW_PROCESS_GROUP  # O(1)
                               | subprocess.CREATE_NO_WINDOW)  # never DETACHED_PROCESS: it makes CREATE_NO_WINDOW ignored and every console child pops a window | O(1)
    else:  # O(1)
        kw["start_new_session"] = True  # O(1)
    with open(log, "ab") as fh:  # O(1)
        return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,  # O(a) + process spawn
                                env=env, close_fds=True, **kw)  # O(1)
