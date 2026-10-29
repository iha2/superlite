#!/usr/bin/env python3
"""Split an open superlite run into worktrees so a second session can start without clobbering state.

    session.py split --new TOPIC [--keep TOPIC] [--base REF] [--root DIR]
    session.py status [--root DIR] [--json]

When a run is open (`.superlite/orchestrator.json`) and the user wants to start something else,
`split` moves the open run's `.superlite/` state and any uncommitted work into
`.superlite/worktrees/<keep>` on the keep branch, parks the main checkout on the base branch with
only a `sessions.json` registry, and cuts `.superlite/worktrees/<new>` on `superlite/<new>` for the
new session. `--keep` defaults to the current branch slug (`superlite/export` → `export`, else
`current`). On the default branch, keep uses a new branch `superlite/<keep>` at HEAD so the main
checkout can stay on base. Stdlib only.
"""
# Complexity notation:
#   mostly O(1) file work plus git spawns; S = size of .superlite tree moved
import argparse  # O(1)
import json  # O(1)
import re  # O(1)
import shutil  # O(1)
import subprocess  # O(1)
import sys  # O(1)
import tempfile  # O(1)
from pathlib import Path  # O(1)

HERE = Path(__file__).resolve().parent  # O(1)
if str(HERE) not in sys.path:  # O(1)
    sys.path.insert(0, str(HERE))  # O(1)

import gitignore  # noqa: E402  | O(1)

TOPIC = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")  # O(1)
FILE = "sessions.json"  # O(1)
ORCH = "orchestrator.json"  # O(1)


def git(*args, cwd=None, check=True):  # O(1) + process spawn
    r = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=cwd)  # O(out)
    if check and r.returncode != 0:  # O(1)
        sys.exit(f"session: git {' '.join(args[:2])} failed: {(r.stderr or r.stdout).strip()}")  # O(1)
    return r  # O(1)


def root_of(path: Path) -> Path:  # O(1) + process spawn
    return Path(git("rev-parse", "--show-toplevel", cwd=path).stdout.strip())  # O(1)


def default_base(root: Path) -> str:  # O(1) + process spawn
    for name in ("main", "master"):  # O(1)
        if git("rev-parse", "--verify", "-q", f"refs/heads/{name}", cwd=root, check=False).returncode == 0:  # O(1)
            return name  # O(1)
    return git("rev-parse", "--abbrev-ref", "HEAD", cwd=root).stdout.strip()  # O(1)


def slug_from_branch(branch: str) -> str:  # O(1)
    b = branch.strip()  # O(1)
    if b.startswith("superlite/"):  # O(1)
        b = b[len("superlite/"):]  # O(1)
    b = re.sub(r"[^a-z0-9-]+", "-", b.lower()).strip("-")  # O(len)
    return b if TOPIC.match(b or "") else "current"  # O(1)


def validate_topic(name: str, flag: str) -> str:  # O(1)
    if not TOPIC.match(name or ""):  # O(1)
        print(f"session: {flag} must be a short slug (a-z, 0-9, -)", file=sys.stderr)  # O(1)
        sys.exit(2)  # O(1)
    return name  # O(1)


def read_sessions(root: Path) -> dict | None:  # O(file)
    f = root / ".superlite" / FILE  # O(1)
    try:  # O(1)
        d = json.loads(f.read_text(encoding="utf-8"))  # O(file)
    except (OSError, ValueError):  # O(1)
        return None  # O(1)
    return d if isinstance(d, dict) and isinstance(d.get("sessions"), list) else None  # O(1)


def write_sessions(root: Path, sessions: list) -> None:  # O(1)
    d = root / ".superlite"  # O(1)
    d.mkdir(parents=True, exist_ok=True)  # O(1)
    (d / FILE).write_text(json.dumps({"v": 1, "sessions": sessions}, indent=1) + "\n", encoding="utf-8")  # O(1)


def cmd_status(a) -> int:  # O(1)
    root = Path(a.root).resolve() if a.root else root_of(Path.cwd())  # O(1)
    st = read_sessions(root)  # O(file)
    if a.json:  # O(1)
        print(json.dumps(st or {"v": 1, "sessions": []}))  # O(1)
        return 0  # O(1)
    if not st or not st["sessions"]:  # O(1)
        print("session: none (no .superlite/sessions.json)")  # O(1)
        return 0  # O(1)
    for s in st["sessions"]:  # O(n)
        print(f"session: {s.get('role', '?')} {s.get('topic')} -> {s.get('worktree')} on {s.get('branch')}")  # O(1)
    return 0  # O(1)


def cmd_split(a) -> int:  # O(S) + git spawns
    root = Path(a.root).resolve() if a.root else root_of(Path.cwd())  # O(1)
    sl = root / ".superlite"  # O(1)
    if not (sl / ORCH).is_file():  # O(1)
        print("session: no open run (.superlite/orchestrator.json missing); nothing to move", file=sys.stderr)  # O(1)
        return 2  # O(1)

    cur = git("rev-parse", "--abbrev-ref", "HEAD", cwd=root).stdout.strip()  # O(1)
    if cur == "HEAD":  # O(1)
        print("session: detached HEAD; check out a branch before splitting", file=sys.stderr)  # O(1)
        return 2  # O(1)

    new_topic = validate_topic(a.new, "--new")  # O(1)
    keep_topic = validate_topic(a.keep or slug_from_branch(cur), "--keep")  # O(1)
    if keep_topic == new_topic:  # O(1)
        print("session: --keep and --new must differ", file=sys.stderr)  # O(1)
        return 2  # O(1)

    base = a.base or default_base(root)  # O(1)
    keep_branch = cur if cur != base else f"superlite/{keep_topic}"  # O(1)
    new_branch = f"superlite/{new_topic}"  # O(1)
    wt_keep = sl / "worktrees" / keep_topic  # O(1)
    wt_new = sl / "worktrees" / new_topic  # O(1)
    if wt_keep.exists() or wt_new.exists():  # O(1)
        print(f"session: worktree path already exists ({wt_keep if wt_keep.exists() else wt_new})", file=sys.stderr)  # O(1)
        return 2  # O(1)
    if git("rev-parse", "--verify", "-q", f"refs/heads/{new_branch}", cwd=root, check=False).returncode == 0:  # O(1)
        print(f"session: branch {new_branch} already exists", file=sys.stderr)  # O(1)
        return 2  # O(1)

    gitignore.ensure(str(root), [])  # O(g)

    # Park .superlite outside the repo so worktree paths can be created under it again.
    tmp = Path(tempfile.mkdtemp(prefix="superlite-session-"))  # O(1)
    sl_tmp = tmp / "superlite"  # O(1)
    shutil.move(str(sl), str(sl_tmp))  # O(S)

    stashed = False  # O(1)
    try:  # O(1)
        dirty = bool(git("status", "--porcelain", cwd=root, check=False).stdout.strip())  # O(1)
        if dirty:  # O(1)
            git("stash", "push", "-u", "-m", "superlite-session-split", cwd=root)  # O(diff)
            stashed = True  # O(1)

        if cur == base:  # main checkout must leave the keep branch free for a worktree | O(1)
            if git("rev-parse", "--verify", "-q", f"refs/heads/{keep_branch}", cwd=root, check=False).returncode == 0:  # O(1)
                print(f"session: branch {keep_branch} already exists; pass --keep with another slug", file=sys.stderr)  # O(1)
                return _abort(root, sl_tmp, stashed, 2)  # O(S)
            git("branch", keep_branch, cwd=root)  # O(1)
        else:  # O(1)
            git("checkout", "-q", base, cwd=root)  # O(1)

        (root / ".superlite" / "worktrees").mkdir(parents=True, exist_ok=True)  # O(1)
        git("worktree", "add", str(wt_keep), keep_branch, cwd=root)  # O(tree)
        shutil.move(str(sl_tmp), str(wt_keep / ".superlite"))  # O(S)
        sl_tmp = None  # moved | O(1)

        if stashed:  # O(1)
            pop = git("stash", "pop", cwd=wt_keep, check=False)  # O(diff)
            if pop.returncode != 0:  # O(1)
                print(f"session: warning: stash pop in keep worktree failed: {(pop.stderr or pop.stdout).strip()}",
                      file=sys.stderr)  # O(1)
            stashed = False  # O(1)

        git("worktree", "add", "-b", new_branch, str(wt_new), base, cwd=root)  # O(tree)

        sessions = [  # O(1)
            {"topic": keep_topic, "worktree": wt_keep.as_posix(), "branch": keep_branch, "role": "previous"},
            {"topic": new_topic, "worktree": wt_new.as_posix(), "branch": new_branch, "role": "new"},
        ]
        write_sessions(root, sessions)  # O(1)
    except SystemExit:  # O(1)
        raise  # O(1)
    except Exception as e:  # O(1)
        print(f"session: split failed: {e}", file=sys.stderr)  # O(1)
        return _abort(root, sl_tmp, stashed, 1)  # O(S)
    finally:  # O(1)
        if tmp.exists() and not any(tmp.iterdir()):  # O(1)
            tmp.rmdir()  # O(1)

    print(f"session: previous {keep_topic} -> {wt_keep.as_posix()} on {keep_branch}")  # O(1)
    print(f"session: new {new_topic} -> {wt_new.as_posix()} on {new_branch}")  # O(1)
    print(f"session: main checkout on {base} has no open run; resume in the previous worktree, "
          f"start the new request in the new one")  # O(1)
    return 0  # O(1)


def _abort(root: Path, sl_tmp: Path | None, stashed: bool, code: int) -> int:  # O(S)
    """Best-effort restore of .superlite and stash when split fails mid-way."""
    if sl_tmp and sl_tmp.is_dir() and not (root / ".superlite" / ORCH).exists():  # O(1)
        dest = root / ".superlite"  # O(1)
        if dest.exists():  # O(1)
            shutil.rmtree(dest, ignore_errors=True)  # O(S)
        shutil.move(str(sl_tmp), str(dest))  # O(S)
    if stashed:  # O(1)
        git("stash", "pop", cwd=root, check=False)  # O(diff)
    return code  # O(1)


def main(argv=None) -> int:  # O(1)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])  # O(1)
    sp = ap.add_subparsers(dest="cmd", required=True)  # O(1)
    s = sp.add_parser("split", help="move the open run to a worktree and cut a new one")  # O(1)
    s.add_argument("--new", required=True, help="slug for the new session worktree/branch")  # O(1)
    s.add_argument("--keep", help="slug for the open run's worktree (default: current branch slug)")  # O(1)
    s.add_argument("--base", help="branch the main checkout returns to (default: main or master)")  # O(1)
    s.add_argument("--root", help="repo root (default: current)")  # O(1)
    t = sp.add_parser("status", help="list sessions recorded by split")  # O(1)
    t.add_argument("--root", help="repo root (default: current)")  # O(1)
    t.add_argument("--json", action="store_true")  # O(1)
    a = ap.parse_args(argv)  # O(1)
    sys.stdout.reconfigure(encoding="utf-8")  # O(1)
    return cmd_split(a) if a.cmd == "split" else cmd_status(a)  # O(1)


if __name__ == "__main__":  # O(1)
    sys.exit(main())  # O(1)
