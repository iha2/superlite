#!/usr/bin/env python3
"""The feature flow's prototype: its own git worktree, the human's try-out, then discard.

    proto.py start --topic T [--run CMD] [--reach TEXT] [--plan P.feature.md] [--level L1|L2] [--path DIR] [--base REF]
    proto.py tryout [--run CMD] [--reach TEXT] [--start]  |  tryout --approve [--by NAME] [--ruling TEXT]
    proto.py status [--json]
    proto.py discard [--keep-branch]

State: `.superlite/feature.json` {topic, plan, worktree, branch, base, run, reach, level, status, started, tryout_time,
tryout_approved, tryout_by, tryout_approved_time, proto_report}. status is building (the prototype build runs in the
worktree), tryout (shown to the human, waiting for the reply), approved, or discarded. `start` cuts the worktree
`.superlite/sandboxes/<topic>-proto` on branch `superlite/<topic>-proto` from the user's HEAD (the user's checkout is never
touched; uncommitted changes are not in the prototype); run again on the same topic it reopens the prototype for a fix
round (status building). The prototype build keeps its own `.superlite` inside the worktree, so its plan, lint, dispatch
and report run there, ungated by this file. `tryout` prints the run command, how to reach the prototype and the feature
plan's acceptance checks, and sets status tryout; `--start` also launches the run command in the background (log in
`.superlite/proto-run.log`). `tryout --approve` records the try-out approval. Human mode (orchestrator.json mode human): it
needs a real typed human reply that approves ("it works", "approve", ...) newer than the prototype's last checkpoint (the
later of the try-out time and the worktree's checkpoints.json), read from `.superlite/human-replies.jsonl` written by
hooks/record-human-reply; else exit 3 with a BLOCKED line. AI mode or no orchestrator file: `--ruling TEXT` is appended to
`.superlite/progress.md` as a `Ruling:` line. While the status is tryout in human mode the integration build is gated:
orchestrator.gate refuses lint --stable, dispatch, wave0 and build_prime, and hooks/block-coordinator-edits denies agent
dispatch. `discard` saves the prototype's report headline into feature.json (for the report's two parts), then removes
the worktree and the branch (`--keep-branch` keeps the branch). Stdlib only, no language assumed.
"""
# Complexity notation:
#   everything here is O(1) file work plus a few git spawns; P = lines of the feature plan, R = human replies
import argparse  # O(1)
import json  # O(1)
import re  # O(1)
import subprocess  # O(1)
import sys  # O(1)
import time  # O(1)
from pathlib import Path  # O(1)

HERE = Path(__file__).resolve().parent  # O(1)
if str(HERE) not in sys.path:  # O(1)
    sys.path.insert(0, str(HERE))  # O(1)

import orchestrator  # noqa: E402  | O(1)

FILE = "feature.json"  # O(1)
STATUSES = ("building", "tryout", "approved", "discarded")  # O(1)
NO_REPLY = ("BLOCKED: no human reply since the prototype was shown; show how to try it, ask the human to reply "
            "\"it works\" or list the changes, end your turn")  # O(1)
TOPIC = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")  # O(1)
MAX_PROTO_TASKS = 8  # a larger prototype plan means the scope is wrong | O(1)


def git(*args, cwd=None, check=True):  # O(1) + process spawn
    r = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd)  # O(out)
    if check and r.returncode != 0:  # O(1)
        sys.exit(f"proto: git {' '.join(args[:2])} failed: {(r.stderr or r.stdout).strip()}")  # O(1)
    return r  # O(1)


def read_state(sl) -> dict | None:  # O(file)
    try:  # O(1)
        d = json.loads((Path(sl) / FILE).read_text(encoding="utf-8"))  # O(file)
    except (OSError, ValueError):  # O(1)
        return None  # O(1)
    return d if isinstance(d, dict) and d.get("status") in STATUSES else None  # O(1)


def write_state(sl, st: dict) -> None:  # O(1)
    Path(sl).mkdir(parents=True, exist_ok=True)  # O(1)
    (Path(sl) / FILE).write_text(json.dumps(st, indent=1) + chr(10), encoding="utf-8")  # O(1)


def now_ms() -> int:  # O(1)
    return int(time.time() * 1000)  # O(1)


def iso(ms) -> str:  # O(1)
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ms / 1000)) if ms else "?"  # O(1)


def human_mode(sl) -> bool:  # O(file)
    st = orchestrator.read_state(sl)  # O(file)
    return bool(st and st["mode"] == "human")  # O(1)


def tryout_pending(sl) -> bool:  # O(file)
    """True while a human-mode try-out waits for its approval: the integration build must not start."""
    st = read_state(sl)  # O(file)
    return bool(st and st["status"] == "tryout" and human_mode(sl))  # O(file)


def last_checkpoint_ms(st: dict) -> float:  # O(1)
    """The prototype's last checkpoint: the later of the try-out time and the worktree's checkpoints.json."""
    t = st.get("tryout_time") or 0  # O(1)
    cp = Path(st.get("worktree") or "") / ".superlite" / "checkpoints.json"  # O(1)
    if st.get("worktree") and cp.is_file():  # O(1)
        t = max(t, cp.stat().st_mtime * 1000)  # O(1)
    return t  # O(1)


def acceptance_checks(plan) -> list[str]:  # O(P)
    """The bullets under the feature plan's `## Acceptance checks` heading."""
    try:  # O(1)
        lines = Path(plan).read_text(encoding="utf-8").splitlines()  # O(P)
    except (OSError, TypeError):  # O(1)
        return []  # O(1)
    out, on = [], False  # O(1)
    for ln in lines:  # O(P)
        if ln.startswith("#"):  # O(1)
            on = "acceptance" in ln.lower()  # O(1)
        elif on and re.match(r"^\s*(?:[-*+]|\d+\.)\s+", ln):  # O(1)
            out.append(re.sub(r"^\s*(?:[-*+]|\d+\.)\s+", "", ln).strip())  # O(1)
    return out  # O(P)


def newest_feature_plan() -> str | None:  # O(F log F)
    found = sorted(Path("docs/plans").glob("*.feature.md"), key=lambda f: f.stat().st_mtime)  # O(F log F)
    return found[-1].as_posix() if found else None  # O(1)


def proto_report(st: dict) -> dict | None:  # O(file)
    """The prototype build's report headline: saved at discard, else read live from the worktree."""
    if st.get("proto_report"):  # O(1)
        return st["proto_report"]  # O(1)
    f = Path(st.get("worktree") or "") / ".superlite" / "report.json"  # O(1)
    try:  # O(1)
        c = json.loads(f.read_text(encoding="utf-8")) if st.get("worktree") else None  # O(file)
    except (OSError, ValueError):  # O(1)
        return None  # O(1)
    return {"headline": c.get("headline"), "cost_split": c.get("cost_split"), "tasks": len(c.get("tasks") or [])} if isinstance(c, dict) else None  # O(1)


def status_line(st: dict | None, sl=None) -> str:  # O(1)
    if not st:  # O(1)
        return "proto: none (no .superlite/feature.json)"  # O(1)
    s = f"proto: {st['topic']} {st['status']}, worktree {st['worktree']} on {st['branch']}"  # O(1)
    if st["status"] == "approved":  # O(1)
        s += f"; try-out approved {iso(st.get('tryout_approved_time'))} by {st.get('tryout_by', '?')}"  # O(1)
    elif st["status"] == "tryout":  # O(1)
        hit = orchestrator.approving_reply(sl, last_checkpoint_ms(st))  # O(R)
        s += "; waiting for the human's try-out reply" + (f" (approving reply at {iso(hit['t'])})" if hit else "")  # O(1)
    return s  # O(1)


def cmd_start(a, sl) -> int:  # O(1) + git spawns
    if not TOPIC.match(a.topic or ""):  # O(1)
        print("proto: --topic must be a short slug (a-z, 0-9, -)", file=sys.stderr)  # O(1)
        return 2  # O(1)
    st = read_state(sl)  # O(file)
    if st and st["topic"] == a.topic and st["status"] != "discarded" and Path(st["worktree"]).is_dir():  # fix round | O(1)
        st.update(status="building", **{k: v for k, v in (("run", a.run), ("reach", a.reach)) if v})  # O(1)
        st["rounds"] = st.get("rounds", 1) + 1  # O(1)
        write_state(sl, st)  # O(1)
        print(status_line(st) + f" (fix round {st['rounds']})")  # O(1)
        return 0  # O(1)
    if st and st["status"] not in ("discarded", "approved") and st["topic"] != a.topic:  # O(1)
        print(f"proto: prototype {st['topic']} is still open; run sl proto discard first", file=sys.stderr)  # O(1)
        return 2  # O(1)
    root = Path(git("rev-parse", "--show-toplevel").stdout.strip())  # O(1) + process spawn
    wt = Path(a.path) if a.path else root / ".superlite" / "sandboxes" / f"{a.topic}-proto"  # O(1)
    branch = f"superlite/{a.topic}-proto"  # O(1)
    base = a.base or git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()  # O(1) + process spawn
    if wt.exists():  # O(1)
        print(f"proto: {wt} already exists; pass --path or remove it", file=sys.stderr)  # O(1)
        return 2  # O(1)
    wt.parent.mkdir(parents=True, exist_ok=True)  # O(1)
    if git("check-ignore", "-q", str(wt), cwd=root, check=False).returncode != 0 and wt.is_relative_to(root):  # O(1) + process spawn
        import gitignore  # O(1)
        gitignore.ensure(str(root), [])  # appends the common block (.superlite/ among it), never rewrites a line | O(g)
    has_branch = git("rev-parse", "--verify", "-q", f"refs/heads/{branch}", check=False).returncode == 0  # O(1) + process spawn
    git("worktree", "add", *([str(wt), branch] if has_branch else ["-b", branch, str(wt), base]))  # O(tree) + process spawn
    dirty = bool(git("status", "--porcelain", "--untracked-files=no", cwd=root, check=False).stdout.strip())  # O(1) + process spawn
    st = {"v": 1, "topic": a.topic, "plan": a.plan or newest_feature_plan(), "worktree": wt.as_posix(), "branch": branch,
          "base": base, "run": a.run, "reach": a.reach, "level": a.level, "status": "building", "rounds": 1,
          "started": now_ms(), "tryout_time": None, "tryout_approved": False}  # O(1)
    write_state(sl, st)  # O(1)
    print(status_line(st))  # O(1)
    print(f"proto: build the prototype there with its own plan (at most {MAX_PROTO_TASKS} tasks): "
          f"sl plan_tools lint --stable --plan {wt.as_posix()}/docs/plans/<date>-{a.topic}.proto.md")  # O(1)
    if dirty:  # O(1)
        print("proto: note: your uncommitted changes are not in the prototype (it is cut from HEAD)")  # O(1)
    return 0  # O(1)


def cmd_tryout(a, sl) -> int:  # O(P + R)
    st = read_state(sl)  # O(file)
    if not st or st["status"] == "discarded":  # O(1)
        print("proto: no open prototype; run sl proto start --topic <topic>", file=sys.stderr)  # O(1)
        return 2  # O(1)
    if a.approve:  # O(R)
        if st["status"] != "tryout":  # O(1)
            print(f"proto: status is {st['status']}; run sl proto tryout first so the human can try it", file=sys.stderr)  # O(1)
            return 2  # O(1)
        if human_mode(sl):  # the same evidence as plan approval: a typed reply, written by the hook | O(file)
            hit = orchestrator.approving_reply(sl, last_checkpoint_ms(st))  # O(R)
            if not hit:  # O(1)
                print(NO_REPLY, file=sys.stderr)  # O(1)
                return 3  # O(1)
            st["tryout_reply"] = hit.get("text", "")  # O(1)
        elif a.ruling:  # AI mode: the automatic acceptance checks decide, as a Ruling | O(1)
            with (Path(sl) / "progress.md").open("a", encoding="utf-8") as fh:  # O(1)
                fh.write(f"Ruling: [tryout] {a.ruling}" + chr(10))  # O(1)
        st.update(status="approved", tryout_approved=True, tryout_by=a.by, tryout_approved_time=now_ms())  # O(1)
        rep = proto_report(st)  # O(file)
        if rep:  # O(1)
            st["proto_report"] = rep  # O(1)
        write_state(sl, st)  # O(1)
        print(status_line(st, sl))  # O(1)
        return 0  # O(1)
    for k in ("run", "reach"):  # O(1)
        if getattr(a, k):  # O(1)
            st[k] = getattr(a, k)  # O(1)
    st.update(status="tryout", tryout_time=now_ms(), tryout_approved=False)  # O(1)
    rep = proto_report(st)  # O(file)
    if rep:  # O(1)
        st["proto_report"] = rep  # O(1)
    write_state(sl, st)  # O(1)
    wt = st["worktree"]  # O(1)
    print(status_line(st, sl))  # O(1)
    print(f"run: cd \"{wt}\" && {st.get('run') or '<the run command: pass --run>'}")  # O(1)
    if st.get("reach"):  # O(1)
        print(f"reach: {st['reach']}")  # O(1)
    checks = acceptance_checks(st.get("plan"))  # O(P)
    print("try:" if checks else "try: (no `## Acceptance checks` in the feature plan)")  # O(1)
    for c in checks:  # O(P)
        print(f"  - {c}")  # O(1)
    if a.start and st.get("run"):  # O(1) + process spawn
        import _pyrun  # O(1)
        log = Path(sl) / "proto-run.log"  # O(1)
        p = _pyrun.spawn_detached(["bash", "-c", st["run"]], log, cwd=wt)  # O(1) + process spawn
        st["pid"] = p.pid  # O(1)
        write_state(sl, st)  # O(1)
        print(f"started: pid {p.pid}, log {log.as_posix()}")  # O(1)
    print("next: show it, ask the human to reply \"it works\" or list changes, end your turn; then sl proto tryout --approve"
          " (changes: sl proto start --topic " + st["topic"] + " reopens it for a fix round)")  # O(1)
    return 0  # O(1)


def cmd_discard(a, sl) -> int:  # O(1) + git spawns
    st = read_state(sl)  # O(file)
    if not st or st["status"] == "discarded":  # O(1)
        print("proto: nothing to discard")  # O(1)
        return 0  # O(1)
    rep = proto_report(st)  # O(file)
    if rep:  # O(1)
        st["proto_report"] = rep  # O(1)
    if Path(st["worktree"]).is_dir():  # O(1)
        git("worktree", "remove", "--force", st["worktree"])  # O(tree) + process spawn
    git("worktree", "prune", check=False)  # O(1) + process spawn
    if not a.keep_branch:  # O(1)
        git("branch", "-D", st["branch"], check=False)  # O(1) + process spawn
    st.update(status="discarded", discarded=now_ms(), branch_kept=bool(a.keep_branch))  # O(1)
    write_state(sl, st)  # O(1)
    print(status_line(st) + (" (branch kept)" if a.keep_branch else ""))  # O(1)
    return 0  # O(1)


def main(argv=None):  # O(P + R) + git spawns
    ap = argparse.ArgumentParser(description=__doc__.split(chr(10))[0])  # O(1)
    sub = ap.add_subparsers(dest="cmd", required=True)  # O(1)
    s = sub.add_parser("start")  # O(1)
    s.add_argument("--topic", required=True)  # O(1)
    s.add_argument("--plan", help="the feature plan (default: the newest docs/plans/*.feature.md)")  # O(1)
    s.add_argument("--level", choices=["L1", "L2"], default="L1", help="L1 stubs (default), L2 an isolated environment")  # O(1)
    s.add_argument("--path", help="the worktree folder (default .superlite/sandboxes/<topic>-proto)")  # O(1)
    s.add_argument("--base", help="the ref to cut from (default: the current branch)")  # O(1)
    t = sub.add_parser("tryout")  # O(1)
    t.add_argument("--start", action="store_true", help="also launch the run command in the background")  # O(1)
    t.add_argument("--approve", action="store_true", help="record the try-out approval")  # O(1)
    t.add_argument("--by", default="human")  # O(1)
    t.add_argument("--ruling", help="AI mode: the acceptance-check result, recorded as a Ruling: line")  # O(1)
    for p in (s, t):  # O(1)
        p.add_argument("--run", help="how to stand the prototype up, run in the worktree")  # O(1)
        p.add_argument("--reach", help="how to reach it: a URL and port, a command, or example calls")  # O(1)
    st = sub.add_parser("status")  # O(1)
    st.add_argument("--json", action="store_true")  # O(1)
    d = sub.add_parser("discard")  # O(1)
    d.add_argument("--keep-branch", action="store_true")  # O(1)
    for p in (s, t, st, d):  # O(1)
        p.add_argument("--dir", default=".superlite", help="the .superlite folder itself")  # O(1)
    a = ap.parse_args(argv)  # O(1)
    sys.stdout.reconfigure(encoding="utf-8")  # O(1)
    sl = Path(a.dir)  # O(1)
    if a.cmd == "start":  # O(1)
        return cmd_start(a, sl)  # O(1) + git spawns
    if a.cmd == "tryout":  # O(1)
        return cmd_tryout(a, sl)  # O(P + R)
    if a.cmd == "discard":  # O(1)
        return cmd_discard(a, sl)  # O(1) + git spawns
    cur = read_state(sl)  # O(file)
    print(json.dumps(cur, indent=1) if a.json else status_line(cur, sl))  # O(R)
    return 0  # O(1)


if __name__ == "__main__":  # O(1)
    sys.exit(main())  # O(1)
