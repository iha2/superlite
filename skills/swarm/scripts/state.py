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
    """The main checkout of the repo containing `start`: itself unless it is a linked worktree, then the first entry
    of `git worktree list` (git lists a --separate-git-dir checkout by its git dir, so that case is not asked)."""
    git_dir, common, top = base.git("rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir",
                                    "--show-toplevel", cwd=Path(start)).stdout.splitlines()[:3]
    if git_dir == common:
        return Path(top)
    first = base.git("worktree", "list", "--porcelain", cwd=Path(start)).stdout.splitlines()[0]
    return Path(first[len("worktree "):])
