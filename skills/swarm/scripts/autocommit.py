#!/usr/bin/env python3
"""Opt-in auto-commit and hidden checkpoints for superlite runs.

    autocommit.py on|off|status
    autocommit.py apply      make real commits on the current branch from the checkpoints
    autocommit.py clean      delete refs/superlite/* and the checkpoint state

Default is OFF: `sl checkpoint` then records a hidden checkpoint (never on HEAD, the
branch or the user's index) under refs/superlite/<run>/<Tn> (or refs/superlite/<session>/…)
and the work stays uncommitted in the tree. `on` (or SUPERLITE_AUTOCOMMIT=1, which overrides
the file) makes `sl checkpoint` make a real commit on the current branch. State:
session-scoped `.superlite/checkpoints/<session>/` when a session is active (survives
`session end` so `apply` still works after finish), else flat `.superlite/checkpoints.json`.
Stdlib only; commits use the user's git identity, no trailers.
"""
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gitignore  # noqa: E402
import session as sl_session  # noqa: E402

CONFIG = ".superlite/autocommit.json"
REF_ROOT = "refs/superlite"
TRUE, FALSE = ("1", "true", "yes", "on"), ("0", "false", "no", "off")


def paths(plan=None):
    """Checkpoint state/index/ref-root for this plan or the active session.

    Session runs store under project `.superlite/checkpoints/<id>/` (not inside
    `sessions/<id>/`) so `sl autocommit apply` still works after `sl finish` deletes
    the session tree. Legacy flat layout keeps `.superlite/checkpoints.json`.
    """
    sid = sl_session.id_from_path(plan) if plan is not None else None
    if not sid:
        sid = sl_session.current_id(plan)
    base = sl_session.project_sl(plan)
    if sid:
        d = base / "checkpoints" / sid
        return d / "checkpoints.json", d / "checkpoint.index", f"{REF_ROOT}/{sid}"
    return base / "checkpoints.json", base / "checkpoint.index", REF_ROOT


def git(*args, env=None, check=True):
    r = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, **env} if env else None)
    if check and r.returncode != 0:
        sys.exit(f"git {' '.join(args[:2])} failed: {(r.stderr or r.stdout).strip()}")
    return r


def is_on():
    """Env SUPERLITE_AUTOCOMMIT wins, then .superlite/autocommit.json; default off."""
    env = os.environ.get("SUPERLITE_AUTOCOMMIT", "").strip().lower()
    if env in TRUE:
        return True
    if env in FALSE and env:
        return False
    try:
        return bool(json.loads(Path(CONFIG).read_text(encoding="utf-8")).get("autocommit"))
    except (OSError, ValueError, AttributeError):
        return False


def set_on(value):
    p = Path(CONFIG)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"autocommit": bool(value)}) + "\n", encoding="utf-8")


def load_state(plan=None):
    state, _, _ = paths(plan)
    try:
        return json.loads(state.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_state(st, plan=None):
    state, _, _ = paths(plan)
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps(st, indent=1) + "\n", encoding="utf-8")


def head_sha():
    r = git("rev-parse", "--verify", "-q", "HEAD", check=False)
    return r.stdout.strip() if r.returncode == 0 else ""


def checkpoint(tid, title, files, plan):
    """Shadow-commit `files` on top of the previous checkpoint (HEAD for the first); return the sha.

    A temporary index holds the parent's tree plus the owned files, so HEAD, the branch,
    the user's index and the working tree are not touched."""
    state, index, ref_root = paths(plan)
    st = load_state(plan)
    if not st or st.get("plan") != plan:
        stem = Path(plan).stem if plan else "run"
        st = {"plan": plan, "run": f"{stem}-{date.today():%Y%m%d}", "head": head_sha(),
              "branch": git("symbolic-ref", "-q", "HEAD", check=False).stdout.strip(),
              "entries": [], "ref_root": ref_root}
    parent = st["entries"][-1]["sha"] if st["entries"] else st["head"]
    idx = str(index.resolve())
    index.parent.mkdir(parents=True, exist_ok=True)
    Path(idx).unlink(missing_ok=True)
    env = {"GIT_INDEX_FILE": idx}
    try:
        git("read-tree", parent, env=env) if parent else git("read-tree", "--empty", env=env)
        git("add", "-A", "--", *files, env=env)
        tree = git("write-tree", env=env).stdout.strip()
    finally:
        Path(idx).unlink(missing_ok=True)
    cmd = ["commit-tree", tree, "-m", f"{tid}: {title}"] + (["-p", parent] if parent else [])
    sha = git(*cmd).stdout.strip()
    git("update-ref", f"{ref_root}/{st['run']}/{tid}", sha)
    st["ref_root"] = ref_root
    st["entries"].append({"task": tid, "sha": sha, "title": f"{tid}: {title}", "files": files})
    save_state(st, plan)
    return sha


def refs(plan=None):
    _, _, ref_root = paths(plan)
    r = git("for-each-ref", "--format=%(refname)", ref_root, check=False)
    return r.stdout.split()


def dirty_outside(files):
    r = git("status", "--porcelain", "-z", "--untracked-files=all", "--no-renames")
    bad = []
    for e in r.stdout.split("\0"):
        if len(e) > 3:
            p = e[3:].replace("\\", "/")
            if not p.endswith(".pyc") and p not in files and not gitignore.ignored(p):  # checkpoint's never-blocking list
                bad.append(p)
    return bad


def apply(plan=None):
    st = load_state(plan)
    if not st or not st.get("entries"):
        sys.exit("apply: no checkpoints recorded (nothing to commit)")
    if head_sha() != st["head"]:
        sys.exit(f"apply: refused, HEAD moved since the run started "
                 f"({st['head'][:10] or 'unborn'} -> {head_sha()[:10] or 'unborn'}); "
                 "commit the files yourself with `git add -A && git commit`")
    if git("symbolic-ref", "-q", "HEAD", check=False).stdout.strip() != st.get("branch", ""):
        sys.exit("apply: refused, the checked-out branch changed since the run started")
    files = {f for e in st["entries"] for f in e["files"]}
    bad = dirty_outside(files)
    if bad:
        sys.exit("apply: refused, the tree has changes unrelated to the run: "
                 + ", ".join(bad[:8]) + (" ..." if len(bad) > 8 else "")
                 + "; commit or stash them first")
    parent = st["head"]
    for e in st["entries"]:
        tree = git("rev-parse", f"{e['sha']}^{{tree}}").stdout.strip()
        parent = git("commit-tree", tree, "-m", e["title"],
                     *(["-p", parent] if parent else [])).stdout.strip()
    git("update-ref", "-m", "superlite: apply checkpoints", "HEAD", parent, *([st["head"]] if st["head"] else []))
    git("reset", "-q")  # index to the new HEAD; the working tree keeps the agents' files
    print(f"applied {len(st['entries'])} commit(s) on {st.get('branch') or 'HEAD'}:")
    for e in st["entries"]:
        print(f"- {e['title']}")
    clean(plan)
    return 0


def clean(plan=None):
    state, index, _ = paths(plan)
    for r in refs(plan):
        git("update-ref", "-d", r, check=False)
    state.unlink(missing_ok=True)
    index.unlink(missing_ok=True)
    if state.parent.name != ".superlite" and state.parent.is_dir() and not any(state.parent.iterdir()):
        try:
            state.parent.rmdir()
        except OSError:
            pass
    print("checkpoints removed")


def listing(plan=None):
    st = load_state(plan)
    if not st:
        return "no checkpoints"
    root = st.get("ref_root") or REF_ROOT
    out = [f"checkpoints for run {st['run']} (base {st['head'][:10]}, refs {root}/):"]
    out += [f"- {e['sha'][:10]} {e['title']} ({len(e['files'])} files)" for e in st["entries"]]
    return "\n".join(out)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else "status"
    if cmd in ("on", "off"):
        set_on(cmd == "on")
        print(f"autocommit {cmd}")
    elif cmd == "status":
        print(f"autocommit: {'on (commit each task on your branch)' if is_on() else 'off (hidden checkpoints, work stays uncommitted)'}")
        print(listing())
    elif cmd == "apply":
        return apply()
    elif cmd == "clean":
        clean()
    else:
        print(__doc__.split("\n")[0] + "\nusage: autocommit.py on|off|status|apply|clean", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
