"""Shared base: git, json files, locks, processes, flags. Stdlib only."""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
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


def _win_retry(fn):
    """fn(), retried briefly on Windows while another process holds the file open or is replacing it (a sharing
    violation surfaces as PermissionError there); elsewhere PermissionError is final, so fn() runs once."""
    for _ in range(50 if os.name == "nt" else 0):
        try:
            return fn()
        except PermissionError:
            time.sleep(0.01)
    return fn()


def read_json(path: Path, default):
    try:
        return json.loads(_win_retry(lambda: Path(path).read_text(encoding="utf-8")))
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
        _win_retry(lambda: os.replace(tmp, path))
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


def bash() -> str:
    """A real bash: SUPERLITE_BASH, else Git's on Windows (a bare "bash" there resolves to System32's WSL stub
    before PATH), else whatever is on PATH."""
    if os.environ.get("SUPERLITE_BASH"):
        return os.environ["SUPERLITE_BASH"]
    if os.name == "nt":
        found = git_bash(shutil.which("git"), os.environ.get("ProgramFiles", r"C:\Program Files"))
        if found:
            return found
    return shutil.which("bash") or "bash"


def git_bash(git_exe: str | None, program_files: str) -> str | None:
    """Git for Windows' bash.exe, near the git on PATH (Git\\cmd, Git\\bin or Git\\mingw64\\bin) or under Program Files;
    None if neither has one."""
    roots = list(Path(git_exe).parents)[1:3] if git_exe else []
    roots.append(Path(program_files) / "Git")
    return next((str(r / sub / "bash.exe") for r in roots for sub in ("bin", "usr/bin")
                 if (r / sub / "bash.exe").is_file()), None)


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
