#!/usr/bin/env python3
"""Run the project's test command (testcmd: .superlite/test-cmd.json, else detection; pytest for Python),
parse its junit XML or output, print one summary line.

    run_tests.py [--fast PATHS... | --full] [--log PATH] [--ledger PATH]
                 [--summary PATH] [--background]
                 [--expect SECONDS]
    run_tests.py --wait [--timeout S] [--patient] [--summary PATH]

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

--background starts a detached run of the same command, prints `summary: <path>` and exits 0
at once. Each background run gets its own summary file (`test-summary-<id>.json` next to
--summary) and the pointer `test-summary.latest` (atomic) names the newest run; when a run
finishes it also publishes its summary to --summary, unless a newer run has started since.
--wait blocks until the pointed run's own summary exists (an older summary never counts),
prints it, and exits 0 green, 1 red, 2 on timeout (`TIMEOUT`) or when no background run was started, and 3 as
soon as the probe reports a SUSPECT HANG (prints that line; the run keeps going; --patient waits on regardless).
Stdlib only.
"""
# Complexity notation:
#   T = testcases in the junit XML, X = bytes of the XML, F = distinct test files, k = failing tests,
#   sk = skipped tests, I = lines in the incident log (B = its bytes), d = depth of a dotted classname,
#   p = pytest path args, O = bytes of pytest output, c = length of an incident context, w = width of one line
import argparse  # O(1)
import json  # O(1)
import os  # O(1)
import re  # O(1)
import subprocess  # O(1)
import sys  # O(1)
import tempfile  # O(1)
import time  # O(1)
import xml.etree.ElementTree as ET  # O(1)
from pathlib import Path  # O(1)

sys.path.insert(0, str(Path(__file__).resolve().parent))  # O(path list)
import _pyrun as pyrun  # noqa: E402  # O(1) module load
import testcmd  # noqa: E402  # O(1) module load
import checkpoint as ct  # noqa: E402  # O(1) module load
import next_tasks as nt  # noqa: E402  # O(1) module load
import testprobe as tp  # noqa: E402  # O(1) module load


DEFAULT_SUMMARY = ".superlite/test-summary.json"  # O(1)
POLL_SECONDS = 0.5  # O(1)
DEFAULT_WAIT = 600.0  # O(1)


def pointer_path(summary):  # O(1)
    p = Path(summary)  # O(1)
    return p.with_name(p.stem + ".latest")  # O(1)


def run_summary_path(summary, run_id):  # O(1)
    p = Path(summary)  # O(1)
    return p.with_name(f"{p.stem}-{run_id}{p.suffix}")  # O(1)


def atomic_write(path, text):  # O(len text) + file write
    p = Path(path)  # O(1)
    p.parent.mkdir(parents=True, exist_ok=True)  # O(1)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")  # O(1) unique per process
    tmp.write_text(text, encoding="utf-8")  # O(len text)
    tmp.replace(p)  # O(1) atomic rename


def read_pointer(summary):  # O(1)
    try:  # O(1)
        d = json.loads(pointer_path(summary).read_text(encoding="utf-8"))  # O(1)
        return d if isinstance(d, dict) and d.get("id") else None  # O(1)
    except (OSError, ValueError):  # O(1)
        return None  # O(1)


def start_background(a, argv):  # O(len argv) + one detached spawn
    run_id = f"{time.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}-{os.urandom(3).hex()}"  # O(1) unique id
    mine = run_summary_path(a.summary, run_id).resolve()  # O(1) absolute: --wait may run from another cwd
    child = [sys.executable, str(Path(__file__).resolve()),  # O(len argv)
             *[x for x in argv if x != "--background"],  # O(len argv)
             "--summary", str(mine), "--publish", str(Path(a.summary).resolve()), "--run-id", run_id]  # O(1) last flag wins
    atomic_write(pointer_path(a.summary), json.dumps({"id": run_id, "summary": str(mine),  # O(1)
                                                      "started": time.time()}) + "\n")  # O(1)
    pyrun.spawn_detached(child, mine.with_suffix(".log"))  # O(1) + process spawn
    print(f"summary: {mine}")  # O(1)
    return 0  # O(1)


def wait_background(a):  # O(timeout / POLL_SECONDS) polls
    ptr = read_pointer(a.summary)  # O(1)
    if not ptr:  # O(1)
        print("run_tests: no background run to wait for", file=sys.stderr)  # O(1)
        return 2  # O(1)
    mine = Path(ptr["summary"])  # O(1)
    limit = a.timeout if a.timeout is not None else DEFAULT_WAIT  # O(1)
    deadline = time.monotonic() + limit  # O(1)
    while True:  # O(timeout / POLL_SECONDS) iterations
        if mine.exists():  # O(1)
            try:  # O(1)
                data = json.loads(mine.read_text(encoding="utf-8"))  # O(summary size)
                break  # O(1)
            except (OSError, ValueError):  # O(1) half-written (non-atomic fs): retry
                pass  # O(1)
        line = suspect_line_of(mine)  # O(1) stat
        if line and not a.patient:  # the probe flagged this run: tell the caller now, run still going | O(1)
            print(f"{line}\nrun_tests: the run is still going and was not killed; investigate the code, or wait on with --wait --patient")  # O(1)
            return 3  # O(1)
        if time.monotonic() >= deadline:  # O(1)
            print(f"run_tests: TIMEOUT: timed out waiting for {mine} after {limit:.0f}s" + (f"\n{line}" if line else ""), file=sys.stderr)  # O(1)
            return 2  # O(1)
        time.sleep(POLL_SECONDS)  # O(1)
    print(json.dumps(data, ensure_ascii=False, indent=1))  # O(summary size)
    green = not data.get("failed") and bool(data.get("passed") or data.get("skipped"))  # O(1)
    return 0 if green else 1  # O(1)


def suspect_path(summary):  # O(1) the probe's sidecar for a run (the summary file does not exist until the run ends)
    return Path(summary).with_suffix(".suspect")  # O(1)


def suspect_line_of(summary):  # O(1) stat + small read
    try:  # O(1)
        return json.loads(suspect_path(summary).read_text(encoding="utf-8"))["suspect_hang"]["line"]  # O(1)
    except (OSError, ValueError, KeyError, TypeError):  # O(1)
        return ""  # O(1)


def publish_summary(a):  # O(summary size)
    """After a background run: copy its summary to the published path unless a newer run started."""
    if not (a.run_id and a.publish):  # O(1)
        return  # O(1)
    ptr = read_pointer(a.publish)  # O(1)
    if ptr and ptr["id"] != a.run_id:  # O(1) a newer run owns the published file
        return  # O(1)
    atomic_write(a.publish, Path(a.summary).read_text(encoding="utf-8"))  # O(summary size)


def case_file(case):  # O(d) stats, O(d^2) path work (stats are uncached)
    f = case.get("file")  # O(1)
    if f:  # O(1)
        return f.replace("\\", "/")  # O(len(f))
    cn = case.get("classname") or ""  # O(1)
    if re.search(r"[\\/]|\.(?:[jt]sx?|go|rs|cs)$", cn):  # a path-like classname (vitest, jest, go, cargo): use it as the file | O(1)
        return cn.replace("\\", "/")  # O(1)
    parts = cn.split(".")  # O(d)
    while parts and not Path(*parts).with_suffix(".py").exists():  # O(d) iterations; Path join + stat O(d) each
        parts.pop()  # O(1)
    if parts:  # O(d)
        return Path(*parts).with_suffix(".py").as_posix()  # O(d)
    return case.get("name") or "unknown"  # O(1)


def failure_line(case, text, fallback):  # O(len(text) + d)
    base = re.escape(Path(case_file(case)).name)  # O(d) stats + O(len)
    hits = re.findall(rf"{base}:(\d+)", text or "")  # O(len(text))
    return int(hits[-1]) if hits else fallback  # O(1)


def parse_junit(xml_path):  # O(X + T*(d + len(failure text)))
    """-> (counts dict, failures [(file, line, context)], files {file: [ok, bad]});
    skipped test ids are in counts["skipped_tests"]"""
    root = ET.parse(xml_path).getroot()  # O(X) XML parse
    counts = {"passed": 0, "failed": 0, "skipped": 0, "skipped_tests": []}  # O(1)
    failures, files = [], {}  # O(1)
    for case in root.iter("testcase"):  # O(T) iterations
        f = case_file(case)  # O(d) stats per testcase (uncached; repeated in failure_line)
        ok_bad = files.setdefault(f, [0, 0])  # O(1)
        bad = case.find("failure")  # O(1)
        if bad is None:  # O(1)
            bad = case.find("error")  # O(1)
        if bad is not None:  # O(1)
            counts["failed"] += 1  # O(1)
            ok_bad[1] += 1  # O(1)
            msg = (bad.get("message") or (bad.text or "").strip()).split("\n")[0]  # O(len(failure text))
            line = failure_line(case, bad.text, int(case.get("line") or 0) + 1)  # O(len(failure text) + d)
            failures.append((f, line, f"{case.get('name')}: {msg}"))  # O(1)
        elif case.find("skipped") is not None:  # O(1)
            counts["skipped"] += 1  # O(1)
            counts["skipped_tests"].append(f"{f}::{case.get('name')}")  # O(1)
        else:  # O(1)
            counts["passed"] += 1  # O(1)
            ok_bad[0] += 1  # O(1)
    return counts, failures, files  # O(1)


def existing_verified(log):  # O(B)
    seen = set()  # O(1)
    p = Path(log)  # O(1)
    if p.exists():  # O(1)
        for ln in p.read_text(encoding="utf-8").splitlines():  # O(I) iterations; read O(B)
            try:  # O(1)
                e = json.loads(ln)  # O(w)
            except json.JSONDecodeError:  # O(1)
                continue  # O(1)
            if isinstance(e, dict) and e.get("kind") == "verified":  # O(1)
                seen.add((e.get("file"), e.get("context")))  # O(1)
    return seen  # O(1)


def write_summary(path, mode, counts, failures, seconds, error=None, extra=None):  # O(k + sk) + file write
    """Write the test summary `pt_review packet` reads (atomically)."""  # O(1)
    data = {"passed": counts["passed"], "failed": counts["failed"] + (1 if error else 0),  # O(1)
            "skipped": counts["skipped"], "seconds": round(seconds, 1), "mode": mode,  # O(1)
            "failures": [f"{f}::{ctx}"[:200] for f, _line, ctx in failures]  # O(k)
            + ([error[:200]] if error else []),  # O(k) list concat
            "skipped_tests": counts["skipped_tests"]}  # O(1)
    data.update(extra or {})  # additive probe fields only (verdict, suspect_hang, running) | O(1)
    p = Path(path)  # O(1)
    p.parent.mkdir(parents=True, exist_ok=True)  # O(1)
    tmp = p.with_name(p.name + ".tmp")  # O(1)
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")  # O(k + sk) dumps + write
    tmp.replace(p)  # O(1) rename


def add_incidents(log, items):  # O(total c) + one locked append (no spawn in process)
    """items: (file, line, context, kind, resolution)."""
    nt.add_incidents(log, [(f, line, ctx, kind, "run_tests", res)  # O(k)
                           for f, line, ctx, kind, res in items])


def main(argv=None):  # O(X + T*d + B + F log F) + pytest run + one incident append
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])  # O(1)
    mode = ap.add_mutually_exclusive_group()  # O(1)
    mode.add_argument("--fast", nargs="+", metavar="PATH",  # O(1)
                      help="run only these test paths (comma or space separated)")  # O(1)
    mode.add_argument("--full", action="store_true", help="run everything (default)")  # O(1)
    ap.add_argument("--log", default=ct.DEFAULT_LOG)  # O(1)
    ap.add_argument("--ledger", default=ct.DEFAULT_LEDGER)  # O(1)
    ap.add_argument("--summary", default=DEFAULT_SUMMARY,  # O(1)
                    help="test summary JSON for pt_review packet (default %(default)s)")  # O(1)
    ap.add_argument("--background", action="store_true",  # O(1)
                    help="start a detached run, print `summary: <path>`, exit 0")  # O(1)
    ap.add_argument("--wait", action="store_true", help="wait for the latest background run's summary")  # O(1)
    ap.add_argument("--timeout", type=float, default=None, help=f"--wait limit in seconds (default {DEFAULT_WAIT:.0f})")  # O(1)
    ap.add_argument("--expect", type=float, default=None, metavar="SECONDS",  # O(1)
                    help="expected test time; the hang probe starts at 3x this (default: last green run, else config fallback)")  # O(1)
    ap.add_argument("--patient", action="store_true", help="with --wait: keep waiting after a SUSPECT HANG")  # O(1)
    ap.add_argument("--run-id", default=None, help=argparse.SUPPRESS)  # O(1) set by --background
    ap.add_argument("--publish", default=None, help=argparse.SUPPRESS)  # O(1) set by --background
    argv = list(sys.argv[1:] if argv is None else argv)  # O(len argv)
    a = ap.parse_args(argv)  # O(1)
    if a.wait:  # O(1)
        return wait_background(a)  # O(timeout / POLL_SECONDS)
    if a.background:  # O(1)
        return start_background(a, argv)  # O(len argv) + spawn
    if a.run_id:  # O(1) detached child: a crash still leaves a (red) summary for --wait
        try:  # O(1)
            return run(a)  # see run
        except BaseException as e:  # O(1)
            if not Path(a.summary).exists():  # O(1)
                write_summary(a.summary, "full", {"passed": 0, "failed": 0, "skipped": 0,  # O(1)
                                                  "skipped_tests": []}, [], 0.0,  # O(1)
                              error=f"run_tests crashed: {e!r}")  # O(1)
                publish_summary(a)  # O(summary size)
            raise  # O(1)
    try:  # O(1)
        return run(a)  # see run
    finally:  # a foreground run has no --wait reader: its sidecar must not outlive it | O(1)
        suspect_path(a.summary).unlink(missing_ok=True)  # O(1)


def expected_seconds(a, label):  # O(summary size) expected test time for the probe, None when unknown
    if a.expect and a.expect > 0:  # O(1)
        return a.expect  # O(1)
    try:  # O(1)
        env = float(os.environ.get("SUPERLITE_TEST_EXPECT_S", ""))  # O(1)
        if env > 0:  # O(1)
            return env  # O(1)
    except ValueError:  # O(1)
        pass  # O(1)
    try:  # the last green run of the same mode | O(summary size)
        prior = json.loads(Path(a.publish or a.summary).read_text(encoding="utf-8"))  # O(summary size)
        if (prior.get("mode") == label and not prior.get("failed") and prior.get("passed")  # O(1)
                and "suspect_hang" not in prior and prior.get("seconds", 0) > 0):  # O(1)
            return float(prior["seconds"])  # O(1)
    except (OSError, ValueError, AttributeError, TypeError):  # O(1)
        pass  # O(1)
    return None  # O(1)


def make_reporter(a, label, paths):  # O(1) -> callback for the probe's first SUSPECT HANG
    def report(info):  # O(D) + one summary write + one incident append
        line = tp.suspect_line(info)  # O(D)
        print(line, flush=True)  # the caller sees it at once (foreground stdout, or the background log) | O(1)
        fields = {"verdict": "SUSPECT HANG", "suspect_hang": dict(info, line=line), "running": True}  # O(D)
        try:  # the report must never break the run | O(summary size)
            atomic_write(suspect_path(a.summary), json.dumps(fields, ensure_ascii=False) + "\n")  # what --wait reads | O(1)
            ptr = read_pointer(a.publish) if a.run_id and a.publish else None  # O(1)
            if not (ptr and ptr["id"] != a.run_id):  # a newer run owns the published file otherwise | O(1)
                write_summary(a.publish if a.run_id and a.publish else a.summary, label,  # provisional: the run is still going | O(1)
                              {"passed": 0, "failed": 0, "skipped": 0, "skipped_tests": []}, [],
                              info.get("elapsed_s", 0.0), error=line, extra=fields)
            cpu = "n/a" if info["cpu_pct"] is None else format(info["cpu_pct"], ".0f") + "%"  # O(1)
            ctx = f"SUSPECT HANG {info['verdict']}: no output {info['flat_s']:.0f}s, cpu {cpu}, run still going"  # O(1)
            add_incidents(a.log, [(", ".join(paths) or "-", 0, ctx, "perf", "open")])  # O(1)
        except Exception as e:  # noqa: BLE001 | O(1)
            print(f"run_tests: could not record the hang: {e!r}", file=sys.stderr)  # O(1)
    return report  # O(1)


def watched(a, cmd, label, paths, **kw):  # the test command under the hang probe | O(1) + the run
    cfg = tp.load_config()  # O(file)
    return tp.run_watched(cmd, cfg=cfg, expected_s=expected_seconds(a, label), test_file=", ".join(paths) or None,  # O(1) + the run
                          on_suspect=make_reporter(a, label, paths), **kw)  # O(1)


def run_other(a, spec, paths, label):  # one runner command (any language) | O(X + o) + the run
    """Run the resolved non-Python command: JUnit when the runner wrote one, else exit code plus output parsing."""
    with tempfile.TemporaryDirectory() as tmp:  # O(1)
        xml_path = Path(tmp) / "junit.xml"  # O(1)
        sub = lambda s: s.replace("{junit}", str(xml_path))  # noqa: E731 | O(len)
        env = {**os.environ, **{k: sub(str(v)) for k, v in (spec.get("env") or {}).items()}}  # O(env)
        literal = Path(spec["junit"]) if spec["junit"] and "{junit}" not in spec["junit"] else None  # a path the runner always writes | O(1)
        if literal and literal.exists():  # a stale report must not count | O(1)
            literal.unlink()  # O(1)
        marker = Path(".superlite") / "setup.done"  # setup runs once per run directory | O(1)
        if spec["setup"] and not marker.exists():  # O(1)
            pyrun.run_tree(sub(spec["setup"]), shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)  # O(o)
            marker.parent.mkdir(parents=True, exist_ok=True)  # O(1)
            marker.write_text("ok\n", encoding="utf-8")  # O(1)
        t0 = time.monotonic()  # O(1)
        r = watched(a, sub(spec["command"]), label, paths, shell=True, env=env)  # O(o) + the run, probed for a hang
        seconds = time.monotonic() - t0  # O(1)
        text = (r.stdout or "") + (r.stderr or "")  # O(o)
        report = xml_path if xml_path.exists() and xml_path.stat().st_size else (literal if literal and literal.exists() and literal.stat().st_size else None)  # O(1)
        if report is not None:  # O(1)
            try:  # O(1)
                counts, failures, files = parse_junit(report)  # O(X + T*d)
            except ET.ParseError:  # O(1)
                report = None  # O(1)
        if report is None:  # no JUnit: exit code plus the last 40 lines, failing names best effort | O(o)
            p = testcmd.parse_output(text, r.returncode)  # O(o)
            counts = {"passed": p["passed"], "failed": p["failed"], "skipped": p["skipped"], "skipped_tests": []}  # O(1)
            last = next((ln for ln in reversed(p["tail"]) if ln.strip()), "no output")  # O(1)
            failures = [("-", 0, f"{n}: failed") for n in p["names"]]  # O(k)
            hang = tp.hang_text(r) if (r.returncode or r.killed) else ""  # a SUSPECT HANG or TIMEOUT verdict, never a bare exit code | O(D)
            if hang:  # O(1)
                failures.insert(0, ("-", 0, hang[:300]))  # O(k)
            elif r.returncode and not failures:  # O(1)
                failures = [("-", 0, f"{tp.exit_message(r.returncode)}: {last}"[:160])]  # O(1)
            files = {}  # O(1)
            atomic_write(Path(a.summary).with_name("test-output.log"), "\n".join(p["tail"]) + "\n")  # the last 40 lines | O(40)
    return finish_run(a, label, counts, failures, files, seconds, r.returncode, extra=tp.summary_fields(r))  # O(k + F)


def expand_fast_paths(paths):  # O(p) source paths -> related tests; test paths kept
    """--fast accepts source or test paths; source maps via testcmd.resolve_related."""
    out = []  # O(1)
    for p in paths:  # O(p)
        rel = p.replace("\\", "/")  # O(len)
        if testcmd.is_test_path(rel) and Path(rel).is_file():  # O(1)
            out.append(rel)  # O(1)
            continue  # O(1)
        out.extend(testcmd.resolve_related(".", rel))  # O(1) raises FileNotFoundError
    return out  # O(p)


def run(a):  # O(X + T*d + B + F log F) + the runner + one incident append
    paths = [p for chunk in (a.fast or []) for p in nt.csv(chunk)]  # O(p)
    label = "fast" if paths else "full"  # O(1)
    if paths:  # O(1)
        try:  # O(1)
            paths = expand_fast_paths(paths)  # O(p)
        except FileNotFoundError as e:  # O(1)
            reason = str(e)  # O(1)
            print(f"run_tests: {reason}", file=sys.stderr)  # O(1)
            write_summary(a.summary, label, {"passed": 0, "failed": 0, "skipped": 0, "skipped_tests": []}, [], 0.0, error=reason)  # O(1)
            publish_summary(a)  # O(summary size)
            print("0 0 0 0.0")  # O(1)
            return 1  # O(1)
    spec = testcmd.resolve(".", paths)  # .superlite/test-cmd.json, else marker files | O(m + f)
    if spec is None:  # no test command: exit 2 with a clear summary | O(1)
        reason = ("no test command found: add .superlite/test-cmd.json "  # O(1)
                  '{"language","test","test_file","junit","setup"} or a package.json scripts.test, go.mod, Cargo.toml, *.csproj or pytest project')
        print(f"run_tests: {reason}", file=sys.stderr)  # O(1)
        write_summary(a.summary, label, {"passed": 0, "failed": 0, "skipped": 0, "skipped_tests": []}, [], 0.0, error=reason)  # O(1)
        publish_summary(a)  # O(summary size)
        print("0 0 0 0.0")  # O(1)
        return 2  # O(1)
    if not spec["python"]:  # O(1)
        return run_other(a, spec, paths, label)  # O(X + o)

    with tempfile.TemporaryDirectory() as tmp:  # O(1) mkdtemp
        xml_path = Path(tmp) / "junit.xml"  # O(1)
        try:  # O(1)
            base = pyrun.pytest_cmd()  # O(1) + at most one probe spawn
        except pyrun.PytestUnavailable as e:  # O(1)
            print(f"run_tests: {e}", file=sys.stderr)  # O(1)
            print("0 0 0 0.0")  # O(1)
            return 2  # O(1)
        cmd = [*base, *paths, "-q", "-p", "no:cacheprovider",  # O(p)
               "-o", "junit_family=xunit1", f"--junitxml={xml_path}"]  # O(1)
        t0 = time.monotonic()  # O(1)
        r = watched(a, cmd, label, paths)  # O(O) + process spawn (pytest run time dominates), probed for a hang
        seconds = time.monotonic() - t0  # O(1)
        if (r.returncode not in (0, 1) or not xml_path.exists()  # O(1)
                or xml_path.stat().st_size == 0):  # O(1)
            tail = (r.stdout + r.stderr).strip().splitlines()[-5:]  # O(O)
            reason = tp.hang_text(r) or f"pytest produced no results ({tp.exit_message(r.returncode)}): " + " | ".join(tail)  # O(1)
            print(reason, file=sys.stderr)  # O(1)
            write_summary(a.summary, label, {"passed": 0, "failed": 0, "skipped": 0,  # O(1)
                                             "skipped_tests": []}, [], seconds,  # O(1)
                          error=reason if (r.returncode != 5 or r.killed) else None, extra=tp.summary_fields(r))  # O(1)
            publish_summary(a)  # O(summary size)
            print(f"0 0 0 {seconds:.1f}")  # O(1)
            return 1 if (r.returncode == 5 and not r.killed) else 2  # O(1)
        counts, failures, files = parse_junit(xml_path)  # O(X + T*d)
    return finish_run(a, label, counts, failures, files, seconds, r.returncode, extra=tp.summary_fields(r))  # O(k + F)


def finish_run(a, label, counts, failures, files, seconds, returncode, extra=None):  # O(k + F + B) one incident append
    write_summary(a.summary, label, counts, failures, seconds, extra=extra)  # O(k + sk)
    publish_summary(a)  # O(summary size)
    items = [(f, line, ctx, "test-failure", "open") for f, line, ctx in failures]  # O(k)
    if label == "full":  # O(1)
        seen = existing_verified(a.log)  # O(B)
        for f, (ok, bad) in sorted(files.items()):  # O(F log F) sort; O(F) iterations
            ctx = f"run_tests full: {ok} tests pass"  # O(1)
            if ok and not bad and (f, ctx) not in seen:  # O(1) set lookup
                items.append((f, 0, ctx, "verified", None))  # O(1)
    add_incidents(a.log, items)  # O(k + F) one append

    summary = (f"{counts['passed']} {counts['failed']} {counts['skipped']} "  # O(1)
               f"{seconds:.1f}")  # O(1)
    ct.append_ledger(a.ledger, f"- tests {label} passed={counts['passed']} "  # O(1)
                     f"failed={counts['failed']} skipped={counts['skipped']} "  # O(1)
                     f"seconds={seconds:.1f}")  # O(1)
    print(summary)  # O(1)
    return 1 if counts["failed"] or not (counts["passed"] or counts["skipped"]) else 0  # O(1)


if __name__ == "__main__":  # O(1)
    sys.exit(main())  # O(1)
