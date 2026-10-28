#!/usr/bin/env python3
"""Checkpoint one or more tasks: record only each task's Owns files, block anything else.

    checkpoint.py Tn [Tm ...] [--plan P] [--ledger .superlite/progress.md]
                  [--log .superlite/incidents.jsonl] [--allow-other-tasks]
                  [--dispatch [--pool .superlite/pool.json] [--running T3] [--cap 10] [--batch N]]

Several ids are checkpointed in order in one process (one plan parse, one exclude check).
Output: the SHA for one task; `Tn <sha>` or `Tn FAILED` per task for several. Each
checkpointed task's agent in the pool (`current: Tn`) gets `current` cleared, `busy: false`,
and Tn in its `tasks`. `--dispatch` then prints `next:` and the `dispatch` lines (ready tasks
and agents) for the updated ledger; a dispatch failure only warns, the checkpoints stand.

A checkpoint is a hidden save of one task: a shadow git object (never on HEAD, the branch
or the user's index) stored under refs/superlite/<run>/<Tn>; its SHA is what the ledger,
probes and seam checks use. `sl autocommit on` (or SUPERLITE_AUTOCOMMIT=1) makes it a real
commit on the branch instead. `commit_task.py` is the old name, kept as a shim.

Steps: scope check (changes outside every task's Owns block the checkpoint and are logged as
incidents; files another task owns are left out, silently), py_compile plus an import check
on each touched .py file, checkpoint with the task title, append `- checkpoint Tn <sha>
<title>` to the progress ledger, then launch in the background `sl probe_run --checkpoint
SHA` and, for every seam of the task graph (`sl plan_tools taskgraph --format full`) that
involves this task and whose both sides are now checkpointed per the ledger, `sl pt_review
contract --plan P --seam Ta,Tb --log LOG` (mismatches become incidents, a pass logs
`verified`). Prints the SHA, or exits 1 listing the problem files. Run it from the
repository root.

Scope rules: `tests/regression/` (where probe_run writes `test_probe_*.py`) is owned
by whichever task is being checkpointed, so the task or review that fixes a defect records
the regression test it caused. `.superlite/` is never staged and never blocks; add it
to the repo's `.git/info/exclude` at setup (`setup_exclude()` here does it, and
checkpoint also does it on every run). Stdlib only.
"""

# Complexity notation:
# n = tasks in the plan, e = dependency edges, L = plan text size
# m = changed files in the working tree, m_py = touched .py files
# f = Owns paths of this task, o = Owns paths across the whole plan (~n*f)
# s = seams in the task graph (<= e), c = lines in the progress ledger, x = lines in .git/info/exclude
# "process spawn" = fork/exec of git, sl or python; dominates wall time over the O(...) CPU term
import argparse  # O(1)
import json  # O(1)
import os  # O(1)
import py_compile  # O(1)
import re  # O(1)
import subprocess  # O(1)
import sys  # O(1)
import tempfile  # O(1)
from pathlib import Path  # O(1)

sys.path.insert(0, str(Path(__file__).resolve().parent))  # O(1)
import next_tasks as nt  # noqa: E402 | O(1)
import autocommit  # noqa: E402 | O(1)
import gitignore  # noqa: E402 | O(1)

DEFAULT_LEDGER = ".superlite/progress.md"  # O(1)
DEFAULT_LOG = ".superlite/incidents.jsonl"  # O(1)
# Never staged, never blocking: today's entries plus every language's dependency/build/cache path in config/gitignore.json,
# so a late or missing .gitignore cannot block a task on node_modules, target, .venv ... (the plan's Ignore line joins in main).
IGNORED_PARTS = tuple(dict.fromkeys((".superlite/", "__pycache__/", *(e for e in gitignore.union_entries() if e.endswith("/") and "*" not in e))))  # O(e)
PLAN_IGNORE: list[str] = []  # entries of the plan's `**Ignore:**` header line, set once in main | O(1)
OUT_OF_SCOPE_SHOWN = 20  # paths printed per blocked checkpoint | O(1)
HINT_DIR_FILES = 20  # more blocked files than this under one top-level directory: print the .gitignore hint | O(1)
INCIDENT_PATHS = 5  # paths named in each grouped incident | O(1)
SHARED_OWNS = ("tests/regression/",)  # generated probe regression tests: owned by the task being checkpointed | O(1)
# The ledger line for one saved task, `- checkpoint Tn <sha> <title>`; groups: task, sha, title. Old ledgers and
# history rows wrote `- commit Tn ...`, so both words read. Every ledger reader imports this one regex.
LEDGER_LINE = re.compile(r"^\s*[-*+]?\s*(?:checkpoint|commit)\s+(T\d+[A-Za-z]?)\s+([0-9a-f]{7,40})\s*(.*?)\s*$")  # O(1)
COMMIT_LINE = LEDGER_LINE  # old name, kept for one release | O(1)
TASK_ID = re.compile(r"T\d+[A-Za-z]?")  # O(1)

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
"""  # O(1)


def git(*args, check=True):  # O(output) + process spawn
    r = subprocess.run(["git", *args], capture_output=True, text=True,  # O(output) + process spawn
                       encoding="utf-8")  # O(1)
    if check and r.returncode != 0:  # O(1)
        nt.die(f"git {' '.join(args[:2])} failed: {(r.stderr or r.stdout).strip()}", 1)  # O(output)
    return r  # O(1)


def hard_ignored(path):  # O(len path)
    """Run bookkeeping and bytecode: never staged, even when a task's Owns would match."""
    p = "/" + path  # O(1)
    return path.endswith(".pyc") or "/.superlite/" in p or "/__pycache__/" in p  # O(len path)


def ignored(path):  # O(d + entries)
    return path.endswith(".pyc") or gitignore.ignored(path, PLAN_IGNORE)  # matcher built once per process | O(d + entries)


def claims(owns):  # O(f)
    """Predicate: does one of these Owns patterns name `path` explicitly? Patterns that start with `**` (Wave 0's
    `**/types.*`, `**/__init__.py`) are too generic to pull files out of dependency folders, so they never do."""
    explicit = [o for o in owns if not o.startswith("**")]  # O(f)
    return lambda path: nt.owns_match(path, explicit)  # O(f)


def changed_files(keep=None):  # O(m log m) + process spawn
    """Changed paths minus the never-blocking ones; a path `keep(path)` accepts (the task's own Owns) stays even when
    ignored, except run bookkeeping. Same single `git status`: no extra spawn."""
    r = git("status", "--porcelain", "-z", "--untracked-files=all", "--no-renames")  # O(m) output + process spawn
    files = []  # O(1)
    for entry in r.stdout.split("\0"):  # O(m) iterations
        if len(entry) > 3:  # O(1)
            path = entry[3:].replace("\\", "/")  # O(len path)
            if not ignored(path) or (keep and not hard_ignored(path) and keep(path)):  # O(d + entries + f)
                files.append(path)  # O(1)
    return sorted(set(files))  # O(m log m)


def syntax_errors(files):  # O(total size of m_py files)
    errors = []  # O(1)
    with tempfile.TemporaryDirectory() as tmp:  # O(1) temp dir
        for f in files:  # O(m_py) iterations
            try:  # O(1)
                py_compile.compile(f, cfile=os.path.join(tmp, "x.pyc"), doraise=True)  # O(file size) compile
            except py_compile.PyCompileError as e:  # O(1)
                exc = e.exc_value  # O(1)
                line = getattr(exc, "lineno", 0) or 0  # O(1)
                msg = getattr(exc, "msg", None) or str(e)  # O(1)
                errors.append((f, line, f"SyntaxError: {msg}"))  # O(1)
    return errors  # O(1)


def import_errors(files):  # O(m_py * import cost) + 1 process spawn (batched)
    if not files:  # O(1)
        return []  # O(1)
    r = subprocess.run([sys.executable, "-I", "-c", IMPORT_DRIVER, ".", *files],  # O(output) + process spawn
                       capture_output=True, text=True, encoding="utf-8", timeout=120,  # O(1)
                       env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})  # O(1)
    try:  # O(1)
        out = json.loads(r.stdout.strip().splitlines()[-1])  # O(output)
    except (IndexError, json.JSONDecodeError):  # O(1)
        return [(files[0], 0, f"import check crashed: {r.stderr.strip()[-150:]}")]  # O(1)
    return [(f["file"], f["line"], f["error"]) for f in out]  # O(failures)


def is_module(path):  # O(len path)
    name = Path(path).name  # O(1)
    return (path.endswith(".py") and not name.startswith("test_")  # O(1)
            and not name.endswith("_test.py") and name != "conftest.py")  # O(1)


def append_ledger(ledger, line):  # O(1) file I/O
    p = Path(ledger)  # O(1)
    p.parent.mkdir(parents=True, exist_ok=True)  # O(1)
    new = not p.exists() or p.stat().st_size == 0  # O(1) stat
    with open(p, "a", encoding="utf-8", newline="\n") as f:  # O(1)
        if new:  # O(1)
            f.write("# Progress ledger\n\n")  # O(1)
        f.write(line + "\n")  # O(1)


def setup_exclude():  # O(x) + process spawn
    """Add `.superlite/` to the repo's .git/info/exclude (idempotent); True if it did."""
    r = git("rev-parse", "--git-path", "info/exclude", check=False)  # O(output) + process spawn
    if r.returncode != 0 or not r.stdout.strip():  # O(1)
        return False  # O(1)
    p = Path(r.stdout.strip())  # O(1)
    try:  # O(1)
        old = p.read_text(encoding="utf-8") if p.exists() else ""  # O(x) read
        if any(ln.strip() in (".superlite", ".superlite/") for ln in old.splitlines()):  # O(x)
            return False  # O(1)
        p.parent.mkdir(parents=True, exist_ok=True)  # O(1)
        with open(p, "a", encoding="utf-8", newline="\n") as f:  # O(1)
            f.write(("" if not old or old.endswith("\n") else "\n") + ".superlite/\n")  # O(1)
    except OSError:  # O(1)
        return False  # O(1)
    return True  # O(1)


def spawn_background(cmd):  # O(1) + process spawn
    kw = {}  # O(1)
    if os.name == "nt":  # O(1)
        kw["creationflags"] = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | NEW_GROUP (not DETACHED: it makes NO_WINDOW ignored and console children pop windows) | O(1)
    else:  # O(1)
        kw["start_new_session"] = True  # O(1)
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,  # O(1) + process spawn
                     stderr=subprocess.DEVNULL, **kw)  # O(1)


def probe_categories(plan):  # O(L + categories)
    """Union of the Review Focus categories in the plan, comma separated ("" if none).

    There is no plan or progress source for the app type, so --app-type is not passed."""
    try:  # O(1)
        import pt_review  # O(1) after first import
        cats = []  # O(1)
        for _pkg, cs in pt_review.focus_packages(pt_review.parse_plan(plan)):  # O(L) parse; O(packages) iterations
            cats += [c for c in cs if c not in cats]  # O(categories^2), categories <= 6
        return ",".join(cats)  # O(categories)
    except Exception:  # O(1)
        return ""  # O(1)


def launch_probes(sha, plan=None):  # O(L) + process spawn
    probe = Path(os.environ.get("SUPERLITE_PROBE_RUN")  # O(1)
                 or nt.SCRIPTS.parent / "probes" / "probe_run.py")  # O(1)
    if not probe.exists():  # O(1)
        return False  # O(1)
    extra = []  # O(1)
    cats = probe_categories(plan) if plan else ""  # O(L)
    if cats:  # O(1)
        extra = ["--categories", cats]  # O(1)
    spawn_background(nt.sl_command("probe_run", "--checkpoint", sha, *extra))  # O(1) + process spawn
    return True  # O(1)


def release_agents(pool_path, tids):  # O(pool bytes)
    """In pool.json, the agent whose `current` is a checkpointed task: clear `current`, set
    `busy: false`, add the task to its `tasks`. No pool file: nothing to do."""
    p = Path(pool_path)  # O(1)
    if not tids or not p.exists():  # O(1)
        return  # O(1)
    try:  # O(1)
        pool = json.loads(p.read_text(encoding="utf-8"))  # O(pool bytes)
    except (OSError, ValueError) as e:  # O(1)
        print(f"warning: pool not updated: {e}", file=sys.stderr)  # O(1)
        return  # O(1)
    done = set(tids)  # O(t)
    changed = False  # O(1)
    for ag in pool.get("agents", []):  # O(a) iterations
        cur = ag.get("current") or []  # "Tn", a batch "Tn,Tm", or a list | O(1)
        curs = [str(x).strip() for x in (cur if isinstance(cur, list) else str(cur).split(",")) if str(x).strip()]  # O(len)
        hit = [t for t in curs if t in done]  # O(batch)
        if not hit:  # O(1)
            continue  # O(1)
        for t in hit:  # O(batch) iterations
            if t not in ag.setdefault("tasks", []):  # O(tasks per agent)
                ag["tasks"].append(t)  # O(1)
        left = [t for t in curs if t not in done]  # a FAILED batch task keeps the agent on it | O(batch)
        if left:  # O(1)
            ag["current"] = ",".join(left)  # O(batch)
        else:  # O(1)
            ag.pop("current", None)  # O(1)
            ag["busy"] = False  # O(1)
        changed = True  # O(1)
    if changed:  # O(1)
        tmp = p.with_name(p.name + ".tmp")  # O(1)
        tmp.write_text(json.dumps(pool, indent=1) + "\n", encoding="utf-8")  # O(pool bytes)
        tmp.replace(p)  # O(1) atomic rename


def checkpointed_tasks(ledger):  # O(c)
    """Task ids with a checkpoint line (either word) in the ledger."""
    p = Path(ledger)  # O(1)
    if not p.exists():  # O(1)
        return set()  # O(1)
    out = set()  # O(1)
    for ln in p.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n"):  # O(c) iterations
        m = LEDGER_LINE.match(ln)  # O(1) regex
        if m:  # O(1)
            out.add(m.group(1))  # O(1)
    return out  # O(1)


committed_tasks = checkpointed_tasks  # old name, kept for one release | O(1)


def seams(plan):  # O(n + e), plan parsed once per process (nt.taskgraph_full)
    """[(producer, consumer)] from the task graph; [] on failure."""
    try:  # O(1)
        data = nt.taskgraph_full(plan)  # O(L + n + e) first call, O(1) after
    except SystemExit:  # nt.die on a bad graph: checkpoint anyway, no seams | O(1)
        print("warning: no seams (taskgraph failed)", file=sys.stderr)  # O(1)
        return []  # O(1)
    pairs = []  # O(1)
    for s in data.get("seams") or []:  # O(s) iterations
        ids = TASK_ID.findall(s)  # O(len seam)
        if len(ids) == 2:  # O(1)
            pairs.append((ids[0], ids[1]))  # O(1)
    if not pairs:  # seams missing from the output: derive them from the graph edges | O(1)
        for t, deps in (data.get("graph") or {}).items():  # O(n) iterations
            pairs += [(d, t) for d in deps]  # O(deps) per task, O(e) total
    return pairs  # O(1)


def launch_seam_checks(plan, task, ledger, log):  # O(c + s) + process spawn per matching seam
    """Background `pt_review contract --seam` for each seam of `task` now fully checkpointed.

    Seams that do not involve `task` were launched when their own later side was checkpointed."""
    done = checkpointed_tasks(ledger) | {task}  # O(c)
    launched = []  # O(1)
    for a_, b_ in seams(plan):  # O(s) iterations
        if task in (a_, b_) and a_ in done and b_ in done:  # O(1)
            spawn_background(nt.sl_command("pt_review", "contract", "--plan", plan,  # O(1) + process spawn
                                           "--seam", f"{a_},{b_}", "--log", log))  # O(1)
            launched.append(f"{a_},{b_}")  # O(1)
    return launched  # O(1)


def log_incidents(log, task, items):  # O(k) + one append (no spawn in process)
    """items: (file, line, msg) -> one batched incident write."""
    nt.add_incidents(log, [(f, line, f"{task}: {msg}", "exception", "checkpoint", "open")  # O(k)
                           for f, line, msg in items])


def group_by_top_dir(files):  # O(m)
    """{top-level directory: [paths]} in first-seen order; a file at the repo root is its own group."""
    groups = {}  # O(1)
    for f in files:  # O(m)
        groups.setdefault(f.split("/", 1)[0] if "/" in f else f, []).append(f)  # O(len path)
    return groups  # O(1)


def incident_row(key, files):  # O(1)
    """(file, line, message) for one group: the path itself for a lone file, else `dir/` with the count and the first paths."""
    msg = "change outside Owns blocked by checkpoint; nothing saved"  # O(1)
    if len(files) == 1 and files[0] == key:  # O(1)
        return key, 0, msg  # O(1)
    first = ", ".join(files[:INCIDENT_PATHS])  # O(1)
    return key + "/", 0, f"{msg}: {len(files)} files under {key}/, first {min(len(files), INCIDENT_PATHS)}: {first}"  # O(1)


def checkpoint_one(a, tid, info, own_files):  # O(m*o + py size) + git spawns (status, add, commit, rev-parse)
    """Checkpoint one task; (exit code, sha or None). Files other tasks own are left alone."""
    if tid not in info:  # O(1)
        print(f"error: unknown task {tid}", file=sys.stderr)  # O(1)
        return 2, None  # O(1)
    task = info[tid]  # O(1)
    owns = task["owns"]  # O(1)
    if not owns:  # O(1)
        print(f"error: {tid} has no Owns line", file=sys.stderr)  # O(1)
        return 2, None  # O(1)
    owns = owns + [o for o in SHARED_OWNS if o not in owns]  # O(f)
    other = [o for t, i in info.items() if t != tid for o in i["owns"]  # O(o)
             if o not in SHARED_OWNS]

    if getattr(a, "gitignore_owner", None):  # the coordinator's script edited .gitignore: only the Wave 0 task takes it | O(1)
        if tid == a.gitignore_owner:  # O(1)
            owns = owns + [".gitignore"]  # O(f)
        else:  # O(1)
            other = other + [".gitignore"]  # O(o)

    mine, out_of_scope = [], []  # O(1)
    for f in changed_files(claims(owns)):  # O(m) iterations; changed_files is O(m log m) + git spawn
        if f in own_files and not nt.owns_match(f, owns):  # O(f)
            continue  # O(1)
        if tid == "T0" and nt.owns_match(f, other):  # Wave 0 globs never take another task's file | O(f)
            continue  # O(1)
        if nt.owns_match(f, owns):  # O(f)
            mine.append(f)  # O(1)
        elif nt.owns_match(f, other):  # O(o)
            continue  # another task's file, built in parallel in this tree: leave it alone | O(1)
        else:  # O(1)
            out_of_scope.append(f)  # O(1)

    if out_of_scope:  # O(1)
        for f in out_of_scope[:OUT_OF_SCOPE_SHOWN]:  # O(1) (capped)
            print(f"out-of-scope {f}")  # O(1)
        if len(out_of_scope) > OUT_OF_SCOPE_SHOWN:  # O(1)
            print(f"... and {len(out_of_scope) - OUT_OF_SCOPE_SHOWN} more")  # O(1)
        groups = group_by_top_dir(out_of_scope)  # O(m)
        for d, fs in groups.items():  # O(groups)
            if len(fs) > HINT_DIR_FILES:  # O(1)
                print(f"hint: {len(fs)} blocked files under {d}/; likely a dependency or build folder; add it to .gitignore")  # O(1)
        log_incidents(a.log, tid, [incident_row(d, fs) for d, fs in groups.items()])  # one row per top-level directory | O(groups)
        return 1, None  # O(1)
    if not mine:  # O(1)
        print(f"nothing to checkpoint for {tid}", file=sys.stderr)  # O(1)
        return 1, None  # O(1)

    present = [f for f in mine if os.path.exists(f)]  # O(m) stat
    py = [f for f in present if f.endswith(".py")]  # O(m)
    problems = syntax_errors(py)  # O(size of py files)
    if not problems:  # O(1)
        problems = import_errors([f for f in py if is_module(f)])  # O(m_py * import cost) + process spawn
    if problems:  # O(1)
        for f, line, msg in problems:  # O(problems) iterations
            print(f"{'syntax' if msg.startswith('SyntaxError') else 'import'} "  # O(1)
                  f"{f}:{line} {msg}")  # O(1)
        log_incidents(a.log, tid, [(f, line, f"{msg}; nothing saved")  # O(problems) one append
                                   for f, line, msg in problems])
        return 1, None  # O(1)

    if autocommit.is_on():  # opt-in: a real commit on the user's branch | O(1)
        git("add", "-A", "--", *mine)  # O(m) + process spawn
        title = f"{tid}: {task['title']}"  # O(1)
        r = git("commit", "-m", title, check=False)  # O(1) + process spawn
        if r.returncode != 0:  # O(1)
            print((r.stderr or r.stdout).strip(), file=sys.stderr)  # O(1)
            return 1, None  # O(1)
        sha = git("rev-parse", "HEAD").stdout.strip()  # O(1) + process spawn
    else:  # default: shadow checkpoint under refs/superlite, HEAD/branch/index/tree untouched | O(m) + git spawns
        sha = autocommit.checkpoint(tid, task["title"], mine, a.plan)  # O(m) + process spawn
    append_ledger(a.ledger, f"- checkpoint {tid} {sha[:10]} {task['title']}")  # O(1) file I/O
    launch_probes(sha, a.plan)  # O(L) + process spawn
    launch_seam_checks(a.plan, tid, a.ledger, a.log)  # O(c + s) + process spawn per seam (graph memoized)
    return 0, sha  # O(1)


commit_one = checkpoint_one  # old name, kept for one release | O(1)


def _sl_state(plan, name):  # O(1)
    """Session (or legacy flat) path for questions.json / blocked.json — same dir the hooks read."""
    try:  # O(1)
        import session as sl_session  # O(1)
        return sl_session.state_dir(plan) / name  # O(S)
    except Exception:  # noqa: BLE001 | O(1)
        return Path(".superlite") / name  # O(1)


def write_questions(plan, task, agent=""):  # O(1)
    path = _sl_state(plan, "questions.json")  # O(1)
    path.parent.mkdir(parents=True, exist_ok=True)  # O(1)
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"waiting": {}}  # O(file)
    data.setdefault("waiting", {})[task] = {"agent": agent}  # O(1)
    path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")  # O(1)


def clear_question(plan, task):  # O(1)
    path = _sl_state(plan, "questions.json")  # O(1)
    if not path.exists():  # O(1)
        return  # O(1)
    data = json.loads(path.read_text(encoding="utf-8"))  # O(file)
    data.get("waiting", {}).pop(task, None)  # O(1)
    path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")  # O(1)


def write_blocked(plan, task, tier):  # O(1)
    path = _sl_state(plan, "blocked.json")  # O(1)
    path.parent.mkdir(parents=True, exist_ok=True)  # O(1)
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"blocked": {}}  # O(file)
    data.setdefault("blocked", {})[task] = {"tier": tier}  # O(1)
    path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")  # O(1)


def main(argv=None):  # O(L + t*(m*o)) + git spawns per task; one process for all tasks
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])  # O(1)
    ap.add_argument("task", nargs="*", help="one or more task ids, checkpointed in order")  # O(1)
    ap.add_argument("--plan", default=nt.DEFAULT_PLAN)  # O(1)
    ap.add_argument("--ledger", default=DEFAULT_LEDGER)  # O(1)
    ap.add_argument("--log", default=DEFAULT_LOG)  # O(1)
    ap.add_argument("--allow-other-tasks", action="store_true",  # O(1)
                    help="no-op alias (kept for compatibility): other tasks' files are always left alone")  # O(1)
    ap.add_argument("--dispatch", action="store_true",  # O(1)
                    help="after the checkpoints, print `dispatch` output (next tasks and agents)")  # O(1)
    ap.add_argument("--pool", default=".superlite/pool.json", help="pool for --dispatch")  # O(1)
    ap.add_argument("--running", help="running tasks for --dispatch (default: from the pool)")  # O(1)
    ap.add_argument("--cap", type=int, default=10, help="cap for --dispatch")  # O(1)
    ap.add_argument("--batch", type=int, help="most tasks per agent message for --dispatch (default: config; 1 = off)")  # O(1)
    ap.add_argument("--question", metavar="T", help="record that task T waits for a NEEDS_CONTEXT answer")  # O(1)
    ap.add_argument("--answered", metavar="T", help="clear the question wait for task T")  # O(1)
    ap.add_argument("--blocked", metavar="T", help="record that task T is BLOCKED")  # O(1)
    ap.add_argument("--tier", help="required fixer tier for --blocked (haiku|sonnet|opus)")  # O(1)
    ap.add_argument("--agent", default="", help="implementer id for --question")  # O(1)
    a = ap.parse_args(argv)  # O(1)
    import session as sl_session  # ledger/log/pool follow the active session | O(1)
    sd = sl_session.state_dir(a.plan)  # O(S)
    if a.ledger == DEFAULT_LEDGER:  # O(1)
        a.ledger = sl_session.display_path(sd / "progress.md")  # O(1)
    if a.log == DEFAULT_LOG:  # O(1)
        a.log = sl_session.display_path(sd / "incidents.jsonl")  # O(1)
    if a.pool == ".superlite/pool.json":  # O(1)
        a.pool = sl_session.display_path(sd / "pool.json")  # O(1)

    if a.question:  # O(1)
        write_questions(a.plan, a.question, a.agent)  # O(1)
        print(f"question {a.question}")  # O(1)
        if not a.task:  # O(1)
            return 0  # O(1)
    if a.answered:  # O(1)
        clear_question(a.plan, a.answered)  # O(1)
        print(f"answered {a.answered}")  # O(1)
        if not a.task:  # O(1)
            return 0  # O(1)
    if a.blocked:  # O(1)
        if not a.tier:  # O(1)
            nt.die("--blocked needs --tier")  # O(1)
        write_blocked(a.plan, a.blocked, a.tier)  # O(1)
        print(f"blocked {a.blocked} tier={a.tier}")  # O(1)
        if not a.task:  # O(1)
            return 0  # O(1)
    if not a.task:  # O(1)
        nt.die("checkpoint needs a task id (or --question/--answered/--blocked)")  # O(1)

    info = nt.parse_plan(a.plan)  # O(L) parse plan, once for every task
    if "T0" not in info:  # a Wave 0 task synthesized by `wave0` | O(1)
        import wave0  # O(1) lazy
        w0 = wave0.synth_info(a.plan)  # O(1) file read
        if w0:  # O(1)
            info["T0"] = {"id": "T0", "title": w0["title"], "owns": w0["owns"], "body": ""}  # O(1)
    unknown = [t for t in a.task if t not in info]  # O(t)
    if len(a.task) == 1 and unknown:  # O(1)
        nt.die(f"unknown task {unknown[0]}")  # O(1)
    if len(a.task) == 1 and not info[a.task[0]]["owns"]:  # O(1)
        nt.die(f"{a.task[0]} has no Owns line")  # O(1)
    setup_exclude()  # O(x) + process spawn, once
    try:  # the plan's Ignore line joins the never-blocking list | O(L)
        import plan_tools  # O(1)
        PLAN_IGNORE[:] = plan_tools.Plan.load(a.plan).header_ignore() or []  # O(L)
    except Exception:  # noqa: BLE001 | O(1)
        pass  # O(1)
    import wave0  # O(1)
    a.gitignore_owner = wave0.gitignore_owner(a.plan)  # the Wave 0 task checkpoints the .gitignore its script wrote | O(1)

    own_files = set()  # bookkeeping files of the run itself, never in scope | O(1)
    for p in (a.plan, a.ledger, a.log):  # O(1) iterations (3 paths)
        try:  # O(1)
            own_files.add(Path(p).resolve().relative_to(Path.cwd().resolve()).as_posix())  # O(1)
        except ValueError:  # O(1)
            pass  # O(1)

    worst = 0  # O(1)
    saved = []  # O(1)
    for tid in a.task:  # O(t) iterations
        code, sha = checkpoint_one(a, tid, info, own_files)  # see checkpoint_one
        worst = max(worst, code)  # O(1)
        if sha:  # O(1)
            saved.append(tid)  # O(1)
            print(sha if len(a.task) == 1 else f"{tid} {sha}")  # O(1)
        elif len(a.task) > 1:  # O(1)
            print(f"{tid} FAILED")  # O(1)
    release_agents(a.pool, saved)  # O(pool bytes)

    if a.dispatch:  # O(1)
        print("next:")  # O(1)
        try:  # the checkpoints stand whatever happens here | O(1)
            import dispatch  # O(1) lazy: dispatch imports this module
            dispatch.render(*dispatch.run(a.plan, a.pool, a.ledger, None, a.running, a.cap, a.batch))  # see dispatch.run
        except (SystemExit, Exception) as e:  # O(1)
            print(f"warning: dispatch failed ({e}); run `dispatch` after fixing it", file=sys.stderr)  # O(1)
    return worst  # O(1)


if __name__ == "__main__":  # O(1)
    sys.exit(main())  # O(1)
