#!/usr/bin/env python3
"""Suggest a superlite flow from the request text and the project state. Deterministic: same input, same line.

    suggest_flow.py --text "<request>" [--root .] [--state "empty folder"|"existing code"|"run in progress"] [--json]

Prints one line: `suggest: <flow> - <reason> | also fits: <flow>, <flow>`. Flows: regular, mvp, feature, debug, small,
review, and resume / status for a run in progress. The project state is computed like hooks/session-start: run in
progress when `.superlite/orchestrator.json` and `docs/plans/` exist, existing code when there is a `.git` or any entry,
else empty folder. Signals, in order: 1. project state (empty folder: mvp or regular; run in progress: resume or status
first), 2. words (bug words: debug; change words: feature; new-app words: mvp or regular; rename/typo/bump: small;
review words: review), 3. size (one file or one function named: small; screens, data and services together: feature on
existing code, regular when new). The model only confirms or overrides the suggestion with a one-line reason.
Stdlib only, no language assumed.
"""
# Complexity notation:
#   w = words in the request, k = keywords in the tables (constant), n = entries in the root folder
import argparse  # O(1)
import json  # O(1)
import re  # O(1)
import sys  # O(1)
from pathlib import Path  # O(1)

sys.path.insert(0, str(Path(__file__).resolve().parent))  # O(1)
import session_hud  # noqa: E402  O(1)

FLOWS = ("regular", "mvp", "feature", "debug", "small", "review", "resume", "status")  # O(1)
EMPTY, EXISTING, RUNNING = "empty folder", "existing code", "run in progress"  # O(1)

# whole-word phrases per flow; the first hit names the reason | O(1)
WORDS = {
    "debug": ("fails", "failing", "failed", "failure", "bug", "bugs", "error", "errors", "broken", "crash", "crashes",
              "exception", "traceback", "stack trace", "regression", "doesn't work", "does not work", "not working",
              "wrong output", "hangs", "flaky"),
    "review": ("review", "audit", "harden", "code review", "check my branch", "look over"),
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
NOUNS = {"error", "errors", "exception", "failure"}  # bug words that are also things a feature adds | O(1)
SPAN = ("screen", "screens", "page", "pages", "ui", "form", "database", "db", "schema", "table", "api", "endpoint",
        "endpoints", "service", "services", "migration", "auth", "login", "queue", "cache", "report", "dashboard")  # O(1)
TECH_JS = {"node", "next", "nuxt", "vue", "react", "express", "three", "d3", "chart", "deno", "nest", "angular"}  # O(1)
FILE = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)*[\w-]+\.(?:py|js|mjs|cjs|ts|tsx|jsx|go|rs|java|kt|cs|fs|cpp|cc|c|h|hpp|rb|php|swift|"
                  r"scala|ex|exs|erl|dart|lua|vue|svelte|html|css|scss|sql|sh|ps1|md|json|ya?ml|toml|ini|cfg|xml|gradle|tf))\b",
                  re.IGNORECASE)  # O(1)
FUNC = re.compile(r"\b\w+\(\)|\b(?:function|method|func|fn|def)\s+`?\w+", re.IGNORECASE)  # O(1)
TRACE = re.compile(r"Traceback \(most recent call last\)|^\s+at \S+\(.*:\d+\)|\w+(?:Error|Exception): |panic: |"
                   r"error\[E\d+\]|exit code [1-9]", re.MULTILINE)  # O(1)


def _session_row(root: Path) -> dict | None:  # O(N)
    """This checkout's row in the session registry, or None (no registry, not a session, main checkout with none)."""
    try:  # O(1)
        return session_hud.row_for(root)[2]  # O(N)
    except (OSError, ValueError):  # O(1)
        return None  # O(1)


def project_state(root=".") -> str:  # O(n)
    """The session-start hook's line: run in progress, existing code or empty folder."""
    r = Path(root)  # O(1)
    row = _session_row(r)  # O(N)
    if row is not None:  # O(1)
        if session_hud.run_of(row) == "open" and (r / ".superlite" / "orchestrator.json").is_file():  # O(1)
            return RUNNING  # O(1)
    elif (r / ".superlite" / "orchestrator.json").is_file() and (r / "docs" / "plans").is_dir():  # O(1)
        return RUNNING  # O(1)
    try:  # O(1)
        has_entries = any(True for _ in r.iterdir())  # stops at the first entry | O(1)
    except OSError:  # O(1)
        has_entries = False  # O(1)
    return EXISTING if (r / ".git").exists() or has_entries else EMPTY  # O(1)


def hits(text: str, flow: str) -> list[str]:  # O(w*k)
    """The phrases of WORDS[flow] found in text as whole words, in table order."""
    lc = " " + re.sub(r"\s+", " ", text.lower().replace("’", "'")) + " "  # O(w)
    return [p for p in WORDS[flow] if re.search(r"(?<![\w'])" + re.escape(p) + r"(?![\w'])", lc)]  # O(w*k)


def size(text: str) -> dict:  # O(w)
    files = {m.group(1) for m in FILE.finditer(text) if not (m.group(1).lower().endswith(".js")  # O(w)
                                                            and m.group(1)[:-3].lower() in TECH_JS)}
    lc = set(re.findall(r"[a-z]+", text.lower()))  # O(w)
    return {"files": sorted(files), "funcs": len(FUNC.findall(text)), "span": sorted(lc & set(SPAN))}  # O(w)


def _q(words) -> str:  # O(1)
    return ", ".join(f'"{w}"' for w in words[:2])  # O(1)


def suggest(text: str, state: str) -> dict:  # O(w*k)
    """{flow, reason, also, state}: the ranked suggestion for this request in this project state."""
    t = text or ""  # O(1)
    h = {f: hits(t, f) for f in WORDS}  # O(w*k)
    if TRACE.search(t):  # O(w)
        h["debug"] = ["a stack trace"] + h["debug"]  # O(1)
    sz = size(t)  # O(w)
    one_spot = (len(sz["files"]) == 1 or (not sz["files"] and sz["funcs"] == 1)) and len(sz["span"]) < 2  # O(1)
    wide = len(sz["span"]) >= 2  # O(1)
    pick = lambda flow, reason, *also: {"flow": flow, "reason": f"{state}, {reason}", "state": state,  # noqa: E731 | O(1)
                                        "also": [a for a in dict.fromkeys(also) if a != flow]}

    if state == RUNNING:  # O(1)
        if h["status"] and not h["resume"]:  # O(1)
            return pick("status", f"the request asks {_q(h['status'])}", "resume", "regular")  # O(1)
        other = next((f for f in ("debug", "feature", "small") if h[f]), "regular")  # O(1)
        why = f"the request says {_q(h['resume'])}" if h["resume"] else "finish or resume the open run before starting another"  # O(1)
        return pick("resume", why, "status", other)  # O(1)

    if state == EMPTY:  # nothing to debug, review or change yet | O(1)
        if h["mvp"]:  # O(1)
            return pick("mvp", f"the request says {_q(h['mvp'])}", "regular")  # O(1)
        if h["small"] or (one_spot and not wide and not h["new"]):  # O(1)
            return pick("small", "nothing exists yet and the request names one small piece", "regular", "mvp")  # O(1)
        if wide:  # O(1)
            return pick("regular", f"a new project that spans {', '.join(sz['span'][:3])}", "mvp")  # O(1)
        return pick("mvp" if not h["new"] else "regular",
                    "a new idea, so try the smallest version first" if not h["new"] else f"a new project ({_q(h['new'])})",
                    "regular", "mvp")  # O(1)

    # existing code
    if h["feature"] and set(h["debug"]) <= NOUNS:  # "add error messages to the form" is a change, not a fault | O(1)
        h["debug"] = []  # O(1)
    if h["debug"]:  # O(1)
        return pick("debug", f"the request reports a fault ({_q(h['debug'])})", "small" if one_spot else "feature", "regular")  # O(1)
    if h["review"] and not h["feature"]:  # O(1)
        return pick("review", f"the request asks to {_q(h['review'])} what exists", "debug", "feature")  # O(1)
    if h["mvp"] or (h["new"] and any(w in t.lower() for w in ("new app", "from scratch", "beside", "separate", "new project"))):  # O(w)
        return pick("mvp" if h["mvp"] else "regular", "a new app beside the existing code"
                    + (f" ({_q(h['mvp'])})" if h["mvp"] else ""), "regular", "mvp", "feature")  # O(1)
    if (h["small"] or one_spot) and not wide:  # O(1)
        where = f"names {sz['files'][0]}" if sz["files"] else "names one function" if sz["funcs"] else f"says {_q(h['small'])}"  # O(1)
        return pick("small", f"the request {where}, a few files at most", "feature", "regular")  # O(1)
    if h["feature"] or wide:  # O(1)
        why = (f"the request changes the app ({_q(h['feature'])})" if h["feature"] else "the change spans")  # O(1)
        if wide:  # O(1)
            why += (" across " if h["feature"] else " ") + ", ".join(sz["span"][:3])  # O(1)
        return pick("feature", why, "small" if not wide else "regular", "regular")  # O(1)
    if h["review"]:  # O(1)
        return pick("review", f"the request asks to {_q(h['review'])} what exists", "feature")  # O(1)
    if h["new"]:  # O(1)
        return pick("feature", f"new behaviour in the existing app ({_q(h['new'])})", "regular", "mvp")  # O(1)
    return pick("feature", "existing code and no stronger signal", "small", "regular")  # O(1)


def line(s: dict) -> str:  # O(1)
    return f"suggest: {s['flow']} - {s['reason']}" + (f" | also fits: {', '.join(s['also'])}" if s["also"] else "")  # O(1)


def main(argv=None):  # O(w*k + n)
    ap = argparse.ArgumentParser(description=__doc__.split(chr(10))[0])  # O(1)
    ap.add_argument("--text", required=True, help="the user's request, as typed")  # O(1)
    ap.add_argument("--root", default=".", help="the project folder (default: the current one)")  # O(1)
    ap.add_argument("--state", choices=[EMPTY, EXISTING, RUNNING], help="override the computed project state")  # O(1)
    ap.add_argument("--json", action="store_true")  # O(1)
    a = ap.parse_args(argv)  # O(1)
    s = suggest(a.text, a.state or project_state(a.root))  # O(w*k + n)
    sys.stdout.reconfigure(encoding="utf-8")  # O(1)
    print(json.dumps(s) if a.json else line(s))  # O(1)
    return 0  # O(1)


if __name__ == "__main__":  # O(1)
    sys.exit(main())  # O(1)
