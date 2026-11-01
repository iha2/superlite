#!/usr/bin/env python3
"""MVP mode: build, stamp and check the prototype page and its feedback file (skills/mvp-prototype).

    mvp_check.py build --plan P --mock FRAGMENT [--page OUT]     template + plan features + mock -> stamped page
    mvp_check.py open [--page OUT]                               opens the page in the default browser (one way to show it)
    mvp_check.py stamp [--page OUT]                              (re)writes the page's sl-prototype-sha meta
    mvp_check.py check --plan P [--page OUT] [--feedback F] [--require-feedback]
    mvp_check.py ai --plan P [--page OUT] [--feedback F]         AI orchestrator: writes the feedback (source ai)
    mvp_check.py fetch [--feedback F] [--page OUT] [--downloads DIR]

Defaults: page `.superlite/mvp/prototype.html`, feedback `.superlite/mvp/mvp-feedback.json`. Features come from the plan's
`### F<n>: Title` headings with `**Size:**`, `**Priority:**` and `**Recommend:**` lines; the plan also has `**Target:**` and
may have `**Estimate:**`. `check` validates the page (it parses, the sl-data block matches the plan, every feature id is
in the mock as data-feature, no external URLs or network calls) and, when the feedback exists, its schema, its features
against the plan, and its prototype_sha against the page. The sha is sha256 of the page with the meta's content emptied,
so stamping does not change it. Nothing opens the page on its own: the agent
chooses how to show it (an in-IDE view, a browser tool, or `open`). Exit 0 ok, 1 problems (one per line), 2 usage or a missing file. Only reads the page and
the JSON: language-agnostic. Stdlib only.
"""
import argparse
import hashlib
import json
import re
import shutil
import sys
import webbrowser
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import state  # noqa: E402

TEMPLATE = Path(__file__).resolve().parents[2] / "mvp-prototype" / "page-template.html"
PAGE = f"{state.DIR_NAME}/mvp/prototype.html"
FEEDBACK = f"{state.DIR_NAME}/mvp/mvp-feedback.json"
KINDS = {"web", "cli", "library", "api", "other"}
SIZES = {"S", "M", "L"}
PRIORITIES = {"must", "should", "could"}
META = re.compile(r'(<meta\s+name="sl-prototype-sha"\s+content=")[^"]*(")', re.I)
DATA = re.compile(r'(<script\s+id="sl-data"\s+type="application/json">)(.*?)(</script>)', re.S | re.I)
FEATURE = re.compile(r"^#{2,4}\s+(F\d+)\s*[:.\-–—]\s*(.+?)\s*$", re.M)
EXTERNAL = [  # what a self-contained page must never contain
    (re.compile(r"\b(?:https?|wss?|ftp)://", re.I), "external URL"),
    (re.compile(r"""(?:src|href|action|srcset)\s*=\s*["']?//""", re.I), "protocol-relative URL"),
    (re.compile(r"url\(\s*['\"]?//", re.I), "protocol-relative CSS url()"),
    (re.compile(r"@import\b", re.I), "CSS @import"),
    (re.compile(r"\bfetch\s*\(|XMLHttpRequest|\bWebSocket\b|\bEventSource\b|sendBeacon|\bimport\s*\(", re.I), "network call"),
]
XMLNS = re.compile(r'xmlns(?::\w+)?\s*=\s*"http://www\.w3\.org/[^"]*"', re.I)  # SVG namespaces are names, not requests
BALANCED = {"html", "head", "body", "main", "aside", "section", "div", "nav", "header", "footer", "script", "style",
            "button", "ul", "ol", "table", "form", "pre", "textarea", "select"}


def page_sha(text: str) -> str:  # O(n)
    """sha256 of the page with the sl-prototype-sha meta's content emptied (stamping leaves it unchanged)."""
    return hashlib.sha256(META.sub(r"\1\2", text).encode("utf-8")).hexdigest()


def stamped_sha(text: str) -> str | None:  # O(n)
    m = re.search(r'<meta\s+name="sl-prototype-sha"\s+content="([^"]*)"', text, re.I)
    return m.group(1) if m else None


def _field(block: str, name: str) -> str:  # O(n)
    m = re.search(r"\*\*" + name + r":\*\*\s*`?([^`\n]*)", block, re.I)
    return m.group(1).strip() if m else ""


def parse_plan(text: str) -> dict:  # O(n)
    """{title, target, estimate, features: [{id, title, size, priority, in}]} from a `.mvp.md` plan."""
    title = re.search(r"^#\s+(.+?)\s*$", text, re.M)
    heads = list(FEATURE.finditer(text))
    feats = []
    for i, m in enumerate(heads):
        block = text[m.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        block = re.split(r"^#{1,4}\s", block, maxsplit=1, flags=re.M)[0]  # a later non-feature heading ends it
        rec = _field(block, "Recommend").lower()
        feats.append({"id": m.group(1), "title": m.group(2), "size": _field(block, "Size")[:1].upper(),
                      "priority": _field(block, "Priority").split()[0].lower() if _field(block, "Priority") else "",
                      "in": not rec.startswith("out"), "recommend": rec.split()[0] if rec else ""})
    return {"title": title.group(1) if title else "", "target": _field(text, "Target").lower(),
            "estimate": parse_estimate(_field(text, "Estimate")), "features": feats}


def parse_estimate(s: str):  # O(len s)
    """`30-45 min, $4-7` -> {min_lo, min_hi, usd_lo, usd_hi}; None when there is nothing to scale."""
    num = r"(\d+(?:\.\d+)?)"
    mins = re.search(num + r"(?:\s*-\s*" + num + r")?\s*min", s)
    usd = re.search(r"\$\s*" + num + r"(?:\s*-\s*\$?\s*" + num + r")?", s)
    if not (mins or usd):
        return None
    out = {}
    for key, m in (("min", mins), ("usd", usd)):
        if m:
            out[key + "_lo"] = float(m.group(1))
            out[key + "_hi"] = float(m.group(2) or m.group(1))
    return out


def target_kinds(target) -> list[str]:
    return [k for k in re.split(r"[\s,+/]+", str(target or "").lower()) if k]


def plan_problems(plan: dict) -> list[str]:  # O(f)
    out = []
    kinds = target_kinds(plan["target"])
    if not kinds or any(k not in KINDS for k in kinds):
        out.append(f"plan: **Target:** must be one or more of {', '.join(sorted(KINDS))}, got '{plan['target']}'")
    if not plan["features"]:
        out.append("plan: no features (headings `### F1: Title`)")
    seen = set()
    for f in plan["features"]:
        if f["id"] in seen:
            out.append(f"plan: feature {f['id']} appears twice")
        seen.add(f["id"])
        if f["size"] not in SIZES:
            out.append(f"plan: {f['id']} needs **Size:** S, M or L")
        if f["priority"] not in PRIORITIES:
            out.append(f"plan: {f['id']} needs **Priority:** must, should or could")
        if f["recommend"] not in ("in", "out"):
            out.append(f"plan: {f['id']} needs **Recommend:** in or out, with the reason")
    return out


class _Tags(HTMLParser):
    """Collects data-feature ids and checks that the structural tags open and close in order."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.problems, self.features, self.ids = [], [], set(), set()

    def handle_starttag(self, tag, attrs):  # O(attrs)
        a = dict(attrs)
        if a.get("data-feature"):
            self.features.update(a["data-feature"].split())
        if a.get("id"):
            self.ids.add(a["id"])
        if tag in BALANCED:
            self.stack.append((tag, self.getpos()[0]))

    def handle_endtag(self, tag):  # O(depth)
        if tag not in BALANCED:
            return
        if not self.stack or self.stack[-1][0] != tag:
            opened = f"<{self.stack[-1][0]}> from line {self.stack[-1][1]}" if self.stack else "nothing"
            self.problems.append(f"page: </{tag}> on line {self.getpos()[0]} closes {opened}")
            while self.stack and self.stack[-1][0] != tag:
                self.stack.pop()
        if self.stack:
            self.stack.pop()


def page_problems(text: str, plan: dict) -> list[str]:  # O(n)
    out = []
    p = _Tags()
    try:
        p.feed(text)
        p.close()
    except (ValueError, AssertionError, IndexError, RecursionError) as e:  # html.parser rarely raises; report it rather than crash
        return [f"page: does not parse ({e})"]
    out += p.problems + [f"page: <{t}> from line {ln} is never closed" for t, ln in p.stack]
    for need in ("sl-mock", "sl-panel", "sl-features", "sl-save", "sl-copy", "sl-data"):
        if need not in p.ids:
            out.append(f"page: missing element id={need} (start from skills/mvp-prototype/page-template.html)")
    if stamped_sha(text) is None:
        out.append('page: missing <meta name="sl-prototype-sha">')
    m = DATA.search(text)
    data = None
    if m:
        try:
            data = json.loads(m.group(2))
        except ValueError as e:
            out.append(f"page: the sl-data block is not JSON ({e})")
    plan_ids = [f["id"] for f in plan["features"]]
    if isinstance(data, dict):
        ids = [f.get("id") for f in data.get("features", []) if isinstance(f, dict)]
        if ids != plan_ids:
            out.append(f"page: sl-data features {ids} differ from the plan's {plan_ids}; rebuild the page")
    for fid in plan_ids:
        if fid not in p.features:
            out.append(f'page: feature {fid} is not in the mock (no data-feature="{fid}")')
    for fid in sorted(p.features - set(plan_ids)):
        out.append(f"page: data-feature {fid} is not a feature of the plan")
    scan = XMLNS.sub("", text)
    for rx, what in EXTERNAL:
        for hit in rx.finditer(scan):
            line = scan.count("\n", 0, hit.start()) + 1
            out.append(f"page: {what} on line {line} ({hit.group(0).strip()}); the page must work offline")
            break  # one line per kind is enough
    return out


def feedback_problems(fb, plan: dict, plan_path: str, sha: str) -> list[str]:  # O(f)
    if not isinstance(fb, dict):
        return ["feedback: not a JSON object"]
    out = []

    def need(key, kind, label):
        if not isinstance(fb.get(key), kind) or (kind is int and isinstance(fb.get(key), bool)):
            out.append(f"feedback: '{key}' must be {label}")
            return False
        return True

    if fb.get("schema") != 1:
        out.append("feedback: 'schema' must be 1")
    if fb.get("source") not in ("human", "ai"):
        out.append("feedback: 'source' must be human or ai")
    if need("plan", str, "the plan path") and Path(fb["plan"]).resolve() != Path(plan_path).resolve():
        out.append(f"feedback: 'plan' is {fb['plan']}, not {plan_path}")
    if need("prototype_sha", str, "a sha256 hex string") and fb["prototype_sha"] != sha:
        out.append("feedback: prototype_sha does not match the page; it was saved from an older page (ask the user to "
                   "save again from the current one)")
    need("approved", bool, "true or false")
    need("keep_as_frontend", bool, "true or false")
    if need("target", str, "the target kind"):
        kinds = target_kinds(fb["target"])
        if not kinds or any(k not in KINDS for k in kinds):
            out.append(f"feedback: 'target' must be one or more of {', '.join(sorted(KINDS))}")
        if fb.get("keep_as_frontend") is True and "web" not in kinds:
            out.append("feedback: keep_as_frontend is only for a web target")
    if need("features", list, "a list"):
        ids, orders = [], []
        for i, f in enumerate(fb["features"]):
            if not (isinstance(f, dict) and isinstance(f.get("id"), str) and isinstance(f.get("title"), str)
                    and isinstance(f.get("in"), bool) and isinstance(f.get("order"), int)
                    and not isinstance(f.get("order"), bool) and isinstance(f.get("note", ""), str)):
                out.append(f"feedback: features[{i}] needs id, title, in (bool), order (int) and note (text)")
                continue
            ids.append(f["id"])
            orders.append(f["order"])
        plan_ids = [f["id"] for f in plan["features"]]
        if len(ids) == len(fb["features"]):
            if sorted(ids) != sorted(plan_ids):
                out.append(f"feedback: features {sorted(set(ids) ^ set(plan_ids)) or 'repeat'} do not match the plan")
            if sorted(orders) != list(range(1, len(orders) + 1)):
                out.append("feedback: feature orders must be 1..n, each once")
    screens = fb.get("screens") if isinstance(fb.get("screens"), list) else []
    if "screens" in fb and need("screens", list, "a list"):
        for i, s in enumerate(screens):
            edits = s.get("edits", []) if isinstance(s, dict) else None
            if not (isinstance(s, dict) and isinstance(s.get("id"), str) and isinstance(edits, list) and all(
                    isinstance(e, dict) and isinstance(e.get("label"), str) and isinstance(e.get("to"), str) for e in edits)):
                out.append(f"feedback: screens[{i}] needs id and edits [{{label, to}}]")
    if "added" in fb and need("added", list, "a list"):
        for i, a in enumerate(fb["added"]):
            if not (isinstance(a, dict) and isinstance(a.get("title"), str) and a["title"].strip()):
                out.append(f"feedback: added[{i}] needs a title")
    if "notes" in fb:
        need("notes", str, "text")
    if fb.get("source") == "ai" and (fb.get("added") or any(isinstance(s, dict) and s.get("edits") for s in screens)):
        out.append("feedback: source ai carries no edits or added features")
    return out


def _read(path: str, what: str) -> str:  # O(n)
    p = Path(path)
    if not p.is_file():
        print(f"mvp_check: {what} {path} not found", file=sys.stderr)
        raise SystemExit(2)
    return p.read_text(encoding="utf-8")


def _write(path: str, text: str) -> None:  # O(n)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text, encoding="utf-8", newline="\n")


def stamp(text: str) -> str:  # O(n)
    return META.sub(lambda m: m.group(1) + page_sha(text) + m.group(2), text)


def build(plan_path: str, plan: dict, mock: str, template: str) -> str:  # O(n)
    data = {"plan": plan_path.replace("\\", "/"), "title": plan["title"], "target": plan["target"],
            "estimate": plan["estimate"],
            "features": [{k: f[k] for k in ("id", "title", "size", "priority", "in")} for f in plan["features"]]}
    blob = json.dumps(data, indent=1).replace("</", "<\\/")  # never closes the script tag early
    page = template.replace("<!--SL:MOCK-->", mock.strip(), 1).replace("/*SL:DATA*/", blob, 1)
    return stamp(page)


def ai_feedback(plan_path: str, plan: dict, sha: str) -> dict:  # O(f)
    return {"schema": 1, "source": "ai", "plan": plan_path.replace("\\", "/"), "prototype_sha": sha, "approved": True,
            "target": plan["target"], "keep_as_frontend": False,
            "features": [{"id": f["id"], "title": f["title"], "in": f["in"], "order": i + 1, "note": ""}
                         for i, f in enumerate(plan["features"])],
            "screens": [], "added": [], "notes": ""}


def fetch(feedback: str, page: str, downloads: str) -> str | None:  # O(files in downloads)
    """The project feedback file, else the newest mvp-feedback*.json in Downloads newer than the page, copied in."""
    dest = Path(feedback)
    since = Path(page).stat().st_mtime if Path(page).is_file() else 0
    cands = [p for p in Path(downloads).glob("mvp-feedback*.json") if p.is_file() and p.stat().st_mtime >= since]
    newest = max(cands, key=lambda p: p.stat().st_mtime, default=None)
    if dest.is_file() and (newest is None or dest.stat().st_mtime >= newest.stat().st_mtime):
        return str(dest)
    if newest is None:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(newest, dest)
    return str(dest)


def open_page(page: str) -> str:
    """Opens the page in the user's default browser (file URI); the message says what happened."""
    uri = Path(page).resolve().as_uri()
    try:
        ok = webbrowser.open(uri)
    except (webbrowser.Error, OSError):  # no browser on this machine
        ok = False
    return f"opened {uri}" if ok else f"could not open a browser; ask the user to open {uri}"


def report(problems: list[str], ok: str) -> int:  # O(problems)
    if problems:
        print(f"mvp_check: {len(problems)} problem(s)")
        print("\n".join(problems))
        return 1
    print(f"mvp_check: ok, {ok}")
    return 0


def main(argv=None) -> int:  # O(n)
    ap = argparse.ArgumentParser(description=__doc__.split(chr(10))[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("build", "stamp", "check", "ai", "fetch", "open"):
        s = sub.add_parser(name)
        s.add_argument("--page", default=PAGE)
        if name in ("build", "check", "ai"):
            s.add_argument("--plan", required=True)
        if name in ("check", "ai", "fetch"):
            s.add_argument("--feedback", default=FEEDBACK)
        if name == "build":
            s.add_argument("--mock", required=True, help="the mock's HTML fragment (the main area)")
            s.add_argument("--template", default=str(TEMPLATE))
        if name == "check":
            s.add_argument("--require-feedback", action="store_true")
        if name == "fetch":
            s.add_argument("--downloads", default=str(Path.home() / "Downloads"))
    a = ap.parse_args(argv)

    if a.cmd == "fetch":
        got = fetch(a.feedback, a.page, a.downloads)
        if got is None:
            print("mvp_check: no mvp-feedback.json in the project or Downloads; ask the user to press Save feedback "
                  "or paste the Copy feedback block")
            return 1
        print(got)
        return 0
    if a.cmd == "open":
        _read(a.page, "page")  # exit 2 when missing
        print(f"mvp_check: {open_page(a.page)}")
        return 0
    if a.cmd == "stamp":
        text = stamp(_read(a.page, "page"))
        _write(a.page, text)
        print(stamped_sha(text))
        return 0

    plan = parse_plan(_read(a.plan, "plan"))
    problems = plan_problems(plan)
    if a.cmd == "build":
        if problems:
            return report(problems, "")
        page = build(a.plan, plan, _read(a.mock, "mock"), _read(a.template, "template"))
        _write(a.page, page)
        return report(page_problems(page, plan), f"wrote {a.page} ({len(plan['features'])} features)")

    text = _read(a.page, "page")
    sha = page_sha(text)
    problems += page_problems(text, plan)
    if stamped_sha(text) not in (None, sha):
        problems.append("page: changed since it was stamped; run superlite-cli mvp_check stamp")
    if a.cmd == "ai":
        if problems:
            return report(problems, "")
        _write(a.feedback, json.dumps(ai_feedback(a.plan, plan, sha), indent=2) + "\n")
        return report([], f"wrote {a.feedback} (source ai)")
    fb_path = Path(a.feedback)
    if fb_path.is_file():
        try:
            fb = json.loads(fb_path.read_text(encoding="utf-8"))
        except ValueError as e:
            fb, problems = None, problems + [f"feedback: not JSON ({e})"]
        if fb is not None:
            problems += feedback_problems(fb, plan, a.plan, sha)
    elif a.require_feedback:
        problems.append(f"feedback: {a.feedback} not found (run superlite-cli mvp_check fetch)")
    kept = sum(1 for f in plan["features"] if f["in"])
    return report(problems, f"{len(plan['features'])} features ({kept} recommended in), page sha {sha[:12]}")


if __name__ == "__main__":
    sys.exit(main())
