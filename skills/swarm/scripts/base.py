"""Shared base: git, json files, locks, processes, flags. Stdlib only."""
import json
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl

TRUE = ("1", "true", "yes", "on")
FALSE = ("0", "false", "no", "off")


class GitError(Exception):
    def __init__(self, args_, stderr: str):
        super().__init__(f"git {' '.join(args_)}: {stderr.strip()}")
        self.stderr = stderr


def git(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    """Run git in cwd. With check, a failure raises GitError (never exits)."""
    try:
        r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    except OSError as e:
        raise GitError(args, str(e)) from e
    if check and r.returncode != 0:
        raise GitError(args, r.stderr)
    return r


def advisory_errors() -> tuple:
    """What an optional step (an advisory line, a cost estimate, a fallback lookup) may fail with while the tool goes
    on without it: I/O, bad data, a bad plan, config or predictor input, a git failure. Bugs (TypeError ...) are not
    in it: they surface."""
    import planfile
    import predict
    import project_config
    return (OSError, ValueError, LookupError, ArithmeticError, ImportError, GitError,
            planfile.PlanError, predict.PredictError, project_config.ProjectConfigError)


def read_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, data) -> None:
    """Atomic: write a temp file in the same folder, then replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    replaced = False
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=1)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        replaced = True
    finally:
        if not replaced:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _lock(f) -> None:
    if os.name == "nt":
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
    else:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)


def _unlock(f) -> None:
    if os.name == "nt":
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def locked_update(path: Path, fn) -> dict:
    """Lock, read the json object at path ({} if absent), apply fn, write atomically, return the result."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path) + ".lock", "a+b") as lock:
        _lock(lock)
        try:
            new = fn(read_json(path, {}))
            write_json(path, new)
            return new
        finally:
            _unlock(lock)


def kill_tree(pid: int) -> None:
    """Kill the process we started and its descendants, by PID only."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, check=False)
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def spawn_background(argv: list, log: Path) -> int:
    """Start argv detached in its own session, output appended to log; return its PID."""
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kw["start_new_session"] = True
    Path(log).parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    with open(log, "ab") as fh:
        return subprocess.Popen(list(argv), stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                                env=env, close_fds=True, **kw).pid


def flag(env: str, path: Path, key: str, default: bool) -> bool:
    """On/off setting: the env var wins, then `key` in the json file at path, then default."""
    val = os.environ.get(env, "").strip().lower()
    if val in TRUE:
        return True
    if val in FALSE and val:
        return False
    data = read_json(path, {})
    if isinstance(data, dict) and isinstance(data.get(key), bool):
        return data[key]
    return default
