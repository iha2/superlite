"""Where superlite cuts worktrees: `<hub>/.claude/worktrees/<name>` (D10). Stdlib only.

Names: `sessions-<slug>`, `<topic>-proto`, `merge-<base>`. Branches stay `superlite/...`. Old homes
(`.superlite/worktrees/...`, `.superlite/sandboxes/...`) keep working for rows already registered there.
`superlite_cut` is the one test for "superlite made this checkout and may remove it": the Claude Code
harness shares the folder (`agent-*`, `worktree-*` branches) and is never touched.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import base  # noqa: E402
import ident  # noqa: E402
import state  # noqa: E402

EXCLUDE = ".claude/worktrees/"
_NAMED = re.compile(r"^(sessions-[a-z0-9][a-z0-9-]*|[a-z0-9][a-z0-9._-]*-proto|merge-[A-Za-z0-9._-]+)$")


def home_path(root: Path) -> Path:
    return Path(root) / ".claude" / "worktrees"


def home(root: Path) -> Path:
    """The home folder, created on first use."""
    d = home_path(root)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _registered(root: Path) -> list[str]:  # O(W)
    text = base.git("worktree", "list", "--porcelain", cwd=root, check=False).stdout
    return [ident.git_path(ln[len("worktree "):]) for ln in text.splitlines() if ln.startswith("worktree ")]


def _free(root: Path, name: str) -> Path:
    """`<home>/<name>`, or `<name>-2`, `-3` while another worktree holds the path."""
    used = _registered(root)
    n, cand = 1, name
    while any(ident.same_path(Path(u), home_path(root) / cand) for u in used):
        n += 1
        cand = f"{name}-{n}"
    return home_path(root) / cand


def session_dir(root: Path, slug: str) -> Path:
    return _free(root, f"sessions-{slug}")


def proto_dir(root: Path, topic: str) -> Path:
    return _free(root, f"{topic}-proto")


def merge_dir(root: Path, base_name: str) -> Path:
    return _free(root, "merge-" + re.sub(r"[^A-Za-z0-9._-]+", "-", base_name).strip("-"))


def _exclude_file(root: Path) -> Path:
    common = base.git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=root).stdout.strip()
    return Path(common) / "info" / "exclude"


def _append_once(root: Path, forms: tuple[str, ...], line: str) -> None:
    f = _exclude_file(root)
    f.parent.mkdir(parents=True, exist_ok=True)
    text = f.read_text(encoding="utf-8") if f.is_file() else ""
    if any(ln.strip() in forms for ln in text.splitlines()):
        return
    with f.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(("" if not text or text.endswith("\n") else "\n") + line + "\n")


def hide(root: Path) -> None:
    """Keep the home out of `git status` and `git stash -u`, via info/exclude (never a tracked .gitignore)."""
    _append_once(root, (EXCLUDE, "/" + EXCLUDE, EXCLUDE.rstrip("/")), EXCLUDE)


def hide_superlite(root: Path) -> None:
    """Keep .superlite/ out of `git status` via .git/info/exclude, never by editing a tracked .gitignore."""
    d = state.DIR_NAME
    _append_once(root, (f"{d}/", f"/{d}/"), f"{d}/")


def hide_all(root: Path) -> None:
    hide_superlite(root)
    hide(root)


def _under(path, folder: Path) -> bool:
    return ident.within(Path(path), folder)


def harness_name(path, branch: str | None) -> bool:
    """A name or branch the Claude Code harness uses: agent-*, worktree-*, or a worktree-* / claude/* branch."""
    name = Path(path).name
    return name.startswith(("agent-", "worktree-")) or bool(branch) and branch.startswith(("worktree-", "claude/"))


def superlite_named(path, branch: str | None) -> bool:
    """A name superlite gives its own worktrees, on a superlite/ branch when the branch is known."""
    return bool(_NAMED.match(Path(path).name)) and (branch is None or branch.startswith("superlite/"))


def harness_worktree(path, branch: str | None) -> bool:
    """A checkout the harness made: under .claude/worktrees/ (unless named like superlite's), or on a worktree-*/claude/* branch."""
    parts = Path(path).as_posix().split("/")
    under = any(a == ".claude" and b == "worktrees" for a, b in zip(parts, parts[1:]))
    if bool(branch) and branch.startswith(("worktree-", "claude/")):
        return True
    return under and not superlite_named(path, branch)


def _branch_of(root: Path, path) -> str | None:  # O(W)
    text = base.git("worktree", "list", "--porcelain", cwd=root, check=False).stdout
    cur = None
    for ln in text.splitlines() + [""]:
        if ln.startswith("worktree "):
            cur = ident.same_path(Path(ident.git_path(ln[len("worktree "):])), Path(path))
        elif cur and ln.startswith("branch "):
            ref = ln[len("branch "):]
            return ref[11:] if ref.startswith("refs/heads/") else ref
        elif ln == "":
            cur = None
    return None


def _owned_row(root: Path, path) -> bool:
    reg = base.read_json(state.path(root, "sessions.json"), {})
    rows = reg.get("sessions") if isinstance(reg, dict) else None
    return any(isinstance(r, dict) and r.get("owned") is True and r.get("worktree")
               and ident.same_path(Path(r["worktree"]), Path(path)) for r in rows or [])


def superlite_cut(root: Path, path) -> bool:
    """True when superlite cut `path` and may remove it. Old homes: always. New home: never a harness name or
    branch (agent-*, worktree-*, claude/*); else a registry row with owned:true, or a superlite name
    (sessions-<slug>, <topic>-proto, merge-<base>) whose branch, when known, starts `superlite/`."""
    if _under(path, state.path(root, "worktrees")) or _under(path, state.path(root, "sandboxes")):
        return True
    if not _under(path, home_path(root)) or ident.same_path(Path(path), home_path(root)):
        return False
    branch = _branch_of(root, path)
    if harness_name(path, branch):
        return False
    return _owned_row(root, path) or superlite_named(path, branch)
