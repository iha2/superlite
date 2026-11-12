#!/usr/bin/env python3
"""Learning sync for the per-repo learning loop: a run folder's signals become lessons, history rows and outbox rows.

    learn.py sync [--plan P] [--final] [--status finished|unfinished]   fold new signals into the lessons cache
    learn.py note --task Tn --tag setup|test|pairing|convention --text "..."   record a `Learned:` line

Reads incidents, review findings, fix notes, learned lines, launches and (with --plan) task outcomes of this
checkout's run folder; writes `lessons.json` and `learn-state.json` in the hub run dir, history rows (newest outcome
wins) and anonymised rows to `<history home>/learn/outbox.jsonl`. Errors warn and exit 0: every hook point is advisory.
Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date as _date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402
import history  # noqa: E402
import learn_signals  # noqa: E402
import lessons_score  # noqa: E402
import lessons_store  # noqa: E402
import run_archive  # noqa: E402
import state  # noqa: E402

HOOK_SIGNAL = {"rule-block": "hook-block", "rule-rewrite": "hook-rewrite"}
SIGNAL_FILES = ("incidents.jsonl", "review-findings.jsonl", "learned.jsonl", "metrics.jsonl")


def _version() -> str:  # O(1)
    manifest = Path(__file__).resolve().parents[3] / ".claude-plugin" / "plugin.json"
    data = base.read_json(manifest, {})
    value = data.get("version") if isinstance(data, dict) else None
    return str(value) if value else "unknown"


def _tail(path: Path, memo: dict, key: str) -> tuple:  # O(n) in new bytes
    """New rows of path past the stored offset: (rows, start offset); the memo copy is advanced."""
    start = int(memo["offsets"].get(key, 0))
    rows, end = learn_signals.read_new_lines(path, start)
    memo["offsets"][key] = end
    return rows, start


def _all_rows(path: Path) -> list:  # O(n)
    return learn_signals.read_new_lines(path, 0)[0]


def _mods(files: list) -> list:  # O(f)
    return sorted({lessons_score.module_key(f) for f in files})


def _append(path: Path, rows: list) -> None:  # O(rows)
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def _add(store: dict, kind: str, module: str, key: str, text: str, source: str, run: str,
         verified: bool = False) -> str:  # O(1)
    lid = lessons_store.add_signal(store, kind, module, key, text, source, run, verified)
    store["lessons"][lid]["key"] = key
    return lid


def _is_test(path: str) -> bool:  # O(len(path))
    name = Path(path).name
    return name.startswith("test_") or name.endswith(("_test.py", ".test.ts", ".test.js", ".spec.ts")) \
        or "tests/" in path.replace("\\", "/")


def _checkpoint_shas(sl: Path) -> dict:  # O(ledger)
    """Task id -> newest checkpoint sha from the ledger's `- checkpoint Tn <sha>` lines."""
    import checkpoint  # noqa: PLC0415  (heavy import, only when a plan is given)
    ledger = sl / "progress.md"
    out: dict = {}
    if ledger.is_file():
        for ln in ledger.read_text(encoding="utf-8", errors="replace").splitlines():
            m = checkpoint.LEDGER_LINE.match(ln)
            if m:
                out[m.group(1)] = m.group(2)
    return out


def _changed(sl: Path, sha: str) -> list:  # O(files)
    """Files of one checkpoint commit (non-test files first); [] on any git error."""
    try:
        r = base.git("diff-tree", "--no-commit-id", "--name-only", "-r", sha, cwd=sl.parent, check=False)
    except (base.GitError, OSError):
        return []
    if r.returncode != 0:
        return []
    files = [f for f in r.stdout.splitlines() if f and not f.startswith(state.DIR_NAME + "/")]
    return sorted(files, key=lambda f: (_is_test(f), f))


def _tasks(sl: Path, plan: str) -> dict:  # O(tasks + ledger)
    """Task id -> outcome record {owns, first_time_pass, changed, tests_run, rework_cost, cost_usd}."""
    import metrics  # noqa: PLC0415
    import plan_tools  # noqa: PLC0415
    owns = {t.id: list(t.owns) for t in plan_tools.Plan.load(plan).tasks}
    shas = _checkpoint_shas(sl)
    out: dict = {}
    for r in metrics.task_records(sl, plan):
        tid = r["task"]
        changed = _changed(sl, shas[tid]) if tid in shas else []
        out[tid] = {"owns": owns.get(tid, []), "first_time_pass": r.get("first_time_pass"), "changed": changed,
                    "tests_run": [f for f in changed if _is_test(f)],
                    "rework_cost": r.get("rework_cost") or 0, "cost_usd": r.get("cost_usd") or 0}
    return out


def _history_rows(plan: str, sl: Path, run: str) -> list:  # O(tasks)
    return history.run_rows(plan, sl, run)


def _fold_incidents(store: dict, rows: list, start: int, run: str, ctx: dict) -> None:  # O(rows)
    hooks: dict = {}
    for n, row in enumerate(rows):
        src = f"{run}:incidents.jsonl:{start}+{n}"
        if learn_signals.is_hook_row(row):
            k = (row["kind"], row.get("tool", ""))
            if src not in store["seen"]:
                store["seen"][src] = 1
                hooks[k] = hooks.get(k, 0) + 1
        elif row.get("file") and row.get("kind"):
            _add(store, "pitfall", lessons_score.module_key(row["file"]), str(row["kind"]),
                 "avoid: " + str(row.get("context", "")), src, run)
    for (kind, tool), count in sorted(hooks.items()):
        ctx["outbox"].append(learn_signals.outbox_row(HOOK_SIGNAL[kind], ctx["repo"], ctx["today"], ctx["version"],
                                                      run=run, tool=tool, count=count))


def _fold_launches(store: dict, rows: list, start: int, run: str, dispatch: dict, ctx: dict) -> None:  # O(rows)
    for n, row in enumerate(rows):
        src = f"{run}:metrics.jsonl:{start}+{n}"
        if src in store["seen"]:
            continue
        store["seen"][src] = 1
        for mm in learn_signals.model_mismatches([row], dispatch):
            ctx["outbox"].append(learn_signals.outbox_row("model-mismatch", ctx["repo"], ctx["today"], ctx["version"],
                                                          run=run, requested=mm["requested"], ran=mm["ran"]))


def _fold_text_signals(store: dict, inp: dict, run: str) -> None:  # O(rows)
    for n, row in enumerate(inp["findings_new"]):
        if row.get("file"):
            _add(store, "pitfall", lessons_score.module_key(row["file"]), str(row.get("category", "other")),
                 "avoid: " + str(row.get("problem", "")), f"{run}:review-findings.jsonl:{inp['f0']}+{n}", run)
    for path in inp["fixes"]:
        note = learn_signals.parse_fix_note(path.read_text(encoding="utf-8", errors="replace"))
        if note and note["status"] == "FIXED" and note["file"]:
            _add(store, "fix", lessons_score.module_key(note["file"]), note["cause"].lower()[:40],
                 f"do: {note['fix']} (was: {note['cause']})", f"{run}:fixes:{path.name}", run, verified=True)
    for n, row in enumerate(inp["learned"]):
        if row.get("tag") and row.get("text"):
            owns = (inp["tasks"].get(row.get("task")) or {}).get("owns") or []
            _add(store, "practice", (_mods(owns) or ["."])[0], str(row["tag"]), str(row["text"]),
                 f"{run}:learned.jsonl:{inp['l0']}+{n}", run)


def _fold_task(store: dict, tid: str, t: dict, run: str, found_mods: set) -> None:  # O(files)
    ftp = bool(t.get("first_time_pass"))
    for m in _mods(t.get("owns") or []):
        rec = store["modules"].setdefault(m, {}).setdefault(run, {"tasks": 0, "ftp": 0})
        rec["tasks"] += 1
        rec["ftp"] += 1 if ftp else 0
    changed = list(t.get("changed") or [])
    if not (ftp and changed) or lessons_score.module_key(changed[0]) in found_mods:
        return
    mod, src = lessons_score.module_key(changed[0]), f"{run}:tasks.jsonl:{tid}"
    if len(changed) >= 2:
        a, b = changed[0], changed[1]
        _add(store, "practice", mod, f"pair:{Path(a).name}|{Path(b).name}", f"do: change {a} with {b}", src, run)
    for tr in t.get("tests_run") or []:
        _add(store, "practice", mod, f"test:{tr}", f"do: run {tr}", src, run)


def _fold_outcomes(store: dict, inp: dict, run: str) -> None:  # O(tasks + deliveries)
    """Task outcomes: module baselines, practices from first-time passes, and delivery hits (newest outcome wins)."""
    tasks = {k: v for k, v in inp["tasks"].items() if v.get("first_time_pass") is not None}
    found_in = {(lessons_score.module_key(r["file"]), r.get("category", "other")) for r in inp["findings_all"]}
    for m in store["modules"].values():
        m.pop(run, None)
    for tid, t in sorted(tasks.items()):
        _fold_task(store, tid, t, run, {m for m, _ in found_in})
    for d in inp["deliveries"]:
        t = tasks.get(d.get("task"))
        if t is not None and d.get("run", run) == run:
            _record_hits(store, d, bool(t.get("first_time_pass")), found_in, run)


def _record_hits(store: dict, d: dict, ftp: bool, found_in: set, run: str) -> None:  # O(lessons)
    for lid in d.get("lessons") or []:
        les = store["lessons"].get(lid)
        if les is None:
            continue
        hit = ftp and not (les["kind"] == "pitfall" and (les["module"], les.get("key")) in found_in)
        for e in les["deliveries"]:
            if e.get("run") == run and e.get("task") == d["task"]:
                e["hit"] = hit
                break
        else:
            les["deliveries"].append({"run": run, "task": d["task"], "hit": hit})


def _gather(sl: Path, memo: dict, run: str, plan: str | None) -> dict:  # O(input)
    incidents, i0 = _tail(sl / "incidents.jsonl", memo, f"{run}:incidents.jsonl")
    findings_new, f0 = _tail(sl / "review-findings.jsonl", memo, f"{run}:review-findings.jsonl")
    learned, l0 = _tail(sl / "learned.jsonl", memo, f"{run}:learned.jsonl")
    launches, m0 = _tail(sl / "metrics.jsonl", memo, f"{run}:metrics.jsonl")
    return {"incidents": incidents, "i0": i0, "findings_new": findings_new, "f0": f0, "learned": learned, "l0": l0,
            "launches": launches, "m0": m0, "dispatch": base.read_json(sl / "dispatch.json", {}) or {},
            "tasks": _tasks(sl, plan) if plan else {},
            "findings_all": [r for r in _all_rows(sl / "review-findings.jsonl") if r.get("file")],
            "fixes": sorted((sl / "fixes").glob("*.md")) if (sl / "fixes").is_dir() else [],
            "deliveries": _all_rows(sl / "deliveries.jsonl")}


def _write_history(sl: Path, plan: str | None, run: str, status: str, repo: str, inp: dict) -> None:  # O(tasks)
    if not plan:
        return
    owns = {tid: set(_mods(t.get("owns") or [])) for tid, t in inp["tasks"].items()}
    rows = []
    for r in _history_rows(plan, sl, run):
        mods = owns.get(r["task"], set())
        nfind = sum(1 for f in inp["findings_all"] if lessons_score.module_key(f["file"]) in mods)
        rows.append(dict(r, repository=repo, status=status, findings=nfind))
    history.record_rows(rows, sl)


def sync(sl, plan=None, run=None, status: str = "unfinished", final: bool = False) -> list:  # O(input) per sync
    sl = Path(sl)
    hub, home = lessons_store.hub_dir(sl.parent), history.home()
    run = run or (run_archive.read_current_run(sl) or {}).get("id") or "unknown"
    ctx = {"repo": history.repository_id(sl), "today": _date.today().isoformat(), "version": _version(), "outbox": []}
    state_path = hub / "learn-state.json"
    memo = base.read_json(state_path, {}) or {}
    memo.setdefault("offsets", {})
    inp = _gather(sl, memo, run, plan)
    events: list = []

    def fn(store: dict) -> dict:  # O(input)
        store.setdefault("seen", {})
        ctx["outbox"].clear()
        lessons_store.note_run(store, run)
        _fold_incidents(store, inp["incidents"], inp["i0"], run, ctx)
        _fold_launches(store, inp["launches"], inp["m0"], run, inp["dispatch"], ctx)
        _fold_text_signals(store, inp, run)
        if plan:
            _fold_outcomes(store, inp, run)
        events.extend(lessons_store.step(store))
        return store

    lessons_store.update(hub, fn)
    _write_history(sl, plan, run, status, ctx["repo"], inp)
    _append(home / "learn" / "outbox.jsonl", ctx["outbox"])
    base.locked_update(state_path, lambda _old: memo)  # watermarks last
    if final:
        c = {w: sum(1 for e in events if e.startswith(w + " ")) for w in ("admitted", "evicted", "proven")}
        print(f"learn: admitted {c['admitted']}, evicted {c['evicted']}, proven {c['proven']}")
    return events


def spawn(sl, plan=None) -> None:  # O(1)
    """Start `learn sync` detached; never raises."""
    argv = [sys.executable, str(Path(__file__).resolve()), "sync"] + (["--plan", str(plan)] if plan else [])
    try:
        base.spawn_background(argv, log=state.path(Path(sl).parent, "learn.log"))
    except (*base.advisory_errors(), subprocess.SubprocessError):
        pass


def note(sl, run: str, task: str, tag: str, text: str) -> None:  # O(1)
    _append(Path(sl) / "learned.jsonl", [{"v": 1, "run": run, "task": task, "tag": tag, "text": text}])


def _parser() -> argparse.ArgumentParser:  # O(1)
    p = argparse.ArgumentParser(prog="learn", description="Per-repo learning: sync signals into lessons, note a learned line.")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync", help="fold new run signals into the lessons cache")
    s.add_argument("--plan", help="plan file: adds task outcomes and history rows")
    s.add_argument("--final", action="store_true", help="print the admitted, evicted, proven counts")
    s.add_argument("--status", default="unfinished", choices=("finished", "unfinished"))
    s.add_argument("--sl", help="run dir (default: the current checkout's)")
    s.add_argument("--run", help="run id (default: the current run)")
    n = sub.add_parser("note", help="record a `Learned:` line for a task")
    n.add_argument("--task", required=True)
    n.add_argument("--tag", required=True, choices=("setup", "test", "pairing", "convention"))
    n.add_argument("--text", required=True)
    n.add_argument("--sl", help="run dir (default: the current checkout's)")
    n.add_argument("--run", help="run id (default: the current run)")
    return p


def main(argv=None) -> int:  # O(input)
    a = _parser().parse_args(argv)
    sl = Path(a.sl) if a.sl else state.run_dir(Path.cwd())
    try:
        if a.cmd == "sync":
            sync(sl, a.plan, a.run, a.status, a.final)
        else:
            run = a.run or (run_archive.read_current_run(sl) or {}).get("id") or "unknown"
            note(sl, run, a.task, a.tag, a.text)
    except (*base.advisory_errors(), KeyError, json.JSONDecodeError) as exc:
        print(f"learn: warning: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
