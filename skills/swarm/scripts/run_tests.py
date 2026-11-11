#!/usr/bin/env python3
"""Run the project's test command (testcmd: .superlite/test-cmd.json, else detection; pytest for Python),
parse its junit XML or output, print one summary line.

    run_tests.py [--fast PATHS... | --full | --affected [--base REF] [--files F...]] [--reuse] [--log PATH] [--ledger PATH]
                 [--summary PATH] [--background]
                 [--expect SECONDS]
    run_tests.py --wait [--run ID] [--timeout S] [--patient] [--summary PATH]

--affected runs only the tests testcmd.select_affected picks for the changed files (git changes since
--base, default the last `- start <sha>` in the ledger, else HEAD; --files replaces git). The summary has
mode "affected", selected, reason, changed_count, untested and tree; a whole-suite trigger runs everything
(reason `full: <trigger>`), nothing selected is green with zero counts. Full and affected summaries carry `tree`
(tree_id.tree_hash). --reuse skips the run when --summary is a green full run with the same tree
(prints `reused: <path>`, exit 0).

Prints `passed failed skipped seconds` (errors count as failed). Each failing
test becomes a `test-failure` incident; on a --full run each test file whose
tests all pass becomes a `verified` incident (once per distinct entry). Appends
a `tests` line to the progress ledger. On every run it also writes
`.superlite/test-summary.json` (--summary), the file `pt_review packet` reads:
`{"passed": N, "failed": N, "skipped": N, "seconds": S, "mode": "fast|full",
"failures": ["file::test: message", ...], "skipped_tests": ["file::test", ...]}`
(a run where pytest produced nothing records failed=1 and the reason). Exit 0 when nothing failed, 1 when a
test failed or none ran, 2 when pytest could not run.

Hung-test probe (testprobe.py, thresholds in config/testprobe.json): once a run passes 3x its expected time
(--expect, else the last green run of the same mode, else 60 s) it samples output bytes, tree CPU and child
processes. Output and children flat with flat CPU is BLOCKED, with busy CPU SPINNING. That prints one
`SUSPECT HANG: ...` line, logs a `perf` incident and writes ADDITIVE summary fields (`verdict`, `suspect_hang`,
`running`); the run is never killed unless config says so. A killed or crashed run says so in `failures`
(`process killed or crashed (exit -1)`, `TIMEOUT: ...`), never a bare exit code.

--background starts a detached run of the same command, prints `wait: run_tests --wait --run <id>` then `summary: <path>` (last), and exits 0
at once. Each background run gets its own summary file (`test-summary-<id>.json` next to
--summary) and the pointer `test-summary.latest` (atomic) names the newest run; when a run
finishes it also publishes its summary to --summary, unless a newer run has started since.
Every summary carries `"run_id"` (the background run id; a foreground run makes one); --wait --run ID exits 2
with `run_tests: run id mismatch` when the file's run_id is not ID.
--wait blocks until the pointed run's own summary exists (an older summary never counts; --wait --run ID waits for run ID's own summary instead of the pointed one),
prints it, and exits 0 green, 1 red, 2 on timeout (`TIMEOUT`) or when no background run was started, and 3 as
soon as the probe reports a SUSPECT HANG (prints that line; the run keeps going; --patient waits on regardless).
Stdlib only.
"""
import argparse
import json
import os
import importlib.util
import re
import shlex
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _pyrun as pyrun  # noqa: E402
import base  # noqa: E402
import state  # noqa: E402
import testcmd  # noqa: E402
import checkpoint as ct  # noqa: E402
import next_tasks as nt  # noqa: E402
import testprobe as tp  # noqa: E402


DEFAULT_SUMMARY = f"{state.DIR_NAME}/test-summary.json"
POLL_SECONDS = 0.5
DEFAULT_WAIT = 600.0
RUN_ID = ""  # the id every summary this process writes carries (set in main)


def pointer_path(summary):
    p = Path(summary)
    return p.with_name(p.stem + ".latest")


def run_summary_path(summary, run_id):
    p = Path(summary)
    return p.with_name(f"{p.stem}-{run_id}{p.suffix}")


def atomic_write(path, text):  # O(len text) + file write
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)


def read_pointer(summary):
    try:
        d = json.loads(pointer_path(summary).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and d.get("id") else None
    except (OSError, ValueError):
        return None


def new_run_id():  # O(1)
    return f"{time.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}-{os.urandom(3).hex()}"


def start_background(a, argv):  # O(len argv) + one detached spawn
    run_id = new_run_id()
    mine = run_summary_path(a.summary, run_id).resolve()
    child = [sys.executable, str(Path(__file__).resolve()),
             *[x for x in argv if x != "--background"],
             "--summary", str(mine), "--publish", str(Path(a.summary).resolve()), "--run-id", run_id]
    atomic_write(pointer_path(a.summary), json.dumps({"id": run_id, "summary": str(mine),
                                                      "started": time.time()}) + "\n")
    base.spawn_background(child, mine.with_suffix(".log"))
    print(f"wait: run_tests --wait --run {run_id}")
    print(f"summary: {mine}")  # last: callers read the final line as the summary path
    return 0


def finished_summary(path):
    """-> the parsed summary at `path`, or None while it is missing or still unreadable."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def wait_background(a):  # O(timeout / POLL_SECONDS) polls
    if a.run:  # exactly that run's own summary, whatever the pointer names
        mine = run_summary_path(a.summary, a.run).resolve()
    else:
        ptr = read_pointer(a.summary)
        if not ptr:
            print("run_tests: no background run to wait for", file=sys.stderr)
            return 2
        mine = Path(ptr["summary"])
    limit = a.timeout if a.timeout is not None else DEFAULT_WAIT
    deadline = time.monotonic() + limit
    while True:
        data = finished_summary(mine)
        if data is not None:
            if a.run and data.get("run_id") != a.run:
                print(f"run_tests: run id mismatch: wanted {a.run}, file has {data.get('run_id')!r}", file=sys.stderr)
                return 2
            break
        line = suspect_line_of(mine)
        if line and not a.patient:  # the probe flagged this run: tell the caller now, run still going
            print(f"{line}\nrun_tests: the run is still going and was not killed; investigate the code, or wait on with --wait --patient")
            return 3
        if time.monotonic() >= deadline:
            print(f"run_tests: TIMEOUT: timed out waiting for {mine} after {limit:.0f}s" + (f"\n{line}" if line else ""), file=sys.stderr)
            return 2
        time.sleep(POLL_SECONDS)
    print(json.dumps(data, ensure_ascii=False, indent=1))
    empty_affected = data.get("mode") == "affected" and data.get("selected") == [] and data.get("reason") == ""
    green = not data.get("failed") and bool(data.get("passed") or data.get("skipped") or empty_affected)
    return 0 if green else 1


def suspect_path(summary):
    return Path(summary).with_suffix(".suspect")


def suspect_line_of(summary):
    try:
        return json.loads(suspect_path(summary).read_text(encoding="utf-8"))["suspect_hang"]["line"]
    except (OSError, ValueError, KeyError, TypeError):
        return ""


def publish_summary(a):  # O(summary size)
    """After a background run: copy its summary to the published path unless a newer run started."""
    if not (a.run_id and a.publish):
        return
    ptr = read_pointer(a.publish)
    if ptr and ptr["id"] != a.run_id:
        return
    atomic_write(a.publish, Path(a.summary).read_text(encoding="utf-8"))


def case_file(case):  # O(d) stats, O(d^2) path work (stats are uncached)
    f = case.get("file")
    if f:
        return f.replace("\\", "/")
    cn = case.get("classname") or ""
    if re.search(r"[\\/]|\.(?:[jt]sx?|go|rs|cs)$", cn):  # a path-like classname (vitest, jest, go, cargo): use it as the file
        return cn.replace("\\", "/")
    parts = cn.split(".")
    while parts and not Path(*parts).with_suffix(".py").exists():
        parts.pop()
    if parts:
        return Path(*parts).with_suffix(".py").as_posix()
    return case.get("name") or "unknown"


def failure_line(case, text, fallback):  # O(len(text) + d)
    fname = re.escape(Path(case_file(case)).name)
    hits = re.findall(rf"{fname}:(\d+)", text or "")
    return int(hits[-1]) if hits else fallback


def parse_junit(xml_path):  # O(X + T*(d + len(failure text)))
    """-> (counts dict, failures [(file, line, context)], files {file: [ok, bad]});
    skipped test ids are in counts["skipped_tests"]"""
    root = ET.parse(xml_path).getroot()
    counts = {"passed": 0, "failed": 0, "skipped": 0, "skipped_tests": []}
    failures, files = [], {}
    for case in root.iter("testcase"):
        f = case_file(case)
        ok_bad = files.setdefault(f, [0, 0])
        bad = case.find("failure")
        if bad is None:
            bad = case.find("error")
        if bad is not None:
            counts["failed"] += 1
            ok_bad[1] += 1
            msg = (bad.get("message") or (bad.text or "").strip()).split("\n")[0]
            line = failure_line(case, bad.text, int(case.get("line") or 0) + 1)
            failures.append((f, line, f"{case.get('name')}: {msg}"))
        elif case.find("skipped") is not None:
            counts["skipped"] += 1
            counts["skipped_tests"].append(f"{f}::{case.get('name')}")
        else:
            counts["passed"] += 1
            ok_bad[0] += 1
    return counts, failures, files


def existing_verified(log):  # O(B)
    seen = set()
    p = Path(log)
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if isinstance(e, dict) and e.get("kind") == "verified":
                seen.add((e.get("file"), e.get("context")))
    return seen


def write_summary(path, mode, counts, failures, seconds, error=None, extra=None):  # O(k + sk) + file write
    """Write the test summary `pt_review packet` reads (atomically)."""
    data = {"passed": counts["passed"], "failed": counts["failed"] + (1 if error else 0),
            "skipped": counts["skipped"], "seconds": round(seconds, 1), "mode": mode,
            "failures": [f"{f}::{ctx}"[:200] for f, _line, ctx in failures]
            + ([error[:200]] if error else []),
            "skipped_tests": counts["skipped_tests"]}
    data.update(extra or {})  # additive probe fields only (verdict, suspect_hang, running)
    if RUN_ID:
        data["run_id"] = RUN_ID
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(p)


def add_incidents(log, items):  # O(total c) + one locked append (no spawn in process)
    """items: (file, line, context, kind, resolution)."""
    nt.add_incidents(log, [(f, line, ctx, kind, "run_tests", res)
                           for f, line, ctx, kind, res in items])


def main(argv=None):  # O(X + T*d + B + F log F) + pytest run + one incident append
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--fast", nargs="+", metavar="PATH",
                      help="run only these test paths (comma or space separated)")
    mode.add_argument("--full", action="store_true", help="run everything (default)")
    mode.add_argument("--affected", action="store_true", help="run only tests related to the changed files")
    ap.add_argument("--base", default=None, metavar="REF", help="with --affected: diff base (default: last start sha, else HEAD)")
    ap.add_argument("--files", nargs="+", metavar="F", help="with --affected: use these files instead of git changes")
    ap.add_argument("--reuse", action="store_true", help="skip the run when --summary is a green full run of this exact tree")
    ap.add_argument("--log", default=ct.DEFAULT_LOG)
    ap.add_argument("--ledger", default=ct.DEFAULT_LEDGER)
    ap.add_argument("--summary", default=DEFAULT_SUMMARY,
                    help="test summary JSON for pt_review packet (default %(default)s)")
    ap.add_argument("--background", action="store_true",
                    help="start a detached run, print `summary: <path>`, exit 0")
    ap.add_argument("--wait", action="store_true", help="wait for the latest background run's summary")
    ap.add_argument("--run", default=None, metavar="ID", help="with --wait: wait for this run id (the `wait:` line), not the newest")
    ap.add_argument("--timeout", type=float, default=None, help=f"--wait limit in seconds (default {DEFAULT_WAIT:.0f})")
    ap.add_argument("--expect", type=float, default=None, metavar="SECONDS",
                    help="expected test time; the hang probe starts at 3x this (default: last green run, else config fallback)")
    ap.add_argument("--patient", action="store_true", help="with --wait: keep waiting after a SUSPECT HANG")
    ap.add_argument("--run-id", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--publish", default=None, help=argparse.SUPPRESS)
    argv = list(sys.argv[1:] if argv is None else argv)
    a = ap.parse_args(argv)
    if a.wait:
        return wait_background(a)
    global RUN_ID
    RUN_ID = a.run_id or new_run_id()
    if a.reuse and (a.full or not (a.fast or a.affected)) and try_reuse(a):
        return 0
    if a.background:
        return start_background(a, argv)
    if a.run_id:
        try:
            return run(a)  # see run
        except BaseException as e:
            if not Path(a.summary).exists():
                write_summary(a.summary, "full", {"passed": 0, "failed": 0, "skipped": 0,
                                                  "skipped_tests": []}, [], 0.0,
                              error=f"run_tests crashed: {e!r}")
                publish_summary(a)
            raise
    try:
        return run(a)  # see run
    finally:  # a foreground run has no --wait reader: its sidecar must not outlive it
        suspect_path(a.summary).unlink(missing_ok=True)


def current_tree():  # the working-tree fingerprint, "" when unknown
    try:
        import tree_id
        return tree_id.tree_hash(".") or ""
    except (ImportError, OSError, ValueError):  # a missing or failing fingerprint only disables reuse
        return ""


def try_reuse(a):  # O(summary size) + one tree hash
    try:
        prior = json.loads(Path(a.summary).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    tree = current_tree()
    if (isinstance(prior, dict) and tree and prior.get("tree") == tree and prior.get("mode") == "full"
            and not prior.get("failed") and (prior.get("passed") or 0) > 0):
        print(f"reused: {a.summary}")
        return True
    return False


def default_base(ledger):  # the sha on the last `- start <sha>` ledger line, else None (HEAD)
    try:
        hits = re.findall(r"^- start\s+([0-9a-fA-F]{4,40})\b", Path(ledger).read_text(encoding="utf-8"), re.M)
    except OSError:
        return None
    return hits[-1] if hits else None


def select_for(a):  # -> (paths, extra summary fields, nothing_selected)
    if a.files:
        changed = sorted({f.replace("\\", "/") for chunk in a.files for f in nt.csv(chunk)})
    else:
        base = a.base or default_base(a.ledger)
        try:
            changed = testcmd.changed_files(".", base)
        except testcmd.BaseUnreadable as e:  # never read a failed diff as "nothing changed": run everything
            print(f"run_tests: {e}; running the whole suite", file=sys.stderr)
            return [], {"selected": [], "reason": f"full: {e}", "changed_count": 0, "untested": []}, False
    sel = testcmd.select_affected(".", changed)
    selected = list(sel.get("selected") or [])
    if sel.get("full"):
        reason, selected = f"full: {sel.get('trigger', '')}", []
    else:
        reason = ",".join(sorted(set((sel.get("reasons") or {}).values())))
    extra = {"selected": selected, "reason": reason, "changed_count": len(changed),
             "untested": list(sel.get("untested") or [])}
    return selected, extra, (not sel.get("full") and not selected)


def expected_seconds(a, label):  # O(summary size) expected test time for the probe, None when unknown
    if a.expect and a.expect > 0:
        return a.expect
    try:
        env = float(os.environ.get("SUPERLITE_TEST_EXPECT_S", ""))
        if env > 0:
            return env
    except ValueError:
        pass
    try:  # the last green run of the same mode
        prior = json.loads(Path(a.publish or a.summary).read_text(encoding="utf-8"))
        if (prior.get("mode") == label and not prior.get("failed") and prior.get("passed")
                and "suspect_hang" not in prior and prior.get("seconds", 0) > 0):
            return float(prior["seconds"])
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return None


def make_reporter(a, label, paths):
    def report(info):  # O(D) + one summary write + one incident append
        line = tp.suspect_line(info)
        print(line, flush=True)  # the caller sees it at once (foreground stdout, or the background log)
        fields = {"verdict": "SUSPECT HANG", "suspect_hang": dict(info, line=line), "running": True}
        try:  # the report must never break the run
            atomic_write(suspect_path(a.summary), json.dumps(fields, ensure_ascii=False) + "\n")  # what --wait reads
            ptr = read_pointer(a.publish) if a.run_id and a.publish else None
            if not (ptr and ptr["id"] != a.run_id):  # a newer run owns the published file otherwise
                write_summary(a.publish if a.run_id and a.publish else a.summary, label,  # provisional: the run is still going
                              {"passed": 0, "failed": 0, "skipped": 0, "skipped_tests": []}, [],
                              info.get("elapsed_s", 0.0), error=line, extra=fields)
            cpu = "n/a" if info["cpu_pct"] is None else format(info["cpu_pct"], ".0f") + "%"
            ctx = f"SUSPECT HANG {info['verdict']}: no output {info['flat_s']:.0f}s, cpu {cpu}, run still going"
            add_incidents(a.log, [(", ".join(paths) or "-", 0, ctx, "perf", "open")])
        except (OSError, ValueError, KeyError, TypeError) as e:
            print(f"run_tests: could not record the hang: {e!r}", file=sys.stderr)
    return report


def serial_addopts():  # O(size of pyproject) -> ["-o", "addopts=..."] when xdist flags would break pytest, else []
    """The project's addopts ask for xdist (`-n auto --dist loadfile`) but xdist is not installed: pytest would
    exit 4 on the unknown flags, so run serially with the other addopts kept."""
    if importlib.util.find_spec("xdist"):
        return []
    try:
        import tomllib
        ini = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["tool"]["pytest"]["ini_options"]
        toks = shlex.split(ini.get("addopts", ""))
    except (OSError, ValueError, KeyError, ImportError):
        return []
    keep, i = [], 0
    while i < len(toks):
        t = toks[i]
        if t in ("-n", "--numprocesses", "--dist", "--maxprocesses"):
            i += 2
        elif t.startswith(("-n", "--numprocesses=", "--dist=", "--maxprocesses=")) and t != "--no-header":
            i += 1
        else:
            keep.append(t)
            i += 1
    return ["-o", "addopts=" + shlex.join(keep)] if len(keep) != len(toks) else []


def watched(a, cmd, label, paths, **kw):  # the test command under the hang probe
    cfg = tp.load_config()
    return tp.run_watched(cmd, cfg=cfg, expected_s=expected_seconds(a, label), test_file=", ".join(paths) or None,
                          on_suspect=make_reporter(a, label, paths), **kw)


def run_other(a, spec, paths, label):  # one runner command (any language)
    """Run the resolved non-Python command: JUnit when the runner wrote one, else exit code plus output parsing."""
    with tempfile.TemporaryDirectory() as tmp:
        xml_path = Path(tmp) / "junit.xml"
        sub = lambda s: s.replace("{junit}", str(xml_path))  # noqa: E731
        env = {**os.environ, **{k: sub(str(v)) for k, v in (spec.get("env") or {}).items()}}
        literal = Path(spec["junit"]) if spec["junit"] and "{junit}" not in spec["junit"] else None  # a path the runner always writes
        if literal and literal.exists():  # a stale report must not count
            literal.unlink()
        marker = Path(state.DIR_NAME) / "setup.done"  # setup runs once per run directory
        if spec["setup"] and not marker.exists():
            pyrun.run_tree(sub(spec["setup"]), shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("ok\n", encoding="utf-8")
        t0 = time.monotonic()
        r = watched(a, sub(spec["command"]), label, paths, shell=True, env=env)
        seconds = time.monotonic() - t0
        text = (r.stdout or "") + (r.stderr or "")
        report = xml_path if xml_path.exists() and xml_path.stat().st_size else (literal if literal and literal.exists() and literal.stat().st_size else None)
        if report is not None:
            try:
                counts, failures, files = parse_junit(report)
            except ET.ParseError:
                report = None
        if report is None:  # no JUnit: exit code plus the last 40 lines, failing names best effort
            p = testcmd.parse_output(text, r.returncode)
            counts = {"passed": p["passed"], "failed": p["failed"], "skipped": p["skipped"], "skipped_tests": []}
            last = next((ln for ln in reversed(p["tail"]) if ln.strip()), "no output")
            failures = [("-", 0, f"{n}: failed") for n in p["names"]]
            hang = tp.hang_text(r) if (r.returncode or r.killed) else ""  # a SUSPECT HANG or TIMEOUT verdict, never a bare exit code
            if hang:
                failures.insert(0, ("-", 0, hang[:300]))
            elif r.returncode and not failures:
                failures = [("-", 0, f"{tp.exit_message(r.returncode)}: {last}"[:160])]
            files = {}
            atomic_write(Path(a.summary).with_name("test-output.log"), "\n".join(p["tail"]) + "\n")  # the last 40 lines
    return finish_run(a, label, counts, failures, files, seconds, r.returncode, extra=tp.summary_fields(r))


def expand_fast_paths(paths):  # O(p) source paths -> related tests; test paths kept
    """--fast accepts source or test paths. An existing source file maps via testcmd.resolve_related.
    A path that is not a file is left as given, so pytest can report a collection error and another
    language's runner receives it in {files}."""
    out = []
    for p in paths:
        rel = p.replace("\\", "/")
        if testcmd.is_test_path(rel) and Path(rel).is_file():
            out.append(rel)
            continue
        if Path(rel).is_file():  # a real source file; missing related tests fail loud
            out.extend(testcmd.resolve_related(".", rel))
            continue
        out.append(rel)  # missing path: the runner reports it
    return out


def run(a):  # O(X + T*d + B + F log F) + the runner + one incident append
    paths = [p for chunk in (a.fast or []) for p in nt.csv(chunk)]
    label = "fast" if paths else "full"
    a.extra = {}
    if a.affected:
        label = "affected"
        paths, a.extra, nothing = select_for(a)
        a.extra.update({"tree": t} if (t := current_tree()) else {})
        if nothing:
            return finish_run(a, label, {"passed": 0, "failed": 0, "skipped": 0, "skipped_tests": []},
                              [], {}, 0.0, 0, extra=a.extra, empty_ok=True)
    elif label == "full":
        a.extra.update({"tree": t} if (t := current_tree()) else {})
    if paths and not a.affected:  # --affected selection is already test files: straight to the runner
        try:
            paths = expand_fast_paths(paths)
        except FileNotFoundError as e:
            reason = str(e)
            print(f"run_tests: {reason}", file=sys.stderr)
            write_summary(a.summary, label, {"passed": 0, "failed": 0, "skipped": 0, "skipped_tests": []}, [], 0.0, error=reason)
            publish_summary(a)
            print("0 0 0 0.0")
            return 1
    spec = testcmd.resolve(".", paths)  # .superlite/test-cmd.json, else marker files
    if spec is None:  # no test command: exit 2 with a clear summary
        reason = (f"no test command found: add {state.DIR_NAME}/test-cmd.json "
                  '{"language","test","test_file","junit","setup"} or a package.json scripts.test, go.mod, Cargo.toml, *.csproj or pytest project')
        print(f"run_tests: {reason}", file=sys.stderr)
        write_summary(a.summary, label, {"passed": 0, "failed": 0, "skipped": 0, "skipped_tests": []}, [], 0.0, error=reason)
        publish_summary(a)
        print("0 0 0 0.0")
        return 2
    if not spec["python"]:
        return run_other(a, spec, paths, label)

    with tempfile.TemporaryDirectory() as tmp:
        xml_path = Path(tmp) / "junit.xml"
        try:
            pyt = pyrun.pytest_cmd()
        except pyrun.PytestUnavailable as e:
            print(f"run_tests: {e}", file=sys.stderr)
            print("0 0 0 0.0")
            return 2
        cmd = [*pyt, *paths, "-q", "-p", "no:cacheprovider",
               "-o", "junit_family=xunit1", f"--junitxml={xml_path}", *serial_addopts()]
        t0 = time.monotonic()
        r = watched(a, cmd, label, paths)
        seconds = time.monotonic() - t0
        if (r.returncode not in (0, 1) or not xml_path.exists()
                or xml_path.stat().st_size == 0):
            tail = (r.stdout + r.stderr).strip().splitlines()[-5:]
            reason = tp.hang_text(r) or f"pytest produced no results ({tp.exit_message(r.returncode)}): " + " | ".join(tail)
            print(reason, file=sys.stderr)
            write_summary(a.summary, label, {"passed": 0, "failed": 0, "skipped": 0,
                                             "skipped_tests": []}, [], seconds,
                          error=reason if (r.returncode != 5 or r.killed) else None, extra=tp.summary_fields(r))
            publish_summary(a)
            print(f"0 0 0 {seconds:.1f}")
            return 1 if (r.returncode == 5 and not r.killed) else 2
        counts, failures, files = parse_junit(xml_path)
    return finish_run(a, label, counts, failures, files, seconds, r.returncode, extra=tp.summary_fields(r))


def finish_run(a, label, counts, failures, files, seconds, returncode, extra=None, empty_ok=False):  # O(k + F + B) one incident append
    extra = {**getattr(a, "extra", {}), **(extra or {})}
    write_summary(a.summary, label, counts, failures, seconds, extra=extra)
    publish_summary(a)
    items = [(f, line, ctx, "test-failure", "open") for f, line, ctx in failures]
    if label == "full":
        seen = existing_verified(a.log)
        for f, (ok, bad) in sorted(files.items()):
            ctx = f"run_tests full: {ok} tests pass"
            if ok and not bad and (f, ctx) not in seen:
                items.append((f, 0, ctx, "verified", None))
    add_incidents(a.log, items)

    summary = (f"{counts['passed']} {counts['failed']} {counts['skipped']} "
               f"{seconds:.1f}")
    ct.append_ledger(a.ledger, f"- tests {label} passed={counts['passed']} "
                     f"failed={counts['failed']} skipped={counts['skipped']} "
                     f"seconds={seconds:.1f}")
    print(summary)
    return 1 if counts["failed"] or not (empty_ok or counts["passed"] or counts["skipped"]) else 0


if __name__ == "__main__":
    sys.exit(main())
