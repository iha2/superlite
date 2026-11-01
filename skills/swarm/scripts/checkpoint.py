#!/usr/bin/env python3
"""Checkpoint one or more tasks: record only each task's Owns files, block anything else.

    checkpoint.py Tn [Tm ...] [--plan P] [--ledger .superlite/progress.md]
                  [--log .superlite/incidents.jsonl]
                  [--dispatch [--pool .superlite/pool.json] [--running T3] [--cap 10] [--batch N]]

Several ids are checkpointed in order in one process (one plan parse, one exclude check).
Output: the SHA for one task; `Tn <sha>` or `Tn FAILED` per task for several. Each
checkpointed task's agent in the pool (`current: Tn`) gets `current` cleared, `busy: false`,
and Tn in its `tasks`. `--dispatch` then prints `next:` and the `dispatch` lines (ready tasks
and agents) for the updated ledger; a dispatch failure only warns, the checkpoints stand.

A checkpoint is a hidden save of one task: a shadow git object (never on HEAD, the branch
or the user's index) stored under refs/superlite/<run>/<Tn>; its SHA is what the ledger,
probes and seam checks use. `superlite-cli finish` deletes the refs at the end of the run.

Steps: scope check (changes outside every task's Owns block the checkpoint and are logged as
incidents; files another task owns are left out, silently), py_compile plus an import check
on each touched .py file, checkpoint with the task title, append `- checkpoint Tn <sha>
<title>` to the progress ledger, then launch in the background `superlite-cli probe_run --checkpoint
SHA` and, for every seam of the task graph (`superlite-cli plan_tools taskgraph --format full`) that
involves this task and whose both sides are now checkpointed per the ledger, `superlite-cli pt_review
contract --plan P --seam Ta,Tb --log LOG` (mismatches become incidents, a pass logs
`verified`). Prints the SHA, or exits 1 listing the problem files. Run it from the
repository root.

Scope rules: `tests/regression/` (where probe_run writes `test_probe_*.py`) is owned
by whichever task is being checkpointed, so the task or review that fixes a defect records
the regression test it caused. `.superlite/` is never staged and never blocks; add it
to the repo's `.git/info/exclude` at setup (`setup_exclude()` here does it, and
checkpoint also does it on every run). Stdlib only.
"""

import argparse
import json
import os
import py_compile
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import next_tasks as nt  # noqa: E402
import base  # noqa: E402
import gitignore  # noqa: E402
import state  # noqa: E402

DEFAULT_LEDGER = f"{state.DIR_NAME}/progress.md"
DEFAULT_LOG = f"{state.DIR_NAME}/incidents.jsonl"
# Never staged, never blocking: today's entries plus every language's dependency/build/cache path in config/gitignore.json,
# so a late or missing .gitignore cannot block a task on node_modules, target, .venv ... (the plan's Ignore line joins in main).
IGNORED_PARTS = tuple(dict.fromkeys((f"{state.DIR_NAME}/", "__pycache__/", *(e for e in gitignore.union_entries() if e.endswith("/") and "*" not in e))))
PLAN_IGNORE: list[str] = []  # entries of the plan's `**Ignore:**` header line, set once in main
OUT_OF_SCOPE_SHOWN = 20  # paths printed per blocked checkpoint
HINT_DIR_FILES = 20  # more blocked files than this under one top-level directory: print the .gitignore hint
INCIDENT_PATHS = 5  # paths named in each grouped incident
SHARED_OWNS = ("tests/regression/",)  # generated probe regression tests: owned by the task being checkpointed
# The ledger line for one saved task, `- checkpoint Tn <sha> <title>`; groups: task, sha, title.
# Every ledger reader imports this one regex.
LEDGER_LINE = re.compile(r"^\s*[-*+]?\s*checkpoint\s+(T\d+[A-Za-z]?)\s+([0-9a-f]{7,40})\s*(.*?)\s*$")
STATE = f"{state.DIR_NAME}/checkpoints.json"
TEMP_INDEX = f"{state.DIR_NAME}/checkpoint.index"
REF_ROOT = "refs/superlite"
TASK_ID = re.compile(r"T\d+[A-Za-z]?")

IMPORT_DRIVER = r"""
import importlib, importlib.util, json, os, sys, traceback
sys.dont_write_bytecode = True
root = os.path.abspath(sys.argv[1])
files = sys.argv[2:]
sys.path.insert(0, root)
if os.path.isdir(os.path.join(root, "src")):
    sys.path.insert(1, os.path.join(root, "src"))
failures = []
for rel in files:
    path = os.path.join(root, rel)
    parts = rel.replace("\\", "/")[:-3].split("/")
    if parts[0] == "src" and os.path.isdir(os.path.join(root, "src")):
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if parts and parts[-1] == "__main__":
        continue  # importing it runs the program (SPECs often require an unguarded sys.exit(main())); the syntax check covers it
    try:
        if parts and all(p.isidentifier() for p in parts):
            importlib.import_module(".".join(parts))
        else:
            name = "_slcheck_" + "_".join(p.replace("-", "_") for p in parts)
            sys.path.append(os.path.dirname(path))
            spec = importlib.util.spec_from_file_location(name, path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
    except BaseException as e:
        line = 0
        for fr in traceback.extract_tb(e.__traceback__):
            if os.path.abspath(fr.filename) == os.path.abspath(path):
                line = fr.lineno
        failures.append({"file": rel, "line": line,
                         "error": f"{type(e).__name__}: {e}"[:150]})
print(json.dumps(failures))
"""


def git(*args, check=True, env=None):  # O(output) + process spawn
    r = subprocess.run(["git", *args], capture_output=True, text=True,
                       encoding="utf-8", env={**os.environ, **env} if env else None)
    if check and r.returncode != 0:
        nt.die(f"git {' '.join(args[:2])} failed: {(r.stderr or r.stdout).strip()}", 1)
    return r


SETTINGS_LOCAL = ".claude/settings.local.json"  # written by exclusive.py (peer suspension); no task owns it


def hard_ignored(path):  # O(len path)
    """Run bookkeeping and bytecode: never staged, even when a task's Owns would match."""
    p = "/" + path
    return path.endswith(".pyc") or f"/{state.DIR_NAME}/" in p or "/__pycache__/" in p or path == SETTINGS_LOCAL


def ignored(path):  # O(d + entries)
    return path.endswith(".pyc") or path == SETTINGS_LOCAL or gitignore.ignored(path, PLAN_IGNORE)  # matcher built once per process


def claims(owns):  # O(f)
    """Predicate: does one of these Owns patterns name `path` explicitly? Patterns that start with `**` (Wave 0's
    `**/types.*`, `**/__init__.py`) are too generic to pull files out of dependency folders, so they never do."""
    explicit = [o for o in owns if not o.startswith("**")]
    return lambda path: nt.owns_match(path, explicit)


def changed_files(keep=None):  # O(m log m) + process spawn
    """Changed paths minus the never-blocking ones; a path `keep(path)` accepts (the task's own Owns) stays even when
    ignored, except run bookkeeping. Same single `git status`: no extra spawn."""
    r = git("status", "--porcelain", "-z", "--untracked-files=all", "--no-renames")
    files = []
    for entry in r.stdout.split("\0"):
        if len(entry) > 3:
            path = entry[3:].replace("\\", "/")
            if not ignored(path) or (keep and not hard_ignored(path) and keep(path)):
                files.append(path)
    return sorted(set(files))


def syntax_errors(files):  # O(total size of m_py files)
    errors = []
    with tempfile.TemporaryDirectory() as tmp:
        for f in files:
            try:
                py_compile.compile(f, cfile=os.path.join(tmp, "x.pyc"), doraise=True)
            except py_compile.PyCompileError as e:
                exc = e.exc_value
                line = getattr(exc, "lineno", 0) or 0
                msg = getattr(exc, "msg", None) or str(e)
                errors.append((f, line, f"SyntaxError: {msg}"))
    return errors


def import_errors(files):  # O(m_py * import cost) + 1 process spawn (batched)
    if not files:
        return []
    r = subprocess.run([sys.executable, "-I", "-c", IMPORT_DRIVER, ".", *files],
                       capture_output=True, text=True, encoding="utf-8", timeout=120,
                       env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    try:
        out = json.loads(r.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return [(files[0], 0, f"import check crashed: {r.stderr.strip()[-150:]}")]
    return [(f["file"], f["line"], f["error"]) for f in out]


def is_module(path):  # O(len path)
    name = Path(path).name
    return (path.endswith(".py") and not name.startswith("test_")
            and not name.endswith("_test.py") and name != "conftest.py")


def append_ledger(ledger, line):
    p = Path(ledger)
    p.parent.mkdir(parents=True, exist_ok=True)
    new = not p.exists() or p.stat().st_size == 0
    with open(p, "a", encoding="utf-8", newline="\n") as f:
        if new:
            f.write("# Progress ledger\n\n")
        f.write(line + "\n")


def setup_exclude():  # O(x) + process spawn
    """Add `.superlite/` to the repo's .git/info/exclude (idempotent); True if it did."""
    r = git("rev-parse", "--git-path", "info/exclude", check=False)
    if r.returncode != 0 or not r.stdout.strip():
        return False
    p = Path(r.stdout.strip())
    try:
        old = p.read_text(encoding="utf-8") if p.exists() else ""
        if any(ln.strip() in (state.DIR_NAME, f"{state.DIR_NAME}/") for ln in old.splitlines()):
            return False
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8", newline="\n") as f:
            f.write(("" if not old or old.endswith("\n") else "\n") + f"{state.DIR_NAME}/\n")
    except OSError:
        return False
    return True


def spawn_background(cmd):
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | NEW_GROUP (not DETACHED: it makes NO_WINDOW ignored and console children pop windows)
    else:
        kw["start_new_session"] = True
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, **kw)


def probe_categories(plan):  # O(L + categories)
    """Union of the Review Focus categories in the plan, comma separated ("" if none).

    There is no plan or progress source for the app type, so --app-type is not passed."""
    try:
        import pt_review
        cats = []
        for _pkg, cs in pt_review.focus_packages(pt_review.parse_plan(plan)):
            cats += [c for c in cs if c not in cats]
        return ",".join(cats)
    except base.advisory_errors():
        return ""


def launch_probes(sha, plan=None):  # O(L) + process spawn
    probe = Path(os.environ.get("SUPERLITE_PROBE_RUN")
                 or nt.SCRIPTS.parent / "probes" / "probe_run.py")
    if not probe.exists():
        return False
    extra = []
    cats = probe_categories(plan) if plan else ""
    if cats:
        extra = ["--categories", cats]
    spawn_background(nt.sl_command("probe_run", "--checkpoint", sha, *extra))
    return True


def release_agents(pool_path, tids):  # O(pool bytes)
    """In pool.json, the agent whose `current` is a checkpointed task: clear `current`, set
    `busy: false`, add the task to its `tasks`. No pool file: nothing to do."""
    p = Path(pool_path)
    if not tids or not p.exists():
        return
    try:
        pool = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"warning: pool not updated: {e}", file=sys.stderr)
        return
    done = set(tids)
    changed = False
    for ag in pool.get("agents", []):
        cur = ag.get("current") or []  # "Tn", a batch "Tn,Tm", or a list
        curs = [str(x).strip() for x in (cur if isinstance(cur, list) else str(cur).split(",")) if str(x).strip()]
        hit = [t for t in curs if t in done]
        if not hit:
            continue
        for t in hit:
            if t not in ag.setdefault("tasks", []):
                ag["tasks"].append(t)
        left = [t for t in curs if t not in done]  # a FAILED batch task keeps the agent on it
        if left:
            ag["current"] = ",".join(left)
        else:
            ag.pop("current", None)
            ag["busy"] = False
        changed = True
    if changed:
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(pool, indent=1) + "\n", encoding="utf-8")
        tmp.replace(p)


def checkpointed_tasks(ledger):  # O(c)
    """Task ids with a checkpoint line in the ledger."""
    p = Path(ledger)
    if not p.exists():
        return set()
    out = set()
    for ln in p.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n"):
        m = LEDGER_LINE.match(ln)
        if m:
            out.add(m.group(1))
    return out


def seams(plan):  # O(n + e), plan parsed once per process (nt.taskgraph_full)
    """[(producer, consumer)] from the task graph; [] on failure."""
    try:
        data = nt.taskgraph_full(plan)
    except SystemExit:  # nt.die on a bad graph: checkpoint anyway, no seams
        print("warning: no seams (taskgraph failed)", file=sys.stderr)
        return []
    pairs = []
    for s in data.get("seams") or []:
        ids = TASK_ID.findall(s)
        if len(ids) == 2:
            pairs.append((ids[0], ids[1]))
    if not pairs:  # seams missing from the output: derive them from the graph edges
        for t, deps in (data.get("graph") or {}).items():
            pairs += [(d, t) for d in deps]
    return pairs


def launch_seam_checks(plan, task, ledger, log):  # O(c + s) + process spawn per matching seam
    """Background `pt_review contract --seam` for each seam of `task` now fully checkpointed.

    Seams that do not involve `task` were launched when their own later side was checkpointed."""
    done = checkpointed_tasks(ledger) | {task}
    launched = []
    for a_, b_ in seams(plan):
        if task in (a_, b_) and a_ in done and b_ in done:
            spawn_background(nt.sl_command("pt_review", "contract", "--plan", plan,
                                           "--seam", f"{a_},{b_}", "--log", log))
            launched.append(f"{a_},{b_}")
    return launched


def log_incidents(log, task, items):  # O(k) + one append (no spawn in process)
    """items: (file, line, msg) -> one batched incident write."""
    nt.add_incidents(log, [(f, line, f"{task}: {msg}", "exception", "checkpoint", "open")
                           for f, line, msg in items])


def group_by_top_dir(files):  # O(m)
    """{top-level directory: [paths]} in first-seen order; a file at the repo root is its own group."""
    groups = {}
    for f in files:
        groups.setdefault(f.split("/", 1)[0] if "/" in f else f, []).append(f)
    return groups


def incident_row(key, files):
    """(file, line, message) for one group: the path itself for a lone file, else `dir/` with the count and the first paths."""
    msg = "change outside Owns blocked by checkpoint; nothing saved"
    if len(files) == 1 and files[0] == key:
        return key, 0, msg
    first = ", ".join(files[:INCIDENT_PATHS])
    return key + "/", 0, f"{msg}: {len(files)} files under {key}/, first {min(len(files), INCIDENT_PATHS)}: {first}"


def head_sha():
    r = git("rev-parse", "--verify", "-q", "HEAD", check=False)
    return r.stdout.strip() if r.returncode == 0 else ""


def load_state():
    try:
        return json.loads(Path(STATE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_checkpoint(tid, title, files, plan):  # O(files) + git spawns
    """Shadow-commit `files` on top of the previous checkpoint (HEAD for the first); return the sha.

    A temporary index holds the parent's tree plus the owned files, so HEAD, the branch,
    the user's index and the working tree are not touched. The ref is refs/superlite/<run>/<Tn>."""
    st = load_state()
    if not st or st.get("plan") != plan:
        stem = Path(plan).stem if plan else "run"
        st = {"plan": plan, "run": f"{stem}-{date.today():%Y%m%d}", "head": head_sha(),
              "branch": git("symbolic-ref", "-q", "HEAD", check=False).stdout.strip(),
              "entries": []}
    parent = st["entries"][-1]["sha"] if st["entries"] else st["head"]
    idx = Path(TEMP_INDEX).resolve()
    idx.parent.mkdir(parents=True, exist_ok=True)
    idx.unlink(missing_ok=True)
    env = {"GIT_INDEX_FILE": str(idx)}
    try:
        git("read-tree", parent, env=env) if parent else git("read-tree", "--empty", env=env)
        git("add", "-A", "--", *files, env=env)
        tree = git("write-tree", env=env).stdout.strip()
    finally:
        idx.unlink(missing_ok=True)
    cmd = ["commit-tree", tree, "-m", f"{tid}: {title}"] + (["-p", parent] if parent else [])
    sha = git(*cmd).stdout.strip()
    git("update-ref", f"{REF_ROOT}/{st['run']}/{tid}", sha)
    st["entries"].append({"task": tid, "sha": sha, "title": f"{tid}: {title}", "files": files})
    p = Path(STATE)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(st, indent=1) + "\n", encoding="utf-8")
    return sha


PLAN_COMPANIONS = (".review.md", ".feature.md", ".mvp.md")  # the plans a run's plan was approved from


def approved_plans(plan):
    """The review/feature/mvp plan beside `docs/plans/<date>-<topic>.md`: part of the run, never in scope."""
    base_path = Path(plan)
    stem = base_path.name[:-3] if base_path.name.endswith(".md") else base_path.name
    return [base_path.with_name(stem + suffix) for suffix in PLAN_COMPANIONS]


def checkpoint_one(a, tid, info, own_files):  # O(m*o + py size) + git spawns (status, add, commit-tree, update-ref)
    """Checkpoint one task; (exit code, sha or None). Files other tasks own are left alone."""
    if tid not in info:
        print(f"error: unknown task {tid}", file=sys.stderr)
        return 2, None
    task = info[tid]
    owns = task["owns"]
    if not owns:
        print(f"error: {tid} has no Owns line", file=sys.stderr)
        return 2, None
    owns = owns + [o for o in SHARED_OWNS if o not in owns]
    other = [o for t, i in info.items() if t != tid for o in i["owns"]
             if o not in SHARED_OWNS]

    if getattr(a, "gitignore_owner", None):  # the coordinator's script edited .gitignore: only the Wave 0 task takes it
        if tid == a.gitignore_owner:
            owns = owns + [".gitignore"]
        else:
            other = other + [".gitignore"]

    mine, out_of_scope = [], []
    for f in changed_files(claims(owns)):
        if f in own_files and not nt.owns_match(f, owns):
            continue
        if tid == "T0" and nt.owns_match(f, other):  # Wave 0 globs never take another task's file
            continue
        if nt.owns_match(f, owns):
            mine.append(f)
        elif nt.owns_match(f, other):
            continue  # another task's file, built in parallel in this tree: leave it alone
        else:
            out_of_scope.append(f)

    if out_of_scope:
        for f in out_of_scope[:OUT_OF_SCOPE_SHOWN]:
            print(f"out-of-scope {f}")
        if len(out_of_scope) > OUT_OF_SCOPE_SHOWN:
            print(f"... and {len(out_of_scope) - OUT_OF_SCOPE_SHOWN} more")
        groups = group_by_top_dir(out_of_scope)
        for d, fs in groups.items():
            if len(fs) > HINT_DIR_FILES:
                print(f"hint: {len(fs)} blocked files under {d}/; likely a dependency or build folder; add it to .gitignore")
        log_incidents(a.log, tid, [incident_row(d, fs) for d, fs in groups.items()])  # one row per top-level directory
        return 1, None
    if not mine:
        print(f"nothing to checkpoint for {tid}", file=sys.stderr)
        return 1, None

    present = [f for f in mine if os.path.exists(f)]
    py = [f for f in present if f.endswith(".py")]
    problems = syntax_errors(py)
    if not problems:
        problems = import_errors([f for f in py if is_module(f)])
    if problems:
        for f, line, msg in problems:
            print(f"{'syntax' if msg.startswith('SyntaxError') else 'import'} "
                  f"{f}:{line} {msg}")
        log_incidents(a.log, tid, [(f, line, f"{msg}; nothing saved")
                                   for f, line, msg in problems])
        return 1, None

    sha = save_checkpoint(tid, task["title"], mine, a.plan)  # hidden ref: HEAD, branch, index and tree untouched
    append_ledger(a.ledger, f"- checkpoint {tid} {sha[:10]} {task['title']}")
    launch_probes(sha, a.plan)
    launch_seam_checks(a.plan, tid, a.ledger, a.log)
    return 0, sha


def _sl_state(plan, name):
    import plan_tools as pt
    return state.path(pt.stable_root(plan), name)


def write_questions(plan, task, agent=""):
    path = _sl_state(plan, "questions.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"waiting": {}}
    data.setdefault("waiting", {})[task] = {"agent": agent}
    path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")


def clear_question(plan, task):
    path = _sl_state(plan, "questions.json")
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    data.get("waiting", {}).pop(task, None)
    path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")


def write_blocked(plan, task, tier):
    path = _sl_state(plan, "blocked.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"blocked": {}}
    data.setdefault("blocked", {})[task] = {"tier": tier}
    path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")


def main(argv=None):  # O(L + t*(m*o)) + git spawns per task; one process for all tasks
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("task", nargs="*", help="one or more task ids, checkpointed in order")
    ap.add_argument("--plan", default=nt.DEFAULT_PLAN)
    ap.add_argument("--ledger", default=DEFAULT_LEDGER)
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--dispatch", action="store_true",
                    help="after the checkpoints, print `dispatch` output (next tasks and agents)")
    ap.add_argument("--pool", default=f"{state.DIR_NAME}/pool.json", help="pool for --dispatch")
    ap.add_argument("--running", help="running tasks for --dispatch (default: from the pool)")
    ap.add_argument("--cap", type=int, default=10, help="cap for --dispatch")
    ap.add_argument("--batch", type=int, help="most tasks per agent message for --dispatch (default: config; 1 = off)")
    ap.add_argument("--question", metavar="T", help="record that task T waits for a NEEDS_CONTEXT answer")
    ap.add_argument("--answered", metavar="T", help="clear the question wait for task T")
    ap.add_argument("--blocked", metavar="T", help="record that task T is BLOCKED")
    ap.add_argument("--tier", help="required fixer tier for --blocked (haiku|sonnet|opus)")
    ap.add_argument("--agent", default="", help="implementer id for --question")
    a = ap.parse_args(argv)

    if a.question:
        write_questions(a.plan, a.question, a.agent)
        print(f"question {a.question}")
        if not a.task:
            return 0
    if a.answered:
        clear_question(a.plan, a.answered)
        print(f"answered {a.answered}")
        if not a.task:
            return 0
    if a.blocked:
        if not a.tier:
            nt.die("--blocked needs --tier")
        write_blocked(a.plan, a.blocked, a.tier)
        print(f"blocked {a.blocked} tier={a.tier}")
        if not a.task:
            return 0
    if not a.task:
        nt.die("checkpoint needs a task id (or --question/--answered/--blocked)")

    info = nt.parse_plan(a.plan)
    if "T0" not in info:  # a Wave 0 task synthesized by `wave0`
        import wave0
        w0 = wave0.synth_info(a.plan)
        if w0:
            info["T0"] = {"id": "T0", "title": w0["title"], "owns": w0["owns"], "body": ""}
    unknown = [t for t in a.task if t not in info]
    if len(a.task) == 1 and unknown:
        nt.die(f"unknown task {unknown[0]}")
    if len(a.task) == 1 and not info[a.task[0]]["owns"]:
        nt.die(f"{a.task[0]} has no Owns line")
    setup_exclude()
    try:  # the plan's Ignore line joins the never-blocking list
        import plan_tools
        PLAN_IGNORE[:] = plan_tools.Plan.load(a.plan).header_ignore() or []
    except Exception:  # noqa: BLE001
        pass
    import wave0
    a.gitignore_owner = wave0.gitignore_owner(a.plan)  # the Wave 0 task checkpoints the .gitignore its script wrote

    own_files = set()  # bookkeeping files of the run itself, never in scope
    for p in (a.plan, a.ledger, a.log, *approved_plans(a.plan)):
        try:
            own_files.add(Path(p).resolve().relative_to(Path.cwd().resolve()).as_posix())
        except ValueError:
            pass

    worst = 0
    saved = []
    for tid in a.task:
        code, sha = checkpoint_one(a, tid, info, own_files)  # see checkpoint_one
        worst = max(worst, code)
        if sha:
            saved.append(tid)
            print(sha if len(a.task) == 1 else f"{tid} {sha}")
        elif len(a.task) > 1:
            print(f"{tid} FAILED")
    release_agents(a.pool, saved)

    if a.dispatch:
        print("next:")
        try:  # the checkpoints stand whatever happens here
            import dispatch
            dispatch.render(*dispatch.run(a.plan, a.pool, a.ledger, None, a.running, a.cap, a.batch))  # see dispatch.run
        except (SystemExit, Exception) as e:
            print(f"warning: dispatch failed ({e}); run `dispatch` after fixing it", file=sys.stderr)
    return worst


if __name__ == "__main__":
    sys.exit(main())
