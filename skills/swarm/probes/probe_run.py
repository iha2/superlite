#!/usr/bin/env python3
"""Parallel probe runner (no agents). Stdlib only, Python >= 3.12.

    probe_run.py --adapter PATH [--root DIR] [--app-type T] [--probes DIR]
                 [--categories deep,numeric,...] [--files a,b | --checkpoint SHA]
                 [--timeout 10] [--workers N] [--out .superlite/probes/probe-results.json]
                 [--log .superlite/incidents.jsonl] [--background]

Results file (--out, default <root>/.superlite/probes/probe-results.json):
    {"files": [...], "selected_categories": [...], "categories": {cat: {"status", "cached", "probes",
      "files", ...}}, "failures": [group, ...], "stats": {...}, "probes": [probe, ...]}
  probe: id, category, entry (call|run|request|raw), status, file, line, group_id, input_size,
         message (plus detail, size, elapsed, timeout). status is pass | crash | hang | leak |
         wrong-status; also inconsistent (state check failed), skip, error (probe bug).
         file and line are the innermost in-repo frame of the failure (null on a pass or when
         none is known); group_id points into "failures"; input_size is the size step reached.
  group (one incident each): group_id, key, category, kind, exc, count, probes, size,
         shrunk_size, file, line, regression_test, incident.

Exit code: 0 all probes passed, 1 at least one failure, 2 usage or adapter error.

How it works
  * Probe generators live in pack/<category>.py; project probes (written by the reviewer from the
    plan's Review Focus) go in a --probes DIR, same format (see pack/_common.py).
  * Workers are persistent subprocesses (one thread of a ThreadPoolExecutor drives each). A worker
    imports the adapter once, runs probes, and keeps one server (started on port 0) for all of its
    server probes. The server is health-checked after every probe and restarted only if it died.
  * A probe that does not answer within its category timeout (call 2 s, run 5 s, request 10 s) is a
    hang: that worker's process tree is killed by PID and the worker is replaced. Only PIDs this
    runner started are ever killed; never by image name.
  * Sized probes step up (e.g. 100, 2000, 10000) and stop at the first failure.
  * Failures are grouped by root cause (kind + exception + innermost in-repo file:line), shrunk by
    bisection (<= 5 s per group), turned into tests/regression/test_probe_<cat>_<n>.py and logged as
    one incident per group. Passing categories log a `verified` entry per file.
  * Passes are cached by (category, file, content hash) in <root>/.superlite/probes/cache.json. The
    hash covers the file, the adapter and the probe sources. Failures are always re-probed.
  * Categories with a `verified` entry (a reviewer's, or this runner's with the same hash) for a
    file are skipped for that file.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # this file may be loaded by path
from probe_common import APP_ALIASES, APP_TYPES, CATEGORY_ORDER, load_catalog, probe_modules
from probe_pool import Cfg, Pool, group_key, run_probe
from probe_regress import shrink_group, write_regression
from probe_store import _log_incidents, commit_files, discover_files, load_cache, norm_file, read_log, save_cache, sha_bytes
from probe_worker import worker_main
import base  # noqa: E402 | scripts dir is on sys.path through probe_common
import state  # noqa: E402




# ----------------------------------------------------------------------------- main run
def select_categories(a, catalog) -> list[str]:  # O(c + p)
    if a.categories:
        return [c.strip() for c in a.categories.split(",") if c.strip()]
    if a.app_type:
        t = APP_ALIASES.get(a.app_type.lower(), a.app_type.lower())
        if t not in APP_TYPES:
            raise SystemExit(f"probe_run: unknown --app-type {a.app_type!r}; "
                             f"use one of {', '.join(APP_TYPES)}")
        return list(APP_TYPES[t])
    extra = sorted({p["category"] for p in catalog} - set(CATEGORY_ORDER))
    return CATEGORY_ORDER + extra


def verified_index(entries: list[dict], root: Path) -> dict:  # O(e) total, regex per entry
    idx: dict = {}
    for e in entries:
        if e.get("kind") != "verified":
            continue
        m = re.match(r"probe (\w+)", str(e.get("context", "")))
        if m:
            sm = re.search(r"\[sha:([0-9a-f]+)\]", e.get("context", ""))
            idx.setdefault((m.group(1), norm_file(root, str(e.get("file", "")))), []).append(
                (e.get("agent"), sm.group(1) if sm else None))
    return idx


def is_verified(idx: dict, cat: str, f: str, sha: str) -> bool:  # O(agents per key)
    for agent, s in idx.get((cat, f), []):
        if agent != "probe_run" or (s and sha.startswith(s)):
            return True
    return False


def _split_files(cat: str, files: list[str], file_sha: dict, cache: dict, vidx: dict) -> tuple[list, list, list]:  # O(f)
    """-> (to probe, skipped because verified, cached passes) for one category."""
    todo, skipped_v, cached = [], [], []
    for f in files:
        sha = file_sha[f]
        if f"{cat}|{f}|{sha}" in cache:
            cached.append(f)
        elif is_verified(vidx, cat, f, sha):
            skipped_v.append(f)
        else:
            todo.append(f)
    return todo, skipped_v, cached


def checkout_commit(root: Path, sha: str) -> tuple[Path, Path | None]:  # O(tree files) + git spawn (worktree add, else archive + tar extract)
    """Materialise `sha` in a fresh temp dir: `git worktree add --detach`, else `git archive | tar`.
    Returns (tree, worktree_repo_or_None); remove it with remove_checkout."""
    tree = Path(tempfile.mkdtemp(prefix="probe-commit-")).resolve() / "tree"
    r = subprocess.run(["git", "-C", str(root), "worktree", "add", "--detach", str(tree), sha],
                       capture_output=True, timeout=120)
    if r.returncode == 0:
        return tree, root
    shutil.rmtree(tree.parent, ignore_errors=True)
    tree = Path(tempfile.mkdtemp(prefix="probe-commit-")).resolve() / "tree"
    tree.mkdir()
    arc = subprocess.run(["git", "-C", str(root), "archive", "--format=tar", sha],
                         capture_output=True, timeout=120)
    if arc.returncode != 0:
        shutil.rmtree(tree.parent, ignore_errors=True)
        raise RuntimeError(f"cannot check out {sha}: {arc.stderr.decode(errors='replace').strip()}")
    import io
    import tarfile
    with tarfile.open(fileobj=io.BytesIO(arc.stdout)) as tf:
        tf.extractall(tree, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))
    return tree, None


def remove_checkout(tree: Path, repo: Path | None) -> None:  # O(tree files) + 2 git spawns
    if repo is not None:
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(tree)],
                       capture_output=True, timeout=120)
        subprocess.run(["git", "-C", str(repo), "worktree", "prune"], capture_output=True, timeout=60)
    shutil.rmtree(tree.parent, ignore_errors=True)


def _python_adapter_required(root: Path) -> bool:  # O(file)
    """Missing adapter.py fails only for Python, or when test-cmd.json is absent."""
    cfg = state.path(root, "test-cmd.json")
    if not cfg.is_file():
        return True
    try:
        data = json.loads(cfg.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return True
    if not isinstance(data, dict):
        return True
    lang = str(data.get("language") or "python").strip().lower()
    return lang in ("", "python")


def run_main(a) -> int:  # O(checkout + _run_main) total; one git checkout per --checkpoint run
    root = Path(a.root or os.getcwd()).resolve()
    adapter = Path(a.adapter).resolve() if a.adapter else state.path(root, "probes/adapter.py")
    if not adapter.is_file():
        if a.adapter or _python_adapter_required(root):  # an explicit --adapter still must exist
            print(f"probe_run: adapter not found: {adapter}", file=sys.stderr)
            return 2
        return 0  # seam-only project; nothing to probe
    if not a.commit:
        return _run_main(a, root, root, adapter)
    # --checkpoint: probe that checkpoint's tree, not the working tree. The adapter is usually untracked
    # (.superlite/), so a copy goes to the same relative place in the checkout for its relative ROOT.
    try:
        tree, repo = checkout_commit(root, a.commit)
    except (RuntimeError, OSError, subprocess.SubprocessError) as e:
        print(f"probe_run: {e}", file=sys.stderr)
        return 2
    try:
        run_adapter = adapter
        try:
            rel = adapter.relative_to(root)
            run_adapter = tree / rel
            run_adapter.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(adapter, run_adapter)
        except ValueError:
            pass
        return _run_main(a, root, tree, run_adapter)
    finally:
        remove_checkout(tree, repo)


def _run_main(a, root: Path, work: Path, adapter: Path) -> int:  # O(c*p + f*bytes + p log p + g*(t*B + shrink)) total + process spawns and probe I/O
    """Probe the tree at `work` (== root, or a checkout of --checkpoint); logs, cache, results and
    regression tests always go under `root`."""
    t_start = time.time()
    probes_dir = Path(a.probes).resolve() if a.probes else None
    if probes_dir and not probes_dir.is_dir():
        print(f"probe_run: --probes is not a directory: {probes_dir}", file=sys.stderr)
        return 2

    def under_root(p: str) -> Path:
        return Path(p) if Path(p).is_absolute() else root / p

    out_path = under_root(a.out) if a.out else state.path(root, "probes/probe-results.json")
    log_path = under_root(a.log) if a.log else state.path(root, "incidents.jsonl")
    cache_path = state.path(root, "probes/cache.json")
    cfg = Cfg(work, adapter, probes_dir, a.timeout)
    cfg.out_root = root

    if a.commit:
        try:
            files = commit_files(root, a.commit, work)
        except (RuntimeError, OSError) as e:
            print(f"probe_run: {e}", file=sys.stderr)
            return 2
    elif a.files:
        files = [norm_file(root, f) for f in a.files.split(",") if f.strip()]
    else:
        files = discover_files(work, adapter, probes_dir) or [cfg.rel(str(adapter))]

    catalog, load_errors = load_catalog(probes_dir)
    try:
        selected = select_categories(a, catalog)
    except SystemExit as e:
        print(e, file=sys.stderr)
        return 2

    pack_digest = sha_bytes(*[p.read_bytes() for p in probe_modules(probes_dir)])
    adapter_bytes = adapter.read_bytes()
    file_sha = {f: sha_bytes((work / f).read_bytes() if (work / f).is_file() else b"<missing>",
                             adapter_bytes, pack_digest.encode()) for f in files}
    log_entries = read_log(log_path)
    vidx = verified_index(log_entries, root)
    cache = load_cache(cache_path)["entries"]

    nworkers = a.workers or os.cpu_count() or 2
    pool = Pool(cfg, nworkers)
    results_run_seconds = 0.0
    results: dict = {"version": 1, "root": str(root), "adapter": cfg.rel(str(adapter)),
                     **({"commit": a.commit} if a.commit else {}),
                     "files": files, "selected_categories": selected, "categories": {},
                     "probes": [], "failures": [], "load_errors": load_errors}
    try:
        try:
            w0 = pool.acquire()
        except RuntimeError as e:
            print(f"probe_run: {e}", file=sys.stderr)
            return 2
        entries = dict(w0.entries)
        pool.release(w0)

        pending: dict[str, list[str]] = {}
        for cat in selected:
            cprobes = [p for p in catalog if p["category"] == cat and entries.get(p["entry"])]
            if not cprobes:
                results["categories"][cat] = {"status": "n/a", "probes": 0,
                                              "reason": "no probe applies to this adapter's entries"}
                continue
            todo, skipped_v, cached = _split_files(cat, files, file_sha, cache, vidx)
            if todo:
                pending[cat] = todo
            elif cached:
                results["categories"][cat] = {"status": "pass", "cached": True, "probes": 0,
                                              "files": cached, "skipped_verified": skipped_v}
            else:
                results["categories"][cat] = {"status": "skipped", "probes": 0, "files": skipped_v,
                                              "reason": "verified" if skipped_v else "no files"}

        tasks = [p for cat in pending for p in catalog
                 if p["category"] == cat and entries.get(p["entry"])]
        order = {p["id"]: i for i, p in enumerate(tasks)}
        done: list[dict] = []
        t_run = time.time()
        if tasks:
            with cf.ThreadPoolExecutor(max_workers=min(nworkers, len(tasks))) as ex:
                done = list(ex.map(lambda m: run_probe(pool, cfg, m), tasks))
        done.sort(key=lambda r: order[r["id"]])
        results_run_seconds = round(time.time() - t_run, 3)

        # group failures by root cause
        groups: dict[str, dict] = {}
        for r in done:
            if r["status"] in ("pass", "skip"):
                continue
            key, top = group_key(r, cfg)
            g = groups.setdefault(key, {"group_id": f"g{len(groups) + 1}", "key": key, "category": r["category"], "kind": r["status"],
                                        "exc": r.get("exc"), "count": 0, "probe": r["id"],
                                        "size": r.get("size"), "detail": r.get("detail", ""),
                                        "file": cfg.rel(top[0]) if top else None,
                                        "line": top[1] if top else 0, "probes": [], "_rep": r})
            g["count"] += 1
            g["probes"].append(r["id"])
            r["_group"] = groups[key]

        # shrink + regression tests, in parallel (one lease per group)
        def finish_group(g):  # O(shrink + regression) per group
            rep = g.pop("_rep")
            if rep["status"] in ("crash", "hang", "wrong-status", "leak") and "probe_meta" in rep:
                size, best = shrink_group(pool, cfg, g, rep)
            else:
                size, best = rep.get("size"), rep
            g["shrunk_size"] = size
            try:
                g["regression_test"] = write_regression(cfg, g, best, size)
            except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError) as e:
                g["regression_test"] = None
                g["regression_error"] = f"{type(e).__name__}: {e}"
            return g

        glist = list(groups.values())
        if glist:
            with cf.ThreadPoolExecutor(max_workers=min(nworkers, len(glist))) as ex:
                glist = list(ex.map(finish_group, glist))
        results["failures"] = glist
    finally:
        pool.close()

    # per-category status, verified entries, cache
    failed_files: dict[str, set] = {}
    unattributed: set = set()
    for g in results["failures"]:
        (failed_files.setdefault(g["category"], set()).add(g["file"]) if g["file"]
         else unattributed.add(g["category"]))
    new_cache, verified_jobs = {}, []
    for cat, todo in pending.items():
        cres = [r for r in done if r["category"] == cat]
        bad = [r for r in cres if r["status"] not in ("pass", "skip")]
        entry_kinds = sorted({r["entry"] for r in cres})
        status = "fail" if bad else "pass"
        results["categories"][cat] = {"status": status, "cached": False, "probes": len(cres),
                                      "skipped_probes": sum(r["status"] == "skip" for r in cres),
                                      "failed_probes": len(bad), "files": todo, "entries": entry_kinds}
        has_error = any(r["status"] == "error" for r in cres)
        for f in todo:
            if has_error or cat in unattributed or f in failed_files.get(cat, set()):
                continue
            if any(r["status"] == "skip" for r in cres) and all(r["status"] == "skip" for r in cres):
                continue
            new_cache[f"{cat}|{f}|{file_sha[f]}"] = {"time": int(time.time()), "probes": len(cres)}
            maxsize = max([s for r in cres for s in [r.get("size")] if isinstance(s, int)] or [0])
            verified_jobs.append((
                f, f"probe {cat}: {len(cres)} probes pass ({'/'.join(entry_kinds)}), "
                   f"sizes up to {maxsize} [sha:{file_sha[f][:12]}]"))
    results["probes"] = []
    for r in done:
        g = r.get("_group")
        entry = {k: v for k, v in r.items()
                 if k not in ("probe_meta", "input", "frames", "prev_pass_size", "_group")}
        entry.update(file=g["file"] if g else None, line=g["line"] if g else None,
                     group_id=g["group_id"] if g else None, input_size=r.get("size"),
                     message=r.get("detail", ""))
        results["probes"].append(entry)

    # incidents: one per group (deduplicated against the log), one verified per category+file
    seen = {(e.get("file"), e.get("line"), e.get("context")) for e in log_entries}
    jobs = []
    for g in results["failures"]:
        what = g.get("exc") or g["kind"]
        ctx = (f"probe {g['category']} {g['kind']} {what}: {g['detail'][:70]} "
               f"(size {g['shrunk_size']}, {g['count']} probe(s), e.g. {g['probe']})")
        if g.get("regression_test"):
            ctx += f"; test {g['regression_test']}"
        ctx = ctx if len(ctx) <= 200 else ctx[:199] + "…"
        f = g["file"] or cfg.rel(str(adapter))
        g["incident"] = (f, g["line"], ctx)
        kind = {"crash": "exception", "leak": "exception", "hang": "perf"}.get(g["kind"], "test-failure")
        if (f, g["line"], ctx) not in seen:
            jobs.append((f, g["line"], ctx, kind, "open"))
    for f, ctx in verified_jobs:
        jobs.append((f, 0, ctx, "verified", None))
    if jobs:
        _log_incidents(log_path, jobs)
    for g in results["failures"]:
        g["incident"] = {"file": g["incident"][0], "line": g["incident"][1]}
    results["verified_logged"] = [{"file": f, "category": c.split(":")[0].split()[1]} for f, c in verified_jobs]
    if new_cache:
        save_cache(cache_path, new_cache)

    results["stats"] = {
        "elapsed": round(time.time() - t_start, 3), "run_seconds": results_run_seconds,
        "probes_run": len(done),
        "workers_started": cfg.workers_started, "server_starts": sum(cfg.server_starts.values()),
        "server_ports": sorted(cfg.server_ports), "killed_pids": cfg.killed_pids,
        "failure_groups": len(results["failures"]),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    nfail = len(results["failures"]) + sum(r["status"] == "error" for r in done)
    print(f"probe_run: {len(done)} probes, {len(results['failures'])} failure group(s), "
          f"{results['stats']['elapsed']}s -> {out_path}")
    return 1 if nfail else 0


def spawn_background(argv: list[str]) -> int:  # O(argv) + process spawn
    args = [x for x in argv if x != "--background"]
    pid = base.spawn_background([sys.executable, str(Path(__file__).resolve()), *args], Path(os.devnull))
    print(json.dumps({"started": pid}))
    return 0


def main(argv: list[str] | None = None) -> int:  # O(argv) + run
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--adapter", help=f"adapter module (default <root>/{state.DIR_NAME}/probes/adapter.py)")
    ap.add_argument("--root", help="project root (default: cwd)")
    ap.add_argument("--app-type", help=f"one of {', '.join(APP_TYPES)}")
    ap.add_argument("--probes", help="directory of project probe modules")
    ap.add_argument("--categories", help="comma list; overrides --app-type")
    ap.add_argument("--files", help="comma list of files under test, relative to --root")
    ap.add_argument("--checkpoint", dest="commit", help="probe only the files this checkpoint touched")
    ap.add_argument("--timeout", type=float, default=10.0, help="cap on per-probe timeouts (s)")
    ap.add_argument("--workers", type=int, default=0, help="default: CPU count")
    ap.add_argument("--out", default=None,
                    help=f"results file, relative to --root (default {state.DIR_NAME}/probes/probe-results.json)")
    ap.add_argument("--log", default=None, help=f"incident log (default {state.DIR_NAME}/incidents.jsonl)")
    ap.add_argument("--background", action="store_true", help="detach and return at once")
    ap.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.worker:
        return worker_main(a)
    if a.background:
        return spawn_background(argv)
    return run_main(a)


if __name__ == "__main__":
    sys.exit(main())
