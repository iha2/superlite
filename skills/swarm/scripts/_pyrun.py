"""Shared helpers for running pytest from the superlite scripts. Stdlib only.

pytest_cmd(): command prefix that runs pytest. Uses the current interpreter when it can
import pytest, else `uv run --no-project --with pytest python -m pytest`, else raises
PytestUnavailable with a clear message (`superlite-cli` may have fallen back to a bare
`uv run --no-project python`, which has no pytest).

run_tree(cmd, timeout, **kw): subprocess.run that, on timeout, kills the whole process
tree of the child it started (`base.kill_tree`, by PID), never by name. Raises subprocess.TimeoutExpired.

spawn_detached(cmd, log): start cmd detached (own session / process group, stdin closed,
output to the log file, UTF-8 forced) and return the Popen without waiting. The child owns
its process tree (it runs run_tree itself); callers only ever kill the PID they got back.
"""
import importlib.util
import os
import shutil
import subprocess
import sys

import base


class PytestUnavailable(RuntimeError):
    pass


def have_pytest():
    return importlib.util.find_spec("pytest") is not None


def pytest_cmd():  # O(P) PATH search
    if have_pytest():
        return [sys.executable, "-m", "pytest"]
    uv = shutil.which("uv")
    if uv:
        return [uv, "run", "--no-project", "--with", "pytest", "python", "-m", "pytest"]
    raise PytestUnavailable("pytest is not installed for this interpreter "
                            f"({sys.executable}) and uv is not on PATH; "
                            "install pytest (pip install pytest) or uv")


def run_tree(cmd, timeout=None, **kw):  # O(a + o) + process spawn; on timeout adds O(D) kill
    if os.name == "nt":
        kw["creationflags"] = kw.get("creationflags", 0) | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kw["start_new_session"] = True
    kw.setdefault("stdout", subprocess.PIPE)
    kw.setdefault("stderr", subprocess.PIPE)
    if kw.pop("capture_output", False):
        kw["stdout"] = kw["stderr"] = subprocess.PIPE
    with subprocess.Popen(cmd, **kw) as proc:
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as e:
            base.kill_tree(proc.pid)
            proc.communicate()
            raise subprocess.TimeoutExpired(cmd, timeout, e.output, e.stderr) from None
        finally:  # an interrupt or any other error leaves the child running: kill its tree
            if proc.poll() is None:
                base.kill_tree(proc.pid)
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def spawn_detached(cmd, log, **kw):  # O(a) + process spawn; does not wait
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    if os.name == "nt":
        kw["creationflags"] = (kw.get("creationflags", 0) | subprocess.CREATE_NEW_PROCESS_GROUP
                               | subprocess.CREATE_NO_WINDOW)  # never DETACHED_PROCESS: it makes CREATE_NO_WINDOW ignored and every console child pops a window
    else:
        kw["start_new_session"] = True
    with open(log, "ab") as fh:
        return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                                env=env, close_fds=True, **kw)
