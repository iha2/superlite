#!/usr/bin/env python3
"""The feature flow's prototype: a sandbox folder in this checkout, the human's try-out, then discard.

    proto.py start --topic T [--run CMD] [--reach TEXT] [--plan P.feature.md] [--level L1|L2]
    proto.py tryout [--run CMD] [--reach TEXT] [--start]  |  tryout --approve [--by NAME] [--ruling TEXT]
    proto.py status [--json]
    proto.py discard

State: `.superlite/feature.json` {topic, plan, sandbox, session, run_id, run, reach, level, status, rounds, started,
tryout_time, tryout_approved, tryout_by, tryout_approved_time, proto_report}. status is building (the prototype build
runs in the sandbox), tryout (shown to the human, waiting for the reply), approved, or discarded. `start` makes the
folder `.superlite/sandboxes/<session>/<topic>/` in this checkout (no worktree, no branch; the prototype sees the
checkout's uncommitted code) and records the session row's slug and the run id (`current-run.json`); run again on the same
topic it reopens the prototype for a fix round (status building, same run id). The prototype build shares this
checkout's `.superlite`, so its plan, lint, dispatch and report run here. Every state change updates the session row's
`sandboxes` list (never fatal). `tryout` prints the run command, how to reach the prototype and the feature plan's
acceptance checks, and sets status tryout; `--start` also launches the run command in the background (log in
`.superlite/proto-run.log`). `tryout --approve` records the try-out approval. Human mode (orchestrator.json mode human): it
needs a real typed human reply that approves ("it works", "approve", ...) newer than the prototype's last checkpoint (the
later of the try-out time and this checkout's checkpoints.json), read from `.superlite/human-replies.jsonl` written by
hooks/prompt; else exit 3 with a BLOCKED line. The coordinator sends `--approve` when it understands that reply as approval. AI mode or no orchestrator file: `--ruling TEXT` is appended to
`.superlite/progress.md` as a `Ruling:` line. While the status is tryout in human mode the integration build is gated:
orchestrator.gate refuses lint --stable, dispatch, wave0 and build_prime, and hooks/pre-tool denies agent
dispatch. `discard` saves the prototype's report headline into feature.json (for the report's two parts), removes the
sandbox and archives the build's run files (proto_discard). A legacy feature.json with `worktree` (and no `sandbox`) still
works; its discard removes the worktree and the branch. Stdlib only, no language assumed.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import base  # noqa: E402
import checkpoint  # noqa: E402
import orchestrator  # noqa: E402
import run_archive  # noqa: E402
import sandbox  # noqa: E402
import session_sandboxes  # noqa: E402
import state  # noqa: E402

FILE = "feature.json"
STATUSES = ("building", "tryout", "approved", "discarded")
NO_REPLY = ("BLOCKED: no human reply since the prototype was shown; show how to try it, ask the human to reply "
            "\"it works\" or list the changes, end your turn")
TOPIC = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
MAX_PROTO_TASKS = 8  # a larger prototype plan means the scope is wrong


def git(*args, cwd=None, check=True):
    try:
        return base.git(*args, cwd=Path(cwd or Path.cwd()), check=check)
    except base.GitError as e:
        sys.exit(f"proto: {e}")


def read_state(sl) -> dict | None:  # O(file)
    d = base.read_json(Path(sl) / FILE, None)
    return d if isinstance(d, dict) and d.get("status") in STATUSES else None


def write_state(sl, st: dict) -> None:
    base.write_json(Path(sl) / FILE, st)


def now_ms() -> int:
    return int(time.time() * 1000)


def iso(ms) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ms / 1000)) if ms else "?"


def human_mode(sl) -> bool:  # O(file)
    st = orchestrator.read_state(sl)
    return bool(st and st["mode"] == "human")


def tryout_pending(sl) -> bool:  # O(file)
    """True while a human-mode try-out waits for its approval: the integration build must not start."""
    st = read_state(sl)
    return bool(st and st["status"] == "tryout" and human_mode(sl))


def run_dir_of(st: dict) -> Path | None:  # O(1)
    """The `.superlite` folder the prototype build shares: the sandbox's checkout, else a legacy worktree's own."""
    if st.get("sandbox"):
        return Path(st["sandbox"]).parents[2]
    return state.run_dir(Path(st["worktree"])) if st.get("worktree") else None


def sandbox_rel(st: dict) -> str | None:  # O(1)
    """The sandbox as a repo-relative posix path (absolute when it is outside the checkout)."""
    if not st.get("sandbox"):
        return None
    sb = Path(st["sandbox"])
    return sb.relative_to(sb.parents[3]).as_posix()


def last_checkpoint_ms(st: dict) -> float:
    """The prototype's last checkpoint: the later of the try-out time and the build's checkpoints.json."""
    t = st.get("tryout_time") or 0
    sl = run_dir_of(st)
    cp = sl / "checkpoints.json" if sl else None
    return max(t, cp.stat().st_mtime * 1000) if cp and cp.is_file() else t


def acceptance_checks(plan) -> list[str]:  # O(P)
    """The bullets under the feature plan's `## Acceptance checks` heading."""
    try:
        lines = Path(plan).read_text(encoding="utf-8").splitlines()
    except (OSError, TypeError):
        return []
    out, on = [], False
    for ln in lines:
        if ln.startswith("#"):
            on = "acceptance" in ln.lower()
        elif on and re.match(r"^\s*(?:[-*+]|\d+\.)\s+", ln):
            out.append(re.sub(r"^\s*(?:[-*+]|\d+\.)\s+", "", ln).strip())
    return out


def newest_feature_plan() -> str | None:  # O(F log F)
    found = sorted(Path("docs/plans").glob("*.feature.md"), key=lambda f: f.stat().st_mtime)
    return found[-1].as_posix() if found else None


def proto_report(st: dict, run_dir: Path | None = None) -> dict | None:  # O(file)
    """The prototype build's report headline: saved at discard, else read live, else the archived run of its run id."""
    if st.get("proto_report"):
        return st["proto_report"]
    sl = Path(run_dir) if run_dir is not None else run_dir_of(st)
    if sl is None:
        return None
    c = base.read_json(sl / "report.json", None)
    if not isinstance(c, dict):
        c = _archived_report(sl.parent, st.get("run_id")) if st.get("sandbox") else _archived_report_legacy(sl.parent)
    if not isinstance(c, dict):
        return None
    out = {"headline": c.get("headline"), "cost_split": c.get("cost_split"), "tasks": len(c.get("tasks") or [])}
    if st.get("sandbox"):
        out.update(run_id=st.get("run_id"), sandbox=sandbox_rel(st))
    return out


def _archived_report(checkout: Path, rid) -> dict | None:  # O(archives)
    """Newest report for stable run id `rid`, with legacy archive-id lookup."""
    if not rid:
        return None
    runs = run_archive.runs_dir(checkout)
    best = None
    for meta in runs.glob("*/run.json") if runs.is_dir() else ():
        m = base.read_json(meta, None)
        if not isinstance(m, dict) or rid not in (m.get("run_id"), m.get("id")):
            continue
        rep = base.read_json(meta.parent / "report.json", None)
        archived_at = str(m.get("archived_at") or "")
        if isinstance(rep, dict) and (best is None or (archived_at, str(m.get("id"))) > best[:2]):
            best = (archived_at, str(m.get("id")), rep)
    return best[2] if best else None


def _archived_report_legacy(worktree: Path) -> dict | None:  # O(archives)
    """report.json of the newest archived run (hub `.superlite/runs/*/run.json`, by archived_at) of this checkout."""
    try:
        runs = state.hub_root(worktree) / state.DIR_NAME / "runs"
        mine = worktree.resolve()
        best = None
        for meta in runs.glob("*/run.json") if runs.is_dir() else ():
            m = base.read_json(meta, None)
            if not isinstance(m, dict) or not m.get("checkout") or Path(m["checkout"]).resolve() != mine:
                continue
            rep = base.read_json(meta.parent / "report.json", None)
            if isinstance(rep, dict) and (best is None or str(m.get("archived_at") or "") > best[0]):
                best = (str(m.get("archived_at") or ""), rep)
        return best[1] if best else None
    except (OSError, ValueError, base.GitError):
        return None


def status_line(st: dict | None, sl=None) -> str:
    if not st:
        return f"proto: none (no {state.DIR_NAME}/feature.json)"
    where = f"sandbox {sandbox_rel(st)}" if st.get("sandbox") else f"worktree {st.get('worktree')} on {st.get('branch')}"
    s = f"proto: {st['topic']} {st['status']}, {where}"
    if st["status"] == "approved":
        s += f"; try-out approved {iso(st.get('tryout_approved_time'))} by {st.get('tryout_by', '?')}"
    elif st["status"] == "tryout":
        hit = orchestrator.approving_reply(sl, last_checkpoint_ms(st))
        s += "; waiting for the human's try-out reply" + (f" (approving reply at {iso(hit['t'])})" if hit else "")
    return s


def record_row(st: dict, **extra) -> None:  # O(registry)
    """Mirror the prototype onto its session row's `sandboxes` list; never fatal (`session reconcile` reports drift)."""
    if not st.get("sandbox"):
        return
    try:
        sb = Path(st["sandbox"])
        session_sandboxes.record(sb.parents[3], st["topic"], session=st.get("session"),
                                 path=sandbox_rel(st), run_id=st.get("run_id"), status=st["status"], **extra)
    except (OSError, ValueError, KeyError, TypeError, IndexError, base.GitError):
        pass


def run_id_for(sl: Path, topic: str, session: str) -> str | None:  # O(file)
    """The id in current-run.json (created when missing), kept across fix rounds."""
    try:
        cur = run_archive.read_current_run(sl) or run_archive.ensure_run(sl, topic, session)[0]
        return cur["id"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def sandbox_for(root: Path, topic: str) -> tuple[str, Path] | None:  # O(1)
    """(session slug, sandbox folder) for `topic`, or None when the path would leave `<root>/.superlite/sandboxes`."""
    session = sandbox.session_of(root)
    sb = Path(sandbox.path_for(root, session, topic))
    home = (root / state.DIR_NAME / "sandboxes").resolve()
    return (session, sb) if sb.resolve().is_relative_to(home) and sb.resolve() != home else None


def cmd_start(a, sl) -> int:
    if not TOPIC.match(a.topic or ""):
        print("proto: --topic must be a short slug (a-z, 0-9, -)", file=sys.stderr)
        return 2
    st = read_state(sl)
    live = st and (Path(st["sandbox"]).is_dir() if st.get("sandbox") else Path(st.get("worktree") or "-").is_dir())
    if st and st["topic"] == a.topic and st["status"] != "discarded" and live:  # fix round
        st.update(status="building", **{k: v for k, v in (("run", a.run), ("reach", a.reach)) if v})
        st["rounds"] = st.get("rounds", 1) + 1
        write_state(sl, st)
        record_row(st)
        print(status_line(st) + f" (fix round {st['rounds']})")
        return 0
    if st and st["status"] not in ("discarded", "approved") and st["topic"] != a.topic:
        print(f"proto: prototype {st['topic']} is still open; run superlite-cli proto discard first", file=sys.stderr)
        return 2
    root = Path(git("rev-parse", "--show-toplevel").stdout.strip())
    made = sandbox_for(root, a.topic)
    if not made:
        print("proto: the sandbox path leaves the sandboxes folder", file=sys.stderr)
        return 2
    session, sb = made
    if sb.exists():
        print(f"proto: {sb.as_posix()} already exists; run superlite-cli proto discard or remove it", file=sys.stderr)
        return 2
    checkpoint.setup_exclude(root)
    sb.mkdir(parents=True)
    st = {"v": 1, "topic": a.topic, "plan": a.plan or newest_feature_plan(), "sandbox": sb.as_posix(), "session": session,
          "run_id": run_id_for(sl, a.topic, session), "run": a.run, "reach": a.reach, "level": a.level or "L1",
          "status": "building", "rounds": 1, "started": now_ms(), "tryout_time": None, "tryout_approved": False}
    write_state(sl, st)
    record_row(st)
    print(status_line(st))
    print(f"proto: build the prototype there with its own plan (at most {MAX_PROTO_TASKS} tasks): "
          f"superlite-cli plan_tools lint --stable --plan {sandbox_rel(st)}/docs/plans/<date>-{a.topic}.proto.md")
    return 0


def cmd_tryout(a, sl) -> int:  # O(P + R)
    st = read_state(sl)
    if not st or st["status"] == "discarded":
        print("proto: no open prototype; run superlite-cli proto start --topic <topic>", file=sys.stderr)
        return 2
    if a.approve:
        if st["status"] != "tryout":
            print(f"proto: status is {st['status']}; run superlite-cli proto tryout first so the human can try it", file=sys.stderr)
            return 2
        if human_mode(sl):  # a human answered after the try-out; the coordinator judged that answer
            hit = orchestrator.newest_reply(sl, last_checkpoint_ms(st))
            if not hit:
                print(NO_REPLY, file=sys.stderr)
                return 3
            st["tryout_reply"] = hit.get("text", "")
        elif a.ruling:  # AI mode: the automatic acceptance checks decide, as a Ruling
            with (Path(sl) / "progress.md").open("a", encoding="utf-8") as fh:
                fh.write(f"Ruling: [tryout] {a.ruling}" + chr(10))
        st.update(status="approved", tryout_approved=True, tryout_by=a.by, tryout_approved_time=now_ms())
        rep = proto_report(st)
        if rep:
            st["proto_report"] = rep
        write_state(sl, st)
        record_row(st)
        print(status_line(st, sl))
        return 0
    for k in ("run", "reach"):
        if getattr(a, k):
            st[k] = getattr(a, k)
    st.update(status="tryout", tryout_time=now_ms(), tryout_approved=False)
    rep = proto_report(st)
    if rep:
        st["proto_report"] = rep
    write_state(sl, st)
    record_row(st)
    wt = st.get("sandbox") or st["worktree"]
    print(status_line(st, sl))
    print(f"run: cd \"{wt}\" && {st.get('run') or '<the run command: pass --run>'}")
    if st.get("reach"):
        print(f"reach: {st['reach']}")
    checks = acceptance_checks(st.get("plan"))
    print("try:" if checks else "try: (no `## Acceptance checks` in the feature plan)")
    for c in checks:
        print(f"  - {c}")
    if a.start and st.get("run"):
        import _pyrun
        log = Path(sl) / "proto-run.log"
        p = _pyrun.spawn_detached([base.bash(), "-c", st["run"]], log, cwd=wt)
        st["pid"] = p.pid
        write_state(sl, st)
        print(f"started: pid {p.pid}, log {log.as_posix()}")
    print("next: show it, ask the human to reply \"it works\" or list changes, end your turn; then, if you understand"
          " the answer as approval, superlite-cli proto tryout --approve"
          " (changes: superlite-cli proto start --topic " + st["topic"] + " reopens it for a fix round)")
    return 0


def cmd_discard(a, sl) -> int:
    st = read_state(sl)
    if not st or st["status"] == "discarded":
        print("proto: nothing to discard")
        return 0
    if st.get("sandbox"):
        import proto_discard  # imports proto, so not at module level
        done = proto_discard.discard_open(sl) or read_state(sl) or st
        record_row(done, ended=iso(done.get("discarded") or now_ms()))
        print(status_line(done))
        return 0
    rep = proto_report(st)  # legacy worktree prototype
    if rep:
        st["proto_report"] = rep
    if st.get("adopted"):  # Claude owns the worktree and its branch
        st.update(status="discarded", discarded=now_ms(), branch_kept=True)
        write_state(sl, st)
        print(status_line(st))
        print("proto: leave the sandbox with ExitWorktree remove")
        return 0
    if Path(st["worktree"]).is_dir():
        git("worktree", "remove", "--force", st["worktree"])
    git("worktree", "prune", check=False)
    git("branch", "-D", st["branch"], check=False)
    st.update(status="discarded", discarded=now_ms(), branch_kept=False)
    write_state(sl, st)
    print(status_line(st))
    return 0


def main(argv=None):  # O(P + R) + git spawns
    ap = argparse.ArgumentParser(description=__doc__.split(chr(10))[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start")
    s.add_argument("--topic", required=True)
    s.add_argument("--plan", help="the feature plan (default: the newest docs/plans/*.feature.md)")
    s.add_argument("--level", choices=["L1", "L2"], default=None, help="L1 stubs (default), L2 an isolated environment")
    t = sub.add_parser("tryout")
    t.add_argument("--start", action="store_true", help="also launch the run command in the background")
    t.add_argument("--approve", action="store_true", help="record the try-out approval; send this when you understand the reply as approval")
    t.add_argument("--by", default="human")
    t.add_argument("--ruling", help="AI mode: the acceptance-check result, recorded as a Ruling: line")
    for p in (s, t):
        p.add_argument("--run", help="how to stand the prototype up, run in the sandbox")
        p.add_argument("--reach", help="how to reach it: a URL and port, a command, or example calls")
    st = sub.add_parser("status")
    st.add_argument("--json", action="store_true")
    d = sub.add_parser("discard")
    for p in (s, t, st, d):
        p.add_argument("--dir", help="the run-state folder itself (default: this checkout's)")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    sl = Path(a.dir) if a.dir else state.run_dir(Path.cwd())
    if a.cmd == "start":
        return cmd_start(a, sl)
    if a.cmd == "tryout":
        return cmd_tryout(a, sl)
    if a.cmd == "discard":
        return cmd_discard(a, sl)
    cur = read_state(sl)
    if a.json or not cur or not cur.get("sandbox"):
        print(json.dumps(cur, indent=1) if a.json else status_line(cur, sl))
    else:
        print(status_line(cur, sl) + f"; session {cur.get('session')}, run {cur.get('run_id')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
