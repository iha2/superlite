#!/usr/bin/env python3
"""Who orchestrates: a human (approves the plan before it runs) or an AI (does everything itself).

    orchestrator.py set human|ai [--by NAME] [--ruling TEXT]
    orchestrator.py approve [--by NAME] [--review PATH]
    orchestrator.py status [--plan P]

State: `.superlite/orchestrator.json` {mode, approved, by, time}. In human mode `plan_tools lint --stable`, `dispatch`,
`wave0` and `build_prime` refuse to run (exit 4, one line) until `approve` has been recorded; `schedule --insert` and
`prune` stay allowed so the plan can be shown whole with its Gantt. In AI mode nothing is gated. With no file nothing is
gated (existing runs behave as before). `--ruling` on `set ai` also appends a `Ruling:` line to `.superlite/progress.md`
(for a headless run where no human can answer).
In human mode `approve` needs `--review PATH` (the plain-language review plan; refused without it, exit 2; a missing file exit 2)
and evidence of a real human reply: `.superlite/human-replies.jsonl` (written only by the UserPromptSubmit hook, one
{"t": epoch ms, "approves": bool, "text": ...} per human prompt while unapproved, or once the approved review plan changed) must hold a line whose t is
later than the review file's modification time, else exit 3 with a BLOCKED line. The coordinator sends `approve` when it
understands that reply as approval; the hook's phrase flag does not decide. The path and sha256 are recorded. AI mode never
needs `approve`. Feature flow: in human mode, while `.superlite/feature.json` (written by `superlite-cli proto`) has status tryout, the same
tools exit 4 with the try-out BLOCKED line until `superlite-cli proto tryout --approve` records the human's try-out reply. `status` and `finish` print the review plan path; `status` also says whether an approving reply exists. Library: gate(plan_or_dir, tool) -> message or None,
require(plan_or_dir, tool) -> exit code (prints the message), read_state, summary_line. Stdlib only.
"""
import argparse
import functools
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402
import state  # noqa: E402

FILE = "orchestrator.json"
REPLIES = "human-replies.jsonl"  # written only by the UserPromptSubmit hook
NO_REPLY = ("BLOCKED: no human approval reply since the review plan was written; show the plan, ask the human to reply approve, "
            "end your turn")
NO_REVIEW = "BLOCKED: write the review plan first, then approve --review <path>"
EXIT_BLOCKED = 4  # exit 3 already means "no Python" for sl
FEATURE = "feature.json"  # written only by `superlite-cli proto`
TRYOUT_BLOCK = ("BLOCKED: the human has not approved the prototype try-out; show it (superlite-cli proto tryout), ask the human to reply "
                "it works or list changes, end your turn, then run superlite-cli proto tryout --approve")
BLOCK = ("BLOCKED: human orchestrator selected, show the plan, ask for approval, then run superlite-cli orchestrator approve")


@functools.lru_cache(maxsize=None)
def _dir_name() -> str:
    """The run-state folder's name, as state.py spells it."""
    return state.run_dir(Path(os.sep)).name


def sl_dir(plan_or_dir=None) -> Path:
    """The run-state folder: the argument itself when it is one, else next to a plan (the plan's repo root), else the nearest one above the working directory that holds an orchestrator.json, else this checkout's."""
    if plan_or_dir is None:
        cwd = Path.cwd().resolve()
        for d in (cwd, *cwd.parents):  # the hooks record replies at the project root; a tool may run from a folder below it
            run = state.run_dir(d)
            if (run / FILE).is_file() or (d / ".git").exists():  # never walk above a checkout root
                return run
        return state.run_dir(cwd)
    p = Path(plan_or_dir).resolve()
    if p.name == _dir_name():
        return p
    if p.is_dir():
        return p / _dir_name()
    root = p.parent
    if root.name == _dir_name():
        return root
    if root.name == "plans" and root.parent.name == "docs":
        return root.parent.parent / _dir_name()
    return root / _dir_name()


def read_state(plan_or_dir=None):  # O(file)
    d = base.read_json(sl_dir(plan_or_dir) / FILE, None)
    return d if isinstance(d, dict) and d.get("mode") in ("human", "ai") else None


def write_state(plan_or_dir, st: dict) -> None:
    base.write_json(sl_dir(plan_or_dir) / FILE, st)


def gate(plan_or_dir=None, tool: str = "") -> str | None:  # O(file)
    """None when the tool may run, else the one-line reason. Only human mode without an approval blocks."""
    st = read_state(plan_or_dir)
    if st and st["mode"] == "human" and not st.get("approved"):
        return BLOCK
    if st and st["mode"] == "human" and feature_status(plan_or_dir) == "tryout":  # the feature flow's second gate
        return TRYOUT_BLOCK
    return None


def feature_status(plan_or_dir=None) -> str | None:  # O(file)
    """The prototype status in `.superlite/feature.json` (written by `superlite-cli proto`), or None; unreadable means None (fail open)."""
    d = base.read_json(sl_dir(plan_or_dir) / FEATURE, None)
    return d.get("status") if isinstance(d, dict) else None


def require(plan_or_dir=None, tool: str = "") -> int:  # O(file)
    """0 to go on; prints the reason to stderr and returns EXIT_BLOCKED when the gate is shut."""
    msg = gate(plan_or_dir, tool)
    if msg:
        print(msg, file=sys.stderr)
        return EXIT_BLOCKED
    return 0


def read_replies(plan_or_dir=None) -> list:  # O(replies)
    f = sl_dir(plan_or_dir) / REPLIES
    out = []
    try:
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for ln in lines:
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        if isinstance(e, dict) and isinstance(e.get("t"), (int, float)):
            out.append(e)
    return out


def approving_reply(plan_or_dir=None, since_ms: float = 0):  # O(replies)
    """The newest human reply with approves true and t later than since_ms, or None."""
    hits = [e for e in read_replies(plan_or_dir) if e.get("approves") is True and e["t"] > since_ms]
    return max(hits, key=lambda e: e["t"]) if hits else None


def newest_reply(plan_or_dir=None, since_ms: float = 0):  # O(replies)
    """The newest human reply with t later than since_ms, or None. The coordinator judged it; the phrase flag is not consulted."""
    hits = [e for e in read_replies(plan_or_dir) if e["t"] > since_ms]
    return max(hits, key=lambda e: e["t"]) if hits else None


def stored_review_path(plan_or_dir, review: Path) -> str:  # O(d) path syscalls
    """The review plan as the state file keeps it: relative to the project root (the run folder's parent) when it lies
    inside, so the reply hook resolves it whatever folder approve ran from and a moved session keeps it; else absolute."""
    path, root = review.resolve(), sl_dir(plan_or_dir).parent.resolve()
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def reply_note(plan_or_dir=None, review=None) -> str:  # O(replies)
    since = 0
    if review and Path(review).is_file():
        since = Path(review).stat().st_mtime * 1000
    hit = approving_reply(plan_or_dir, since)
    if hit:
        return f"approving reply at {datetime.fromtimestamp(hit['t'] / 1000).isoformat(timespec='seconds')}"
    return "no qualifying human reply yet" if review else "no approving human reply yet"


def summary_line(plan_or_dir=None, review=None) -> str:  # O(file)
    st = read_state(plan_or_dir)
    if not st:
        return "orchestrator: not set (ungated)"
    if st["mode"] == "ai":
        return f"orchestrator: AI (set {st.get('time', '?')} by {st.get('by', '?')})"
    if st.get("approved"):
        review = f", review plan {st['review_path']}" if st.get("review_path") else ""
        return f"orchestrator: human, approved {st.get('approved_time') or st.get('time')} by {st.get('approved_by') or st.get('by')}{review}"
    return f"orchestrator: human, awaiting approval (set {st.get('time', '?')}; {reply_note(plan_or_dir, review)})"


def warn_no_session(plan_or_dir=None) -> None:  # O(N)
    """stderr hint when this checkout has no session row; fails open (no git, no registry file, bad json -> silent)."""
    try:
        if os.environ.get("SUPERLITE_NO_AUTO_SESSION") == "1":
            return
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import session
        here = session.toplevel(sl_dir(plan_or_dir).resolve().parent)
        reg = session.read_reg(session.hub_root(here))
        if session.row_at(reg, here, session.resolve_host(session.hub_root(here), reg)) is None:
            print("session: none for this checkout; run superlite-cli session ensure --slug <topic> first", file=sys.stderr)
    except (base.GitError, OSError, ValueError, KeyError, TypeError):
        pass


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split(chr(10))[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("set")
    s.add_argument("mode", choices=["human", "ai"])
    s.add_argument("--by", default="user")
    s.add_argument("--ruling", help=f"with ai: also record a Ruling: line in {_dir_name()}/progress.md")
    a_ = sub.add_parser("approve")
    a_.add_argument("--by", default="human")
    a_.add_argument("--review", help="the approved review plan (docs/plans/<date>-<topic>.review.md): path and sha256 are recorded")
    st = sub.add_parser("status")
    st.add_argument("--review", help="review plan path: status says whether a reply newer than it approves")
    for p in (s, a_, st):
        p.add_argument("--plan", help=f"plan path (locates {_dir_name()}); default ./{_dir_name()}")
    a = ap.parse_args(argv)
    if a.cmd == "set":
        write_state(a.plan, {"mode": a.mode, "approved": a.mode == "ai", "by": a.by, "time": now()})
        if a.mode == "ai" and a.ruling:
            f = sl_dir(a.plan) / "progress.md"
            with f.open("a", encoding="utf-8") as fh:
                fh.write(f"Ruling: {a.ruling}" + chr(10))
        print(summary_line(a.plan))
        warn_no_session(a.plan)
        import exclusive  # peer plugins stay off until superlite-cli finish
        print(exclusive.suspend(a.plan))
        return 0
    cur = read_state(a.plan)
    if a.cmd == "approve":
        if not cur or cur["mode"] != "human":
            print("orchestrator: approve applies to human mode only; run: superlite-cli orchestrator set human", file=sys.stderr)
            return 2
        if not a.review:  # a human orchestrator approves a written review plan, never a bare yes
            print(NO_REVIEW, file=sys.stderr)
            return 2
        if a.review:
            rp = Path(a.review)
            if not rp.is_file():
                print(f"orchestrator: review plan not found: {a.review}", file=sys.stderr)
                return 2
            hit = newest_reply(a.plan, rp.stat().st_mtime * 1000)  # a human answered; the coordinator judged it
            if not hit:
                print(NO_REPLY, file=sys.stderr)
                return 3
            cur["approved_reply"] = hit.get("text", "")
            cur.update(review_path=stored_review_path(a.plan, rp), review_sha256=hashlib.sha256(rp.read_bytes()).hexdigest())
        cur.update(approved=True, approved_by=a.by, approved_time=now())
        write_state(a.plan, cur)
    print(summary_line(a.plan, getattr(a, "review", None)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
