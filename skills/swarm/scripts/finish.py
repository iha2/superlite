#!/usr/bin/env python3
"""Print the closing summary of a run.

    finish.py [--ledger .superlite/progress.md] [--log .superlite/incidents.jsonl]

Collects, from the progress ledger, every `Ruling:` line (in order), the
checkpoints and the last test counts, plus the open incidents from the log.
Deterministic: the same files give the same text. Stdlib only.
"""
import argparse
import json
from datetime import datetime, timedelta
import re
import subprocess
import sys
from pathlib import Path

UNBLOCKED = re.compile(r"^\s*[-*+]?\s*unblocked:\s*dispatched\s+((?:T\d+[A-Za-z]?\s*)+?)\s*at\s+(\d\d:\d\d:\d\d)")

RULING = re.compile(r"^\s*(?:[-*+]\s+)?(?:\*\*)?Ruling:(?:\*\*)?\s*(.*?)\s*$")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from checkpoint import LEDGER_LINE as CHECKPOINT  # noqa: E402 | the one ledger regex
import state  # noqa: E402

DEFAULT_LEDGER = f"{state.DIR_NAME}/progress.md"
DEFAULT_LOG = f"{state.DIR_NAME}/incidents.jsonl"
DEFAULT_LINT_LOG = f"{state.DIR_NAME}/lint-log.jsonl"
TESTS = re.compile(r"^\s*[-*+]?\s*tests\s+(\w+)\s+passed=(\d+)\s+failed=(\d+)"
                   r"\s+skipped=(\d+)\s+seconds=([\d.]+)")


def read_ledger(path):  # O(R*w)
    p = Path(path)
    if not p.exists():
        return [], [], None
    rulings, checkpoints, tests = [], [], None
    for line in p.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n"):
        m = RULING.match(line)
        if m:
            rulings.append(m.group(1))
            continue
        m = CHECKPOINT.match(line)
        if m:
            checkpoints.append(m.groups())
            continue
        m = TESTS.match(line)
        if m:
            tests = m.groups()
    return rulings, checkpoints, tests


def open_incidents(path):  # O(I*w)
    p = Path(path)
    out = []
    if not p.exists():
        return out
    for ln in p.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if (isinstance(e, dict) and "file" in e and e.get("kind") != "verified"
                and e.get("resolution") in (None, "open", "unresolved")):
            out.append(e)
    return out


def unblocked_report(ledger, lint_log):  # O(R*w + L*n)
    """One line: were all stable tasks dispatched before the next patch, and the largest stable-to-dispatch lag."""
    lp = Path(lint_log)
    if not lp.exists():
        return "unblocked: no lint log recorded"
    runs = []
    for ln in lp.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(ln)
            runs.append((datetime.fromisoformat(e["time"]), e))
        except (ValueError, KeyError, TypeError):
            continue
    if not runs:
        return "unblocked: no lint log recorded"
    sent = []  # (task, clock text) per `unblocked:` line
    ledger_p = Path(ledger)
    for line in (ledger_p.read_text(encoding="utf-8").splitlines() if ledger_p.exists() else []):
        m = UNBLOCKED.match(line)
        if m:
            sent += [(t, m.group(2)) for t in m.group(1).split()]
    first_stable = {}  # task -> time it first became stable
    for when, e in runs:
        for t in e.get("newly_stable") or []:
            first_stable.setdefault(t, when)
    sent_at = {}
    for t, clock in sent:
        if t in sent_at or t not in first_stable:
            continue
        base = first_stable[t]
        at = datetime.combine(base.date(), datetime.strptime(clock, "%H:%M:%S").time())
        sent_at[t] = at + timedelta(days=1) if at < base - timedelta(seconds=1) else at
    held = sorted({t for (_, a), (_, b) in zip(runs, runs[1:])  # stable at one lint, still pending at the next
                   for t in set(a.get("pending") or []) & set(b.get("pending") or [])})
    lags = [(sent_at[t] - first_stable[t]).total_seconds() for t in sent_at]
    missing = sorted(t for t in first_stable if t not in sent_at and t in set(runs[-1][1].get("pending") or []))
    worst = f"max lag {max(0, max(lags)):.0f}s" if lags else "max lag n/a (no `unblocked:` lines)"
    if held:
        return f"unblocked: NO - {' '.join(held)} still waiting at the next lint; {worst}"
    if missing:
        return f"unblocked: NO - never dispatched: {' '.join(missing)}; {worst}"
    return f"unblocked: yes - every stable task was dispatched before the next patch; {worst}"


def history_section(plan, sl_dir):  # O(M + B + n*(n + e))
    """Append the run's rows to history (global and project), refresh stats, and report predicted against actual."""
    sl = Path(sl_dir)
    if not plan or not (sl / "metrics.jsonl").is_file():
        return "history: no metrics.jsonl or plan, nothing recorded"
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import history
    import planfile
    import predict
    expected = (OSError, ValueError, KeyError, TypeError, ArithmeticError, planfile.PlanError, predict.PredictError)
    try:
        rows = history.record_run(plan, sl_dir)
    except expected as e:
        return f"history: not recorded ({e})"
    out = [f"history: {len(rows)} rows appended"]  # predicted vs actual is the build report's job
    try:
        import report
        out += ["", report.report_text(plan, sl_dir).rstrip("\n")]
    except expected as e:
        out.append(f"report: not produced ({e})")
    return chr(10).join(out)


def checkpoint_note(ledger):  # O(files); [] unless this run took hidden checkpoints
    """Lines saying the work is uncommitted in the folder, when the run took checkpoints."""
    sl = Path(ledger).resolve().parent
    try:
        st = json.loads((sl / "checkpoints.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not st.get("entries"):
        return []
    files = sorted({f for e in st["entries"] for f in e["files"]})
    return ["", "The work is uncommitted in the folder; your branch is untouched.",
            f"changed files: {len(files)}; checkpoints: {len(st['entries'])}"]


def clean_checkpoints(ledger):  # O(refs) + git spawns; the end-of-run cleanup
    """Delete refs/superlite/* and the checkpoint state. Logs what it could not do."""
    from checkpoint import REF_ROOT
    state = Path(ledger).resolve().parent / "checkpoints.json"
    if not state.exists():
        return
    try:
        r = subprocess.run(["git", "for-each-ref", "--format=%(refname)", REF_ROOT],
                           capture_output=True, text=True, encoding="utf-8")
        for ref in r.stdout.split():
            subprocess.run(["git", "update-ref", "-d", ref], capture_output=True)
        state.unlink(missing_ok=True)
    except OSError as e:
        print(f"checkpoints not cleaned: {e}", file=sys.stderr)


SCOPE_TAG = re.compile(r"^\[scope\]\s*", re.I)


def scope_rulings(rulings):  # O(G)
    """Rulings tagged `[scope]`: a change to scope or to an interface the human approved, made without asking."""
    return [SCOPE_TAG.sub("", r) for r in rulings if SCOPE_TAG.match(r)]


def summary(ledger, log, lint_log=DEFAULT_LINT_LOG, plan=None, sl_dir=None):  # O(R*w + I*w + C + G)
    rulings, checkpoints, tests = read_ledger(ledger)
    incidents = open_incidents(log)
    out = ["# superlite run summary", ""]
    cp = checkpoint_note(ledger)
    out.append(f"checkpoints: {len(checkpoints)}")
    out += [f"- {t} {sha[:10]} {title}".rstrip() for t, sha, title in checkpoints]
    out += cp
    out.append("")
    out.append(f"rulings: {len(rulings)}")
    out += [f"- {r}" for r in rulings]
    out.append("")
    changed = scope_rulings(rulings)
    if changed:  # the human is not asked mid-run, so every scope or interface change is surfaced here
        out.append(f"Changed from the approved plan: {len(changed)}")
        out += [f"- {r}" for r in changed]
        out.append("")
    if tests:
        mode, p, f, s, sec = tests
        out.append(f"tests ({mode}): {p} passed, {f} failed, {s} skipped in {sec}s")
    else:
        out.append("tests: none recorded")
    out.append("")
    out.append(unblocked_report(ledger, lint_log))  # see unblocked_report
    out.append("")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import orchestrator
    out.append(orchestrator.summary_line(Path(ledger).resolve().parent))  # mode, and the approval time in human mode
    out.append("")
    cr = Path(ledger).with_name("chain-rulings.json")  # sequential rulings lint accepted, with the minutes they cost
    try:
        accepted = json.loads(cr.read_text(encoding="utf-8")) if cr.is_file() else []
    except ValueError:
        accepted = []
    out.append(f"sequential rulings accepted: {len(accepted)}")
    out += [f"- {a}" for a in accepted]
    out.append("")
    if plan is not None:
        out.append(history_section(plan or None, sl_dir or str(Path(ledger).parent)))  # see history_section
        out.append("")
    out.append(f"open incidents: {len(incidents)}")
    for e in incidents:
        kind = e.get("kind") or "unknown"
        out.append(f"- {e['file']}:{e['line']} [{kind}] {e.get('context', '')}")
    return "\n".join(out) + "\n"


def main(argv=None):  # O(R*w + I*w)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ledger", default=DEFAULT_LEDGER)
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--plan", help="record this run into history (needs " + state.DIR_NAME + "/metrics.jsonl)")
    ap.add_argument("--lint-log", help="default: lint-log.jsonl next to the ledger")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdout.write(summary(a.ledger, a.log, a.lint_log or str(Path(a.ledger).with_name("lint-log.jsonl")), a.plan))
    import exclusive  # the run is done: peer plugins may load again
    root = Path(a.ledger).resolve().parent  # .superlite, or the ledger's directory
    clean_checkpoints(a.ledger)  # after the summary, which reads the checkpoint state
    sl_dir = root if root.name == state.DIR_NAME else root / state.DIR_NAME
    try:  # fail open: no registry, no git, unreadable sessions.json -> no line. Mark first: release looks at open runs.
        import session
        slug = session.mark_run(sl_dir.parent, "finished")
        if slug:
            print(f"session: {slug} finished")
    except Exception:
        pass
    print(exclusive.release(sl_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
