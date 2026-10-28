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
{"t": epoch ms, "approves": bool, "text": ...} per human prompt while unapproved) must hold a line whose t is
later than the review file's modification time, else exit 3 with a BLOCKED line. The coordinator sends `approve` when it
understands that reply as approval; the hook's phrase flag does not decide. The path and sha256 are recorded. AI mode never
needs `approve`. Feature flow: in human mode, while `.superlite/feature.json` (written by `sl proto`) has status tryout, the same
tools exit 4 with the try-out BLOCKED line until `sl proto tryout --approve` records the human's try-out reply. `status` and `finish` print the review plan path; `status` also says whether an approving reply exists. Library: gate(plan_or_dir, tool) -> message or None,
require(plan_or_dir, tool) -> exit code (prints the message), read_state, summary_line. Stdlib only.
"""
# Complexity notation:
#   everything here is O(1) file work
import argparse  # O(1)
import hashlib  # O(1)
import json  # O(1)
import sys  # O(1)
from datetime import datetime  # O(1)
from pathlib import Path  # O(1)

FILE = "orchestrator.json"  # O(1)
REPLIES = "human-replies.jsonl"  # written only by the UserPromptSubmit hook | O(1)
NO_REPLY = ("BLOCKED: no human approval reply since the review plan was written; show the plan, ask the human to reply approve, "
            "end your turn")  # O(1)
NO_REVIEW = "BLOCKED: write the review plan first, then approve --review <path>"  # O(1)
EXIT_BLOCKED = 4  # exit 3 already means "no Python" for sl | O(1)
FEATURE = "feature.json"  # written only by `sl proto` | O(1)
TRYOUT_BLOCK = ("BLOCKED: the human has not approved the prototype try-out; show it (sl proto tryout), ask the human to reply "
                "it works or list changes, end your turn, then run sl proto tryout --approve")  # O(1)
BLOCK = ("BLOCKED: human orchestrator selected, show the plan, ask for approval, then run sl orchestrator approve")  # O(1)


def sl_dir(plan_or_dir=None) -> Path:  # O(1)
    """The `.superlite` folder: the argument itself when it is one, else next to a plan (the plan's repo root), else ./.superlite."""
    if plan_or_dir is None:  # O(1)
        return Path(".superlite")  # O(1)
    p = Path(plan_or_dir).resolve()  # O(1)
    if p.name == ".superlite":  # O(1)
        return p  # O(1)
    if p.is_dir():  # O(1)
        return p / ".superlite" if p.name != ".superlite" else p  # O(1)
    root = p.parent  # O(1)
    if root.name == ".superlite":  # O(1)
        return root  # O(1)
    if root.name == "plans" and root.parent.name == "docs":  # O(1)
        return root.parent.parent / ".superlite"  # O(1)
    return root / ".superlite"  # O(1)


def read_state(plan_or_dir=None):  # O(file)
    f = sl_dir(plan_or_dir) / FILE  # O(1)
    try:  # O(1)
        d = json.loads(f.read_text(encoding="utf-8"))  # O(file)
    except (OSError, ValueError):  # O(1)
        return None  # O(1)
    return d if isinstance(d, dict) and d.get("mode") in ("human", "ai") else None  # O(1)


def write_state(plan_or_dir, state: dict) -> None:  # O(1)
    d = sl_dir(plan_or_dir)  # O(1)
    d.mkdir(parents=True, exist_ok=True)  # O(1)
    (d / FILE).write_text(json.dumps(state, indent=1) + chr(10), encoding="utf-8")  # O(1)


def gate(plan_or_dir=None, tool: str = "") -> str | None:  # O(file)
    """None when the tool may run, else the one-line reason. Only human mode without an approval blocks."""
    st = read_state(plan_or_dir)  # O(file)
    if st and st["mode"] == "human" and not st.get("approved"):  # O(1)
        return BLOCK  # O(1)
    if st and st["mode"] == "human" and feature_status(plan_or_dir) == "tryout":  # the feature flow's second gate | O(file)
        return TRYOUT_BLOCK  # O(1)
    return None  # O(1)


def feature_status(plan_or_dir=None) -> str | None:  # O(file)
    """The prototype status in `.superlite/feature.json` (written by `sl proto`), or None; unreadable means None (fail open)."""
    try:  # O(1)
        d = json.loads((sl_dir(plan_or_dir) / FEATURE).read_text(encoding="utf-8"))  # O(file)
    except (OSError, ValueError):  # O(1)
        return None  # O(1)
    return d.get("status") if isinstance(d, dict) else None  # O(1)


def require(plan_or_dir=None, tool: str = "") -> int:  # O(file)
    """0 to go on; prints the reason to stderr and returns EXIT_BLOCKED when the gate is shut."""
    msg = gate(plan_or_dir, tool)  # O(file)
    if msg:  # O(1)
        print(msg, file=sys.stderr)  # O(1)
        return EXIT_BLOCKED  # O(1)
    return 0  # O(1)


def read_replies(plan_or_dir=None) -> list:  # O(replies)
    f = sl_dir(plan_or_dir) / REPLIES  # O(1)
    out = []  # O(1)
    try:  # O(1)
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()  # O(file)
    except OSError:  # O(1)
        return out  # O(1)
    for ln in lines:  # O(replies)
        try:  # O(1)
            e = json.loads(ln)  # O(w)
        except ValueError:  # O(1)
            continue  # O(1)
        if isinstance(e, dict) and isinstance(e.get("t"), (int, float)):  # O(1)
            out.append(e)  # O(1)
    return out  # O(replies)


def approving_reply(plan_or_dir=None, since_ms: float = 0):  # O(replies)
    """The newest human reply with approves true and t later than since_ms, or None."""
    hits = [e for e in read_replies(plan_or_dir) if e.get("approves") is True and e["t"] > since_ms]  # O(replies)
    return max(hits, key=lambda e: e["t"]) if hits else None  # O(replies)


def newest_reply(plan_or_dir=None, since_ms: float = 0):  # O(replies)
    """The newest human reply with t later than since_ms, or None. The coordinator judged it; the phrase flag is not consulted."""
    hits = [e for e in read_replies(plan_or_dir) if e["t"] > since_ms]  # O(replies)
    return max(hits, key=lambda e: e["t"]) if hits else None  # O(replies)


def reply_note(plan_or_dir=None, review=None) -> str:  # O(replies)
    since = 0  # O(1)
    if review and Path(review).is_file():  # O(1)
        since = Path(review).stat().st_mtime * 1000  # O(1)
    hit = approving_reply(plan_or_dir, since)  # O(replies)
    if hit:  # O(1)
        return f"approving reply at {datetime.fromtimestamp(hit['t'] / 1000).isoformat(timespec='seconds')}"  # O(1)
    return "no qualifying human reply yet" if review else "no approving human reply yet"  # O(1)


def summary_line(plan_or_dir=None, review=None) -> str:  # O(file)
    st = read_state(plan_or_dir)  # O(file)
    if not st:  # O(1)
        return "orchestrator: not set (ungated)"  # O(1)
    if st["mode"] == "ai":  # O(1)
        return f"orchestrator: AI (set {st.get('time', '?')} by {st.get('by', '?')})"  # O(1)
    if st.get("approved"):  # O(1)
        review = f", review plan {st['review_path']}" if st.get("review_path") else ""  # O(1)
        return f"orchestrator: human, approved {st.get('approved_time') or st.get('time')} by {st.get('approved_by') or st.get('by')}{review}"  # O(1)
    return f"orchestrator: human, awaiting approval (set {st.get('time', '?')}; {reply_note(plan_or_dir, review)})"  # O(1)


def now() -> str:  # O(1)
    return datetime.now().isoformat(timespec="seconds")  # O(1)


def main(argv=None):  # O(1)
    ap = argparse.ArgumentParser(description=__doc__.split(chr(10))[0])  # O(1)
    sub = ap.add_subparsers(dest="cmd", required=True)  # O(1)
    s = sub.add_parser("set")  # O(1)
    s.add_argument("mode", choices=["human", "ai"])  # O(1)
    s.add_argument("--by", default="user")  # O(1)
    s.add_argument("--ruling", help="with ai: also record a Ruling: line in .superlite/progress.md")  # O(1)
    a_ = sub.add_parser("approve")  # O(1)
    a_.add_argument("--by", default="human")  # O(1)
    a_.add_argument("--review", help="the approved review plan (docs/plans/<date>-<topic>.review.md): path and sha256 are recorded")  # O(1)
    st = sub.add_parser("status")  # O(1)
    st.add_argument("--review", help="review plan path: status says whether a reply newer than it approves")  # O(1)
    for p in (s, a_, st):  # O(1)
        p.add_argument("--plan", help="plan path (locates .superlite); default ./.superlite")  # O(1)
    a = ap.parse_args(argv)  # O(1)
    if a.cmd == "set":  # O(1)
        write_state(a.plan, {"mode": a.mode, "approved": a.mode == "ai", "by": a.by, "time": now()})  # O(1)
        if a.mode == "ai" and a.ruling:  # O(1)
            f = sl_dir(a.plan) / "progress.md"  # O(1)
            with f.open("a", encoding="utf-8") as fh:  # O(1)
                fh.write(f"Ruling: {a.ruling}" + chr(10))  # O(1)
        print(summary_line(a.plan))  # O(1)
        import exclusive  # peer plugins stay off until sl finish | O(1)
        print(exclusive.suspend(a.plan))  # O(S)
        return 0  # O(1)
    cur = read_state(a.plan)  # O(file)
    if a.cmd == "approve":  # O(1)
        if not cur or cur["mode"] != "human":  # O(1)
            print("orchestrator: approve applies to human mode only; run: sl orchestrator set human", file=sys.stderr)  # O(1)
            return 2  # O(1)
        if not a.review:  # a human orchestrator approves a written review plan, never a bare yes | O(1)
            print(NO_REVIEW, file=sys.stderr)  # O(1)
            return 2  # O(1)
        if a.review:  # O(1)
            rp = Path(a.review)  # O(1)
            if not rp.is_file():  # O(1)
                print(f"orchestrator: review plan not found: {a.review}", file=sys.stderr)  # O(1)
                return 2  # O(1)
            hit = newest_reply(a.plan, rp.stat().st_mtime * 1000)  # a human answered; the coordinator judged it | O(replies)
            if not hit:  # O(1)
                print(NO_REPLY, file=sys.stderr)  # O(1)
                return 3  # O(1)
            cur["approved_reply"] = hit.get("text", "")  # O(1)
            cur.update(review_path=a.review.replace(chr(92), "/"), review_sha256=hashlib.sha256(rp.read_bytes()).hexdigest())  # O(size)
        cur.update(approved=True, approved_by=a.by, approved_time=now())  # O(1)
        write_state(a.plan, cur)  # O(1)
    print(summary_line(a.plan, getattr(a, "review", None)))  # O(1)
    return 0  # O(1)


if __name__ == "__main__":  # O(1)
    sys.exit(main())  # O(1)
