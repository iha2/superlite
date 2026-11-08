#!/usr/bin/env python3
"""Suggest a superlite flow from the request text and the project state. Deterministic: same input, same line.

    suggest_flow.py --text "<request>" [--root .] [--state "empty folder"|"existing code"|"run in progress"] [--json]

Prints one line: `suggest: <flow> - <reason> | also fits: <flow>, <flow>`. Flows: regular, mvp, feature, debug, small,
review, research, and resume / status for a run in progress. The project state is computed like hooks/session-start: run in
progress when `.superlite/orchestrator.json` and `docs/plans/` exist, existing code when there is a `.git` or any entry,
else empty folder. Signals, in order: 1. project state (empty folder: mvp or regular; run in progress: resume or status
first), 2. words (bug words: debug; change words: feature; new-app words: mvp or regular; rename/typo/bump: small;
review words: review; research words: research when no change word, build word or
bug word competes), 3. size (one file or one function named: small; screens, data and services together: feature on
existing code, regular when new). The model only confirms or overrides the suggestion with a one-line reason.
Stdlib only, no language assumed.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import session_hud  # noqa: E402
import state  # noqa: E402

FLOWS = ("regular", "mvp", "feature", "debug", "small", "review", "research", "resume", "status")
EMPTY, EXISTING, RUNNING = "empty folder", "existing code", "run in progress"

# whole-word phrases per flow; the first hit names the reason
WORDS = {
    "debug": ("fails", "failing", "failed", "failure", "bug", "bugs", "error", "errors", "broken", "crash", "crashes",
              "exception", "traceback", "stack trace", "regression", "doesn't work", "does not work", "not working",
              "wrong output", "hangs", "flaky"),
    "review": ("review", "audit", "harden", "code review", "check my branch", "look over"),
    "research": ("research", "investigate", "survey", "literature", "compare", "sources", "state of the art",
                 "findings", "evidence"),
    "small": ("rename", "typo", "typos", "bump", "one-line", "one line", "tweak", "reword", "fix the wording"),
    "mvp": ("mvp", "prototype", "proof of concept", "poc", "smallest version", "quick version", "first version",
            "minimal version"),
    "new": ("build", "new app", "new project", "from scratch", "create a", "create an", "make me", "start a",
            "greenfield", "write a", "new tool", "new service"),
    "feature": ("add", "change", "extend", "support", "improve", "modify", "update", "integrate", "allow", "enable",
                "replace", "migrate", "refactor", "make the", "let users"),
    "status": ("status", "where are we", "where is it", "progress", "how far"),
    "resume": ("resume", "continue", "pick up", "carry on", "keep going"),
}
NOUNS = {"error", "errors", "exception", "failure"}  # bug words that are also things a feature adds
SPAN = ("screen", "screens", "page", "pages", "ui", "form", "database", "db", "schema", "table", "api", "endpoint",
        "endpoints", "service", "services", "migration", "auth", "login", "queue", "cache", "report", "dashboard")
TECH_JS = {"node", "next", "nuxt", "vue", "react", "express", "three", "d3", "chart", "deno", "nest", "angular"}
FILE = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)*[\w-]+\.(?:py|js|mjs|cjs|ts|tsx|jsx|go|rs|java|kt|cs|fs|cpp|cc|c|h|hpp|rb|php|swift|"
                  r"scala|ex|exs|erl|dart|lua|vue|svelte|html|css|scss|sql|sh|ps1|md|json|ya?ml|toml|ini|cfg|xml|gradle|tf))\b",
                  re.IGNORECASE)
FUNC = re.compile(r"\b\w+\(\)|\b(?:function|method|func|fn|def)\s+`?\w+", re.IGNORECASE)
TRACE = re.compile(r"Traceback \(most recent call last\)|^\s+at \S+\(.*:\d+\)|\w+(?:Error|Exception): |panic: |"
                   r"error\[E\d+\]|exit code [1-9]", re.MULTILINE)


def _session_row(root: Path) -> dict | None:  # O(N)
    """This checkout's row in the session registry, or None (no registry, not a session, main checkout with none)."""
    try:
        return session_hud.row_for(root)[2]
    except (OSError, ValueError):
        return None


def project_state(root=".") -> str:  # O(n)
    """The session-start hook's line: run in progress, existing code or empty folder."""
    r = Path(root)
    row = _session_row(r)
    if row is not None:
        if session_hud.run_of(row) == "open" and (r / state.DIR_NAME / "orchestrator.json").is_file():
            return RUNNING
    elif (r / state.DIR_NAME / "orchestrator.json").is_file() and (r / "docs" / "plans").is_dir():
        return RUNNING
    try:
        has_entries = any(True for _ in r.iterdir())  # stops at the first entry
    except OSError:
        has_entries = False
    return EXISTING if (r / ".git").exists() or has_entries else EMPTY


def hits(text: str, flow: str) -> list[str]:  # O(w*k)
    """The phrases of WORDS[flow] found in text as whole words, in table order."""
    lc = " " + re.sub(r"\s+", " ", text.lower().replace("’", "'")) + " "
    return [p for p in WORDS[flow] if re.search(r"(?<![\w'])" + re.escape(p) + r"(?![\w'])", lc)]


def size(text: str) -> dict:  # O(w)
    files = {m.group(1) for m in FILE.finditer(text) if not (m.group(1).lower().endswith(".js")
                                                            and m.group(1)[:-3].lower() in TECH_JS)}
    lc = set(re.findall(r"[a-z]+", text.lower()))
    return {"files": sorted(files), "funcs": len(FUNC.findall(text)), "span": sorted(lc & set(SPAN))}


def _q(words) -> str:
    return ", ".join(f'"{w}"' for w in words[:2])


def suggest(text: str, state: str) -> dict:  # O(w*k)
    """{flow, reason, also, state}: the ranked suggestion for this request in this project state."""
    t = text or ""
    h = {f: hits(t, f) for f in WORDS}
    if TRACE.search(t):
        h["debug"] = ["a stack trace"] + h["debug"]
    sz = size(t)
    one_spot = (len(sz["files"]) == 1 or (not sz["files"] and sz["funcs"] == 1)) and len(sz["span"]) < 2
    wide = len(sz["span"]) >= 2
    pick = lambda flow, reason, *also: {"flow": flow, "reason": f"{state}, {reason}", "state": state,  # noqa: E731
                                        "also": [a for a in dict.fromkeys(also) if a != flow]}

    if state == RUNNING:
        if h["status"] and not h["resume"]:
            return pick("status", f"the request asks {_q(h['status'])}", "resume", "regular")
        other = next((f for f in ("debug", "feature", "small") if h[f]), "regular")
        why = f"the request says {_q(h['resume'])}" if h["resume"] else "finish or resume the open run before starting another"
        return pick("resume", why, "status", other)

    inquiry = bool(h["research"]) and not (h["feature"] or h["new"] or h["mvp"] or h["small"])  # asks, builds nothing

    if state == EMPTY:  # nothing to debug, review or change yet
        if inquiry:
            return pick("research", f"the request asks to investigate ({_q(h['research'])})", "mvp", "regular")
        if h["mvp"]:
            return pick("mvp", f"the request says {_q(h['mvp'])}", "regular")
        if h["small"] or (one_spot and not wide and not h["new"]):
            return pick("small", "nothing exists yet and the request names one small piece", "regular", "mvp")
        if wide:
            return pick("regular", f"a new project that spans {', '.join(sz['span'][:3])}", "mvp")
        return pick("mvp" if not h["new"] else "regular",
                    "a new idea, so try the smallest version first" if not h["new"] else f"a new project ({_q(h['new'])})",
                    "regular", "mvp")

    # existing code
    if h["feature"] and set(h["debug"]) <= NOUNS:  # "add error messages to the form" is a change, not a fault
        h["debug"] = []
    if h["debug"]:
        return pick("debug", f"the request reports a fault ({_q(h['debug'])})", "small" if one_spot else "feature", "regular")
    if h["review"] and not h["feature"]:
        return pick("review", f"the request asks to {_q(h['review'])} what exists", "debug", "feature")
    if inquiry:
        return pick("research", f"the request asks to investigate ({_q(h['research'])})", "feature", "review")
    if h["mvp"] or (h["new"] and any(w in t.lower() for w in ("new app", "from scratch", "beside", "separate", "new project"))):
        return pick("mvp" if h["mvp"] else "regular", "a new app beside the existing code"
                    + (f" ({_q(h['mvp'])})" if h["mvp"] else ""), "regular", "mvp", "feature")
    if (h["small"] or one_spot) and not wide:
        where = f"names {sz['files'][0]}" if sz["files"] else "names one function" if sz["funcs"] else f"says {_q(h['small'])}"
        return pick("small", f"the request {where}, a few files at most", "feature", "regular")
    if h["feature"] or wide:
        why = (f"the request changes the app ({_q(h['feature'])})" if h["feature"] else "the change spans")
        if wide:
            why += (" across " if h["feature"] else " ") + ", ".join(sz["span"][:3])
        return pick("feature", why, "small" if not wide else "regular", "regular")
    if h["review"]:
        return pick("review", f"the request asks to {_q(h['review'])} what exists", "feature")
    if h["new"]:
        return pick("feature", f"new behaviour in the existing app ({_q(h['new'])})", "regular", "mvp")
    return pick("feature", "existing code and no stronger signal", "small", "regular")


def line(s: dict) -> str:
    return f"suggest: {s['flow']} - {s['reason']}" + (f" | also fits: {', '.join(s['also'])}" if s["also"] else "")


def main(argv=None):  # O(w*k + n)
    ap = argparse.ArgumentParser(description=__doc__.split(chr(10))[0])
    ap.add_argument("--text", required=True, help="the user's request, as typed")
    ap.add_argument("--root", default=".", help="the project folder (default: the current one)")
    ap.add_argument("--state", choices=[EMPTY, EXISTING, RUNNING], help="override the computed project state")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    s = suggest(a.text, a.state or project_state(a.root))
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(s) if a.json else line(s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
