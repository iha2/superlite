#!/usr/bin/env python3
"""Learning sync for the per-repo learning loop: a run folder's signals become lessons, history rows and outbox rows.

    learn.py sync [--plan P] [--final] [--status finished|unfinished] [--run ID] [--detached]   fold new signals in
    learn.py note --task Tn --tag setup|test|pairing|convention --text "..."   record a `Learned:` line

Reads incidents, review findings, fix notes, learned lines, launches and (with --plan, or the plan an earlier sync of
this run was given) task outcomes of this checkout's run folder; writes `lessons.json` and `learn-state.json` in the hub
run dir, history rows (newest outcome wins) and anonymised rows to `<history home>/learn/outbox.jsonl`, staged in the
store with their `seen` markers and appended deduped by id. A whole sync holds the hub's learn-state lock. The requested
model of a launch comes from its agent type's agent file (dispatch.json records batches, not agents).
Errors warn and exit 0: every hook point is advisory. Stdlib only.
"""
from __future__ import annotations

import argparse
import hashlib
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
AGENTS_DIR = Path(__file__).resolve().parents[3] / "agents"


def _version() -> str:  # O(1)
    manifest = Path(__file__).resolve().parents[3] / ".claude-plugin" / "plugin.json"
    data = base.read_json(manifest, {})
    value = data.get("version") if isinstance(data, dict) else None
    return str(value) if value else "unknown"


def _ck(sl: Path) -> str:  # O(1)
    """This checkout's key in the hub-shared memo: offsets and plans of different worktrees never collide."""
    return Path(sl).resolve().as_posix()


def _head(path: Path) -> str:  # O(1): at most 4 KiB read
    """Hash of the file's first complete line ("" when none): a new file under the same name restarts its offset."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(4096)
    except OSError:
        return ""
    cut = data.find(b"\n")
    first = data[:cut + 1] if cut >= 0 else (data if len(data) == 4096 else b"")
    return hashlib.sha1(first).hexdigest()[:12] if first else ""


def _tail(path: Path, memo: dict, key: str) -> tuple:  # O(n) in new bytes
    """New rows of path past the stored offset: (rows, start offset); the memo copy is advanced.
    The offset is per checkout and file, not per run: a run change keeps the existing-file boundary, so rows an
    earlier run already synced are never read again. A replaced file (other first line) restarts at 0."""
    ent = memo["offsets"].get(key, 0)
    start, head = (int(ent.get("off", 0)), ent.get("head")) if isinstance(ent, dict) else (int(ent), None)
    cur = _head(path)
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    if (head is not None and head != cur) or start < 0 or start > size:
        start = 0
    rows, end = learn_signals.read_new_lines(path, start)
    memo["offsets"][key] = {"off": end, "head": cur}
    return rows, start


def _run_of(row: dict, run: str) -> str:  # O(1)
    """The run a row came from: its own `run` field, else (legacy rows without one) the syncing run, which is the
    run that appended it, since the per-file boundary only ever reads rows written after the previous sync."""
    r = row.get("run") if isinstance(row, dict) else None
    return r if isinstance(r, str) and r else run


def _note_run(store: dict, r: str, current: str) -> None:  # O(runs)
    """Record an originating run in the run index, ahead of the current run when it is an older one."""
    ri = store["run_index"]
    if r in ri:
        return
    if current in ri:
        ri.insert(ri.index(current), r)
    else:
        ri.append(r)


def _oid(src: str) -> str:  # O(len)
    return hashlib.sha256(src.encode("utf-8")).hexdigest()[:16]


def _queue(store: dict, ctx: dict, signal: str, src: str, **fields) -> None:  # O(1)
    """Stage an outbox row in the store, saved with its `seen` marker; `_publish` appends it later, deduped by id."""
    store["outbox_pending"].append(learn_signals.outbox_row(signal, ctx["repo"], ctx["today"], ctx["version"],
                                                            id=_oid(src), **fields))


def _publish(hub: Path, outbox: Path, saved: dict) -> None:  # O(outbox + pending)
    """Append the staged rows the outbox does not hold yet, then unstage them. A crash anywhere repeats safely."""
    pending = list(saved.get("outbox_pending") or [])
    if not pending:
        return
    have = {r.get("id") for r in _all_rows(outbox) if r.get("id")}
    fresh, ids = [], set()
    for r in pending:
        if r.get("id") not in have and r.get("id") not in ids:
            fresh.append(r)
            ids.add(r.get("id"))
    _append(outbox, fresh)
    done = {r.get("id") for r in pending}

    def unstage(store: dict) -> dict:  # O(pending)
        store["outbox_pending"] = [r for r in store.get("outbox_pending") or [] if r.get("id") not in done]
        return store
    lessons_store.update(hub, unstage)


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


def _plan_owns(plan: str) -> dict:  # O(tasks)
    """Task id -> owned files of a plan; {} when it cannot be read."""
    import plan_tools  # noqa: PLC0415
    try:
        return {t.id: list(t.owns) for t in plan_tools.Plan.load(plan).tasks}
    except (*base.advisory_errors(), KeyError):
        return {}


def _fold_incidents(store: dict, rows: list, start: int, run: str, ctx: dict) -> None:  # O(rows)
    hooks: dict = {}
    for n, row in enumerate(rows):
        rr = _run_of(row, run)
        src = f"{rr}:incidents.jsonl:{start}+{n}"
        if learn_signals.is_hook_row(row):
            if src not in store["seen"]:
                store["seen"][src] = 1
                hooks.setdefault((rr, row["kind"], row.get("tool", "")), []).append(src)
        elif row.get("kind") == "verified":
            continue  # a passing check (run_tests, package review), never a pitfall
        elif row.get("file") and row.get("kind"):
            _note_run(store, rr, run)
            _add(store, "pitfall", lessons_score.module_key(row["file"]), str(row["kind"]),
                 "avoid: " + str(row.get("context", "")), src, rr)
    for (rr, kind, tool), srcs in sorted(hooks.items()):
        _queue(store, ctx, HOOK_SIGNAL[kind], srcs[0], run=rr, tool=tool, count=len(srcs))


def _fold_launches(store: dict, rows: list, start: int, run: str, ctx: dict) -> None:  # O(rows)
    for n, row in enumerate(rows):
        rr = _run_of(row, run)
        src = f"{rr}:metrics.jsonl:{start}+{n}"
        if src in store["seen"]:
            continue
        store["seen"][src] = 1
        for mm in learn_signals.model_mismatches([row], ctx["tiers"]):
            _queue(store, ctx, "model-mismatch", src, run=rr, requested=mm["requested"], ran=mm["ran"])


def _fold_fix(store: dict, path: Path, ck: str, run: str) -> None:  # O(note)
    """A FIXED note becomes a verified practice (key `fix:<goal>`), its source naming the note; the run that first
    saw this note content owns it, so a note left live across a run change is not counted as a second run."""
    try:
        data = path.read_bytes()
    except OSError:
        return
    note = learn_signals.parse_fix_note(learn_signals.decode_note(data))
    if not (note and note["status"] == "FIXED" and note["file"]):
        return
    rr = store["seen"].setdefault(f"fix|{ck}|{path.name}|{hashlib.sha1(data).hexdigest()[:12]}", run)
    _note_run(store, rr, run)
    what = note["what"] or note["goal"]
    text = f"do: {what}" + (f" ({note['why']})" if note["why"] else "")
    _add(store, "practice", lessons_score.module_key(note["file"]), "fix:" + (note["goal"] or what).lower()[:40],
         text, f"{rr}:fixes:{path.name}", rr, verified=True)


def _fold_text_signals(store: dict, inp: dict, run: str) -> None:  # O(rows)
    for n, row in enumerate(inp["findings_new"]):
        if row.get("file"):
            rr = _run_of(row, run)
            _note_run(store, rr, run)
            _add(store, "pitfall", lessons_score.module_key(row["file"]), str(row.get("category", "other")),
                 "avoid: " + str(row.get("problem", "")), f"{rr}:review-findings.jsonl:{inp['f0']}+{n}", rr)
    for path in inp["fixes"]:
        _fold_fix(store, path, inp["ck"], run)
    for n, row in enumerate(inp["learned"]):
        if not (row.get("tag") and row.get("text")):
            continue
        rr = _run_of(row, run)
        owns = (inp["tasks"].get(row.get("task")) or {}).get("owns") if rr == run else \
            inp["owns_of"](rr).get(row.get("task"))
        if not owns:
            continue  # no ownership for this task: never guess a module
        _note_run(store, rr, run)
        _add(store, "practice", _mods(owns)[0], str(row["tag"]), str(row["text"]),
             f"{rr}:learned.jsonl:{inp['l0']}+{n}", rr)


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


def _retract(store: dict, run: str, tids) -> None:  # O(lessons * sources)
    """Drop this run's task-derived evidence for tasks about to be re-derived from their newest outcome; the run
    leaves a lesson only when no other source of that run remains. Other tasks' and runs' evidence is kept."""
    gone = {f"{run}:tasks.jsonl:{tid}" for tid in tids}
    for lid in list(store["lessons"]):
        les = store["lessons"][lid]
        kept = [s for s in les["sources"] if s not in gone]
        if len(kept) == len(les["sources"]):
            continue
        les["sources"] = kept
        if not any(s.startswith(run + ":") for s in kept):
            les["runs"] = [r for r in les["runs"] if r != run]
        if not kept and les["status"] == "candidate":
            del store["lessons"][lid]


def _fold_outcomes(store: dict, inp: dict, run: str) -> None:  # O(tasks + deliveries + lessons)
    """Task outcomes: module baselines, practices from first-time passes, and delivery hits (newest outcome wins)."""
    tasks = {k: v for k, v in inp["tasks"].items() if v.get("first_time_pass") is not None}
    found_in = {(lessons_score.module_key(r["file"]), r.get("category", "other")) for r in inp["findings_all"]}
    for m in store["modules"].values():
        m.pop(run, None)
    _retract(store, run, tasks)
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


def _plan_for(memo: dict, sl: Path, run: str, plan: str | None) -> str | None:  # O(1)
    """The plan of this checkout's run: the one given (remembered for later syncs), else the remembered one, so a
    sync from review recording, session close or registration still sees the run's tasks."""
    key = f"{_ck(sl)}|{run}"
    if plan:
        memo["plans"][key] = Path(plan).resolve().as_posix()
        return plan
    known = memo["plans"].get(key)
    return known if known and Path(known).is_file() else None


def _gather(sl: Path, memo: dict, run: str, plan: str | None) -> dict:  # O(input)
    ck = _ck(sl)
    incidents, i0 = _tail(sl / "incidents.jsonl", memo, f"{ck}|incidents.jsonl")
    findings_new, f0 = _tail(sl / "review-findings.jsonl", memo, f"{ck}|review-findings.jsonl")
    # Learned rows need their task's owned files: with no plan known for this run they wait (offset kept).
    learned, l0 = _tail(sl / "learned.jsonl", memo, f"{ck}|learned.jsonl") if plan else ([], 0)
    launches, m0 = _tail(sl / "metrics.jsonl", memo, f"{ck}|metrics.jsonl")
    plans, cache = memo["plans"], {}

    def owns_of(r: str) -> dict:  # O(tasks), once per run
        if r not in cache:
            p = plans.get(f"{ck}|{r}")
            cache[r] = _plan_owns(p) if p and Path(p).is_file() else {}
        return cache[r]
    return {"ck": ck, "incidents": incidents, "i0": i0, "findings_new": findings_new, "f0": f0, "learned": learned,
            "l0": l0, "launches": launches, "m0": m0, "tasks": _tasks(sl, plan) if plan else {}, "owns_of": owns_of,
            "findings_all": [r for r in _all_rows(sl / "review-findings.jsonl")
                             if r.get("file") and _run_of(r, run) == run],
            "fixes": sorted((sl / "fixes").glob("*.md")) if (sl / "fixes").is_dir() else [],
            "deliveries": _all_rows(sl / "deliveries.jsonl")}


def _owner(path: str, tasks: dict) -> str | None:  # O(tasks * owns)
    """The one task owning a file (exact path, or a directory it owns); the lowest task id on overlap."""
    f = str(path).replace("\\", "/")
    for tid in sorted(tasks):
        for o in tasks[tid].get("owns") or []:
            o = str(o).replace("\\", "/").rstrip("/")
            if f == o or f.startswith(o + "/"):
                return tid
    return None


def _write_history(sl: Path, plan: str | None, run: str, status: str, repo: str, inp: dict) -> None:  # O(tasks)
    if not plan:
        return
    counts: dict = {}
    for f in inp["findings_all"]:  # each finding counts once, for the task owning its file
        tid = _owner(f["file"], inp["tasks"])
        counts[tid] = counts.get(tid, 0) + 1
    rows = [dict(r, repository=repo, status=status, findings=counts.get(r["task"], 0))
            for r in _history_rows(plan, sl, run)]
    history.record_rows(rows, sl)


def _stale(memo: dict, sl: Path, run: str, final: bool, detached: bool) -> bool:  # O(1)
    """True for a sync that must do nothing, checked under the learn-state lock the final sync also holds: a
    non-final sync of a run a final sync already sealed (it would turn `finished` rows back to `unfinished`), or a
    detached checkpoint sync whose originating run is no longer the live one (finish renamed or archived it, or a
    new run began): it would read an emptied folder, erase the run's module totals or recreate live state."""
    if final:
        return False
    if f"{_ck(sl)}|{run}" in memo["final"]:
        return True
    return detached and (run_archive.read_current_run(sl) or {}).get("id") != run


def sync(sl, plan=None, run=None, status: str = "unfinished", final: bool = False,
         detached: bool = False) -> list:  # O(input) per sync
    """One sync, start to end under the hub's learn-state lock: concurrent syncs (detached checkpoint syncs of
    several worktrees) run one after another, each reading the offsets and plans the previous one saved. A final
    sync seals its run; later non-final and detached syncs of it are no-ops (`_stale`)."""
    sl = Path(sl)
    hub, home = lessons_store.hub_dir(sl.parent), history.home()
    run = run or (run_archive.read_current_run(sl) or {}).get("id") or "unknown"
    ctx = {"repo": history.repository_id(sl), "today": _date.today().isoformat(), "version": _version(),
           "tiers": learn_signals.agent_tiers(AGENTS_DIR)}
    events: list = []

    def cycle(memo: dict) -> dict:  # O(input)
        memo = memo if isinstance(memo, dict) else {}
        memo.setdefault("offsets", {})
        memo.setdefault("plans", {})
        memo.setdefault("final", {})
        if _stale(memo, sl, run, final, detached):
            return memo
        p = _plan_for(memo, sl, run, plan)
        inp = _gather(sl, memo, run, p)

        def fn(store: dict) -> dict:  # O(input)
            store.setdefault("seen", {})
            store.setdefault("outbox_pending", [])
            events.clear()
            lessons_store.note_run(store, run)
            _fold_incidents(store, inp["incidents"], inp["i0"], run, ctx)
            _fold_launches(store, inp["launches"], inp["m0"], run, ctx)
            _fold_text_signals(store, inp, run)
            if p:
                _fold_outcomes(store, inp, run)
            events.extend(lessons_store.step(store))
            return store

        saved = lessons_store.update(hub, fn)
        _write_history(sl, p, run, status, ctx["repo"], inp)
        _publish(hub, home / "learn" / "outbox.jsonl", saved)
        if final and run != "unknown":
            memo["final"][f"{_ck(sl)}|{run}"] = 1
        return memo  # watermarks last: saved only after everything above succeeded

    base.locked_update(hub / "learn-state.json", cycle)
    if final:
        c = {w: sum(1 for e in events if e.startswith(w + " ")) for w in ("admitted", "evicted", "proven")}
        print(f"learn: admitted {c['admitted']}, evicted {c['evicted']}, proven {c['proven']}")
    return events


def spawn(sl, plan=None) -> None:  # O(1)
    """Start `learn sync` detached, bound to the run live now (`--run`); never raises. The worker gives up under
    the learn-state lock once that run was sealed by finish's final sync or is no longer the live run."""
    try:
        run = (run_archive.read_current_run(Path(sl)) or {}).get("id")
        argv = [sys.executable, str(Path(__file__).resolve()), "sync", "--detached"] + \
            (["--run", run] if run else []) + (["--plan", str(plan)] if plan else [])
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
    s.add_argument("--detached", action="store_true", help="a checkpoint's background sync: no-op once its run ended")
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
            sync(sl, a.plan, a.run, a.status, a.final, a.detached)
        else:
            run = a.run or (run_archive.read_current_run(sl) or {}).get("id") or "unknown"
            note(sl, run, a.task, a.tag, a.text)
    except (*base.advisory_errors(), KeyError, json.JSONDecodeError) as exc:
        print(f"learn: warning: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
