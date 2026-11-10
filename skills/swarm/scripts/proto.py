#!/usr/bin/env python3
"""The feature flow's prototype: its own git worktree, the human's try-out, then discard.

    proto.py start --topic T [--run CMD] [--reach TEXT] [--plan P.feature.md] [--level L1|L2] [--path DIR] [--base REF]
    proto.py tryout [--run CMD] [--reach TEXT] [--start]  |  tryout --approve [--by NAME] [--ruling TEXT]
    proto.py status [--json]
    proto.py discard [--keep-branch]

State: `.superlite/feature.json` {topic, plan, worktree, branch, base, run, reach, level, status, started, tryout_time,
tryout_approved, tryout_by, tryout_approved_time, proto_report}. status is building (the prototype build runs in the
worktree), tryout (shown to the human, waiting for the reply), approved, or discarded. `start` cuts the worktree
`.claude/worktrees/<topic>-proto` on branch `superlite/<topic>-proto` from the user's HEAD (the user's checkout is never
touched; uncommitted changes are not in the prototype); run again on the same topic it reopens the prototype for a fix
round (status building). The prototype build keeps its own `.superlite` inside the worktree, so its plan, lint, dispatch
and report run there, ungated by this file. `tryout` prints the run command, how to reach the prototype and the feature
plan's acceptance checks, and sets status tryout; `--start` also launches the run command in the background (log in
`.superlite/proto-run.log`). `tryout --approve` records the try-out approval. Human mode (orchestrator.json mode human): it
needs a real typed human reply that approves ("it works", "approve", ...) newer than the prototype's last checkpoint (the
later of the try-out time and the worktree's checkpoints.json), read from `.superlite/human-replies.jsonl` written by
hooks/prompt; else exit 3 with a BLOCKED line. The coordinator sends `--approve` when it understands that reply as approval. AI mode or no orchestrator file: `--ruling TEXT` is appended to
`.superlite/progress.md` as a `Ruling:` line. While the status is tryout in human mode the integration build is gated:
orchestrator.gate refuses lint --stable, dispatch, wave0 and build_prime, and hooks/pre-tool denies agent
dispatch. `discard` saves the prototype's report headline into feature.json (for the report's two parts), then removes
the worktree and the branch (`--keep-branch` keeps the branch). Stdlib only, no language assumed.
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import base  # noqa: E402
import claude_create  # noqa: E402
import host_harness as harness  # noqa: E402
import ident  # noqa: E402
import orchestrator  # noqa: E402
import state  # noqa: E402
import worktree_home  # noqa: E402

FILE = "feature.json"
STATUSES = ("building", "tryout", "approved", "discarded")
PENDING = "proto-pending.json"  # Claude Code: the create line's topic, resolved plan and expected worktree, until --adopt
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


def last_checkpoint_ms(st: dict) -> float:
    """The prototype's last checkpoint: the later of the try-out time and the worktree's checkpoints.json."""
    t = st.get("tryout_time") or 0
    if not st.get("worktree"):
        return t
    cp = state.path(Path(st["worktree"]), "checkpoints.json")
    return max(t, cp.stat().st_mtime * 1000) if cp.is_file() else t


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


def proto_report(st: dict) -> dict | None:  # O(file)
    """The prototype build's report headline: saved at discard, else read live from the worktree."""
    if st.get("proto_report"):
        return st["proto_report"]
    if not st.get("worktree"):
        return None
    c = base.read_json(state.path(Path(st["worktree"]), "report.json"), None)
    return {"headline": c.get("headline"), "cost_split": c.get("cost_split"), "tasks": len(c.get("tasks") or [])} if isinstance(c, dict) else None


def status_line(st: dict | None, sl=None) -> str:
    if not st:
        return f"proto: none (no {state.DIR_NAME}/feature.json)"
    s = f"proto: {st['topic']} {st['status']}, worktree {st['worktree']} on {st['branch']}"
    if st["status"] == "approved":
        s += f"; try-out approved {iso(st.get('tryout_approved_time'))} by {st.get('tryout_by', '?')}"
    elif st["status"] == "tryout":
        hit = orchestrator.approving_reply(sl, last_checkpoint_ms(st))
        s += "; waiting for the human's try-out reply" + (f" (approving reply at {iso(hit['t'])})" if hit else "")
    return s


def pending(sl) -> dict | None:  # O(file)
    d = base.read_json(Path(sl) / PENDING, None)
    return d if isinstance(d, dict) and d.get("topic") else None


def originating_run(cwd: Path) -> Path | None:  # O(W) file reads + 2 git spawns
    """The run folder, among every checkout of this repo, whose feature.json names the current checkout as its
    prototype (the hub or the session worktree that ran `proto start`), or None."""
    try:
        r = base.git("rev-parse", "--show-toplevel", cwd=cwd, check=False)
        top = Path(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None
        text = base.git("worktree", "list", "--porcelain", cwd=top, check=False).stdout if top else ""
    except base.GitError:
        return None
    for ln in text.splitlines():
        if not ln.startswith("worktree "):
            continue
        other = Path(ident.git_path(ln[len("worktree "):]))
        if ident.same_path(other, top):
            continue
        sl = other / state.DIR_NAME
        for st in (read_state(sl), pending(sl)):
            if st and st.get("worktree") and ident.same_path(Path(st["worktree"]), top):
                return sl
    return None


def cmd_adopt(a, sl) -> int:
    """Complete the originating run's feature.json (else the hub's) for the Claude-created worktree this runs in."""
    cwd = Path.cwd()
    top = Path(git("rev-parse", "--show-toplevel", cwd=cwd).stdout.strip())
    hub = state.hub_root(top)
    if top.resolve() == hub.resolve():
        print("proto: --adopt runs inside the worktree EnterWorktree created, not in the hub", file=sys.stderr)
        return 2
    pend = pending(sl) or {}
    pend = pend if pend.get("topic") == a.topic and ident.same_path(Path(pend.get("worktree") or "."), top) else {}
    branch = git("rev-parse", "--abbrev-ref", "HEAD", cwd=top).stdout.strip()
    ref = a.base or pend.get("base") or git("rev-parse", "--abbrev-ref", "HEAD", cwd=hub).stdout.strip()
    st = {"v": 1, "topic": a.topic, "plan": a.plan or pend.get("plan") or newest_feature_plan(), "worktree": top.as_posix(),
          "branch": branch, "base": ref, "run": a.run or pend.get("run"), "reach": a.reach or pend.get("reach"),
          "level": a.level or pend.get("level") or "L1", "status": "building", "rounds": 1,
          "started": now_ms(), "tryout_time": None, "tryout_approved": False, "adopted": True}
    write_state(sl, st)
    if pend:
        (Path(sl) / PENDING).unlink(missing_ok=True)
    print(status_line(st))
    print(f"proto: build the prototype there with its own plan (at most {MAX_PROTO_TASKS} tasks): "
          f"superlite-cli plan_tools lint --stable --plan {top.as_posix()}/docs/plans/<date>-{a.topic}.proto.md")
    return 0


def cmd_start(a, sl) -> int:
    if not TOPIC.match(a.topic or ""):
        print("proto: --topic must be a short slug (a-z, 0-9, -)", file=sys.stderr)
        return 2
    st = read_state(sl)
    if st and st["topic"] == a.topic and st["status"] != "discarded" and Path(st["worktree"]).is_dir():  # fix round
        st.update(status="building", **{k: v for k, v in (("run", a.run), ("reach", a.reach)) if v})
        st["rounds"] = st.get("rounds", 1) + 1
        write_state(sl, st)
        print(status_line(st) + f" (fix round {st['rounds']})")
        return 0
    if st and st["status"] not in ("discarded", "approved") and st["topic"] != a.topic:
        print(f"proto: prototype {st['topic']} is still open; run superlite-cli proto discard first", file=sys.stderr)
        return 2
    if a.adopt:
        return cmd_adopt(a, sl)
    root = Path(git("rev-parse", "--show-toplevel").stdout.strip())
    base_ref = a.base or git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if harness.under_claude_code() and not a.path and claude_create.hub_on_base(root, base_ref):
        print(claude_create.create_line(f"{a.topic}-proto", base_ref).replace("session: ", "proto: ", 1))
        plan = a.plan or newest_feature_plan()  # resolved here: the new checkout lacks an uncommitted plan
        base.write_json(sl / PENDING, {"topic": a.topic, "plan": Path(plan).resolve().as_posix() if plan else None,
                                       "worktree": (worktree_home.home_path(state.hub_root(root)) / f"{a.topic}-proto").as_posix(),
                                       "base": base_ref, "run": a.run, "reach": a.reach, "level": a.level})
        return 0
    wt = Path(a.path) if a.path else worktree_home.proto_dir(root, a.topic)
    branch = f"superlite/{a.topic}-proto"
    if wt.exists():
        print(f"proto: {wt} already exists; pass --path or remove it", file=sys.stderr)
        return 2
    worktree_home.hide_all(root)
    wt.parent.mkdir(parents=True, exist_ok=True)
    if git("check-ignore", "-q", str(wt), cwd=root, check=False).returncode != 0 and wt.is_relative_to(root):
        import gitignore
        gitignore.ensure(str(root), [])  # appends the common block (.superlite/ among it), never rewrites a line
    has_branch = git("rev-parse", "--verify", "-q", f"refs/heads/{branch}", check=False).returncode == 0
    git("worktree", "add", *([str(wt), branch] if has_branch else ["-b", branch, str(wt), base_ref]))
    dirty = bool(git("status", "--porcelain", "--untracked-files=no", cwd=root, check=False).stdout.strip())
    st = {"v": 1, "topic": a.topic, "plan": a.plan or newest_feature_plan(), "worktree": wt.as_posix(), "branch": branch,
          "base": base_ref, "run": a.run, "reach": a.reach, "level": a.level or "L1", "status": "building", "rounds": 1,
          "started": now_ms(), "tryout_time": None, "tryout_approved": False}
    write_state(sl, st)
    print(status_line(st))
    print(f"proto: build the prototype there with its own plan (at most {MAX_PROTO_TASKS} tasks): "
          f"superlite-cli plan_tools lint --stable --plan {wt.as_posix()}/docs/plans/<date>-{a.topic}.proto.md")
    if dirty:
        print("proto: note: your uncommitted changes are not in the prototype (it is cut from HEAD)")
    enter = harness.enter_line(wt.resolve(), Path.cwd()) if harness.under_claude_code() else None
    if enter:
        print(enter)
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
    wt = st["worktree"]
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
    rep = proto_report(st)
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
    if not a.keep_branch:
        git("branch", "-D", st["branch"], check=False)
    st.update(status="discarded", discarded=now_ms(), branch_kept=bool(a.keep_branch))
    write_state(sl, st)
    print(status_line(st) + (" (branch kept)" if a.keep_branch else ""))
    return 0


def main(argv=None):  # O(P + R) + git spawns
    ap = argparse.ArgumentParser(description=__doc__.split(chr(10))[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start")
    s.add_argument("--topic", required=True)
    s.add_argument("--plan", help="the feature plan (default: the newest docs/plans/*.feature.md)")
    s.add_argument("--level", choices=["L1", "L2"], default=None, help="L1 stubs (default), L2 an isolated environment")
    s.add_argument("--path", help="the worktree folder (default .claude/worktrees/<topic>-proto)")
    s.add_argument("--base", help="the ref to cut from (default: the current branch)")
    s.add_argument("--adopt", action="store_true", help="record the worktree EnterWorktree created (run inside it)")
    t = sub.add_parser("tryout")
    t.add_argument("--start", action="store_true", help="also launch the run command in the background")
    t.add_argument("--approve", action="store_true", help="record the try-out approval; send this when you understand the reply as approval")
    t.add_argument("--by", default="human")
    t.add_argument("--ruling", help="AI mode: the acceptance-check result, recorded as a Ruling: line")
    for p in (s, t):
        p.add_argument("--run", help="how to stand the prototype up, run in the worktree")
        p.add_argument("--reach", help="how to reach it: a URL and port, a command, or example calls")
    st = sub.add_parser("status")
    st.add_argument("--json", action="store_true")
    d = sub.add_parser("discard")
    d.add_argument("--keep-branch", action="store_true")
    for p in (s, t, st, d):
        p.add_argument("--dir", help="the run-state folder itself (default: this checkout's)")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    sl = Path(a.dir) if a.dir else state.run_dir(Path.cwd())
    if not a.dir and read_state(sl) is None:  # inside a Claude-created prototype: its feature.json is the originating run's
        origin = originating_run(Path.cwd())
        if origin:
            sl = origin
        elif a.cmd == "start" and a.adopt:
            sl = state.run_dir(state.hub_root(Path.cwd()))
    if a.cmd == "start":
        return cmd_start(a, sl)
    if a.cmd == "tryout":
        return cmd_tryout(a, sl)
    if a.cmd == "discard":
        return cmd_discard(a, sl)
    cur = read_state(sl)
    print(json.dumps(cur, indent=1) if a.json else status_line(cur, sl))
    return 0


if __name__ == "__main__":
    sys.exit(main())
