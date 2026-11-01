"""The only module that spells the run-state folder. Stdlib only."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402

_DIR = ".superlite"
DIR_NAME = _DIR  # for defaults and help text that name the folder relative to a project root


def run_dir(start: Path) -> Path:
    """The run-state folder of the git checkout containing `start` (a linked worktree has its own).
    Outside a git repo: `start`/.superlite."""
    try:
        r = base.git("rev-parse", "--show-toplevel", cwd=Path(start), check=False)
    except base.GitError:
        return Path(start) / _DIR
    top = r.stdout.strip()
    return (Path(top) if r.returncode == 0 and top else Path(start)) / _DIR


def path(start: Path, name: str) -> Path:
    return run_dir(start) / name


def hub_root(start: Path) -> Path:
    """The main checkout of the repo containing `start` (parent of git's common dir)."""
    out = base.git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=Path(start)).stdout.strip()
    return Path(out).parent
