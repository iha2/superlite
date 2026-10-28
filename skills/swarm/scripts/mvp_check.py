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
# Complexity notation:
#   n = page or plan size in characters, f = features, t = tags in the page
import argparse  # O(1)
import hashlib  # O(1)
import json  # O(1)
import re  # O(1)
import shutil  # O(1)
import sys  # O(1)
import webbrowser  # O(1)
from html.parser import HTMLParser  # O(1)
from pathlib import Path  # O(1)

sys.path.insert(0, str(Path(__file__).resolve().parent))  # O(1)
import session as sl_session  # noqa: E402 | state_dir for per-session mvp paths | O(1)

TEMPLATE = Path(__file__).resolve().parents[2] / "mvp-prototype" / "page-template.html"  # O(1)
PAGE = ".superlite/mvp/prototype.html"  # legacy default; overridden to the active session when one exists | O(1)
FEEDBACK = ".superlite/mvp/mvp-feedback.json"  # O(1)


def default_page() -> str:  # O(S)
    return str(sl_session.state_dir() / "mvp" / "prototype.html")  # O(S)


def default_feedback() -> str:  # O(S)
    return str(sl_session.state_dir() / "mvp" / "mvp-feedback.json")  # O(S)
KINDS = {"web", "cli", "library", "api", "other"}  # O(1)
SIZES = {"S", "M", "L"}  # O(1)
PRIORITIES = {"must", "should", "could"}  # O(1)
META = re.compile(r'(<meta\s+name="sl-prototype-sha"\s+content=")[^"]*(")', re.I)  # O(1)
DATA = re.compile(r'(<script\s+id="sl-data"\s+type="application/json">)(.*?)(</script>)', re.S | re.I)  # O(1)
FEATURE = re.compile(r"^#{2,4}\s+(F\d+)\s*[:.\-–—]\s*(.+?)\s*$", re.M)  # O(1)
EXTERNAL = [  # what a self-contained page must never contain | O(1)
    (re.compile(r"\b(?:https?|wss?|ftp)://", re.I), "external URL"),
    (re.compile(r"""(?:src|href|action|srcset)\s*=\s*["']?//""", re.I), "protocol-relative URL"),
    (re.compile(r"url\(\s*['\"]?//", re.I), "protocol-relative CSS url()"),
    (re.compile(r"@import\b", re.I), "CSS @import"),
    (re.compile(r"\bfetch\s*\(|XMLHttpRequest|\bWebSocket\b|\bEventSource\b|sendBeacon|\bimport\s*\(", re.I), "network call"),
]
XMLNS = re.compile(r'xmlns(?::\w+)?\s*=\s*"http://www\.w3\.org/[^"]*"', re.I)  # SVG namespaces are names, not requests | O(1)
BALANCED = {"html", "head", "body", "main", "aside", "section", "div", "nav", "header", "footer", "script", "style",
            "button", "ul", "ol", "table", "form", "pre", "textarea", "select"}  # O(1)


def page_sha(text: str) -> str:  # O(n)
    """sha256 of the page with the sl-prototype-sha meta's content emptied (stamping leaves it unchanged)."""
    return hashlib.sha256(META.sub(r"\1\2", text).encode("utf-8")).hexdigest()  # O(n)


def stamped_sha(text: str) -> str | None:  # O(n)
    m = re.search(r'<meta\s+name="sl-prototype-sha"\s+content="([^"]*)"', text, re.I)  # O(n)
    return m.group(1) if m else None  # O(1)


def _field(block: str, name: str) -> str:  # O(n)
    m = re.search(r"\*\*" + name + r":\*\*\s*`?([^`\n]*)", block, re.I)  # O(n)
    return m.group(1).strip() if m else ""  # O(1)


def parse_plan(text: str) -> dict:  # O(n)
    """{title, target, estimate, features: [{id, title, size, priority, in}]} from a `.mvp.md` plan."""
    title = re.search(r"^#\s+(.+?)\s*$", text, re.M)  # O(n)
    heads = list(FEATURE.finditer(text))  # O(n)
    feats = []  # O(1)
    for i, m in enumerate(heads):  # O(f)
        block = text[m.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]  # O(n)
        block = re.split(r"^#{1,4}\s", block, maxsplit=1, flags=re.M)[0]  # a later non-feature heading ends it | O(n)
        rec = _field(block, "Recommend").lower()  # O(n)
        feats.append({"id": m.group(1), "title": m.group(2), "size": _field(block, "Size")[:1].upper(),  # O(1)
                      "priority": _field(block, "Priority").split()[0].lower() if _field(block, "Priority") else "",
                      "in": not rec.startswith("out"), "recommend": rec.split()[0] if rec else ""})
    return {"title": title.group(1) if title else "", "target": _field(text, "Target").lower(),  # O(1)
            "estimate": parse_estimate(_field(text, "Estimate")), "features": feats}


def parse_estimate(s: str):  # O(len s)
    """`30-45 min, $4-7` -> {min_lo, min_hi, usd_lo, usd_hi}; None when there is nothing to scale."""
    num = r"(\d+(?:\.\d+)?)"  # O(1)
    mins = re.search(num + r"(?:\s*-\s*" + num + r")?\s*min", s)  # O(len s)
    usd = re.search(r"\$\s*" + num + r"(?:\s*-\s*\$?\s*" + num + r")?", s)  # O(len s)
    if not (mins or usd):  # O(1)
        return None  # O(1)
    out = {}  # O(1)
    for key, m in (("min", mins), ("usd", usd)):  # O(1)
        if m:  # O(1)
            out[key + "_lo"] = float(m.group(1))  # O(1)
            out[key + "_hi"] = float(m.group(2) or m.group(1))  # O(1)
    return out  # O(1)


def target_kinds(target) -> list[str]:  # O(1)
    return [k for k in re.split(r"[\s,+/]+", str(target or "").lower()) if k]  # O(1)


def plan_problems(plan: dict) -> list[str]:  # O(f)
    out = []  # O(1)
    kinds = target_kinds(plan["target"])  # O(1)
    if not kinds or any(k not in KINDS for k in kinds):  # O(1)
        out.append(f"plan: **Target:** must be one or more of {', '.join(sorted(KINDS))}, got '{plan['target']}'")  # O(1)
    if not plan["features"]:  # O(1)
        out.append("plan: no features (headings `### F1: Title`)")  # O(1)
    seen = set()  # O(1)
    for f in plan["features"]:  # O(f)
        if f["id"] in seen:  # O(1)
            out.append(f"plan: feature {f['id']} appears twice")  # O(1)
        seen.add(f["id"])  # O(1)
        if f["size"] not in SIZES:  # O(1)
            out.append(f"plan: {f['id']} needs **Size:** S, M or L")  # O(1)
        if f["priority"] not in PRIORITIES:  # O(1)
            out.append(f"plan: {f['id']} needs **Priority:** must, should or could")  # O(1)
        if f["recommend"] not in ("in", "out"):  # O(1)
            out.append(f"plan: {f['id']} needs **Recommend:** in or out, with the reason")  # O(1)
    return out  # O(1)


class _Tags(HTMLParser):  # O(t)
    """Collects data-feature ids and checks that the structural tags open and close in order."""

    def __init__(self):  # O(1)
        super().__init__(convert_charrefs=True)  # O(1)
        self.stack, self.problems, self.features, self.ids = [], [], set(), set()  # O(1)

    def handle_starttag(self, tag, attrs):  # O(attrs)
        a = dict(attrs)  # O(attrs)
        if a.get("data-feature"):  # O(1)
            self.features.update(a["data-feature"].split())  # O(1)
        if a.get("id"):  # O(1)
            self.ids.add(a["id"])  # O(1)
        if tag in BALANCED:  # O(1)
            self.stack.append((tag, self.getpos()[0]))  # O(1)

    def handle_endtag(self, tag):  # O(depth)
        if tag not in BALANCED:  # O(1)
            return  # O(1)
        if not self.stack or self.stack[-1][0] != tag:  # O(1)
            opened = f"<{self.stack[-1][0]}> from line {self.stack[-1][1]}" if self.stack else "nothing"  # O(1)
            self.problems.append(f"page: </{tag}> on line {self.getpos()[0]} closes {opened}")  # O(1)
            while self.stack and self.stack[-1][0] != tag:  # O(depth)
                self.stack.pop()  # O(1)
        if self.stack:  # O(1)
            self.stack.pop()  # O(1)


def page_problems(text: str, plan: dict) -> list[str]:  # O(n)
    out = []  # O(1)
    p = _Tags()  # O(1)
    try:  # O(1)
        p.feed(text)  # O(n)
        p.close()  # O(1)
    except Exception as e:  # html.parser rarely raises; report it rather than crash | O(1)
        return [f"page: does not parse ({e})"]  # O(1)
    out += p.problems + [f"page: <{t}> from line {ln} is never closed" for t, ln in p.stack]  # O(depth)
    for need in ("sl-mock", "sl-panel", "sl-features", "sl-save", "sl-copy", "sl-data"):  # O(1)
        if need not in p.ids:  # O(1)
            out.append(f"page: missing element id={need} (start from skills/mvp-prototype/page-template.html)")  # O(1)
    if stamped_sha(text) is None:  # O(n)
        out.append('page: missing <meta name="sl-prototype-sha">')  # O(1)
    m = DATA.search(text)  # O(n)
    data = None  # O(1)
    if m:  # O(1)
        try:  # O(1)
            data = json.loads(m.group(2))  # O(n)
        except ValueError as e:  # O(1)
            out.append(f"page: the sl-data block is not JSON ({e})")  # O(1)
    plan_ids = [f["id"] for f in plan["features"]]  # O(f)
    if isinstance(data, dict):  # O(1)
        ids = [f.get("id") for f in data.get("features", []) if isinstance(f, dict)]  # O(f)
        if ids != plan_ids:  # O(f)
            out.append(f"page: sl-data features {ids} differ from the plan's {plan_ids}; rebuild the page")  # O(1)
    for fid in plan_ids:  # O(f)
        if fid not in p.features:  # O(1)
            out.append(f'page: feature {fid} is not in the mock (no data-feature="{fid}")')  # O(1)
    for fid in sorted(p.features - set(plan_ids)):  # O(f log f)
        out.append(f"page: data-feature {fid} is not a feature of the plan")  # O(1)
    scan = XMLNS.sub("", text)  # O(n)
    for rx, what in EXTERNAL:  # O(1)
        for hit in rx.finditer(scan):  # O(n)
            line = scan.count("\n", 0, hit.start()) + 1  # O(n)
            out.append(f"page: {what} on line {line} ({hit.group(0).strip()}); the page must work offline")  # O(1)
            break  # one line per kind is enough | O(1)
    return out  # O(1)


def feedback_problems(fb, plan: dict, plan_path: str, sha: str) -> list[str]:  # O(f)
    if not isinstance(fb, dict):  # O(1)
        return ["feedback: not a JSON object"]  # O(1)
    out = []  # O(1)

    def need(key, kind, label):  # O(1)
        if not isinstance(fb.get(key), kind) or (kind is int and isinstance(fb.get(key), bool)):  # O(1)
            out.append(f"feedback: '{key}' must be {label}")  # O(1)
            return False  # O(1)
        return True  # O(1)

    if fb.get("schema") != 1:  # O(1)
        out.append("feedback: 'schema' must be 1")  # O(1)
    if fb.get("source") not in ("human", "ai"):  # O(1)
        out.append("feedback: 'source' must be human or ai")  # O(1)
    if need("plan", str, "the plan path") and Path(fb["plan"]).resolve() != Path(plan_path).resolve():  # O(1)
        out.append(f"feedback: 'plan' is {fb['plan']}, not {plan_path}")  # O(1)
    if need("prototype_sha", str, "a sha256 hex string") and fb["prototype_sha"] != sha:  # O(1)
        out.append("feedback: prototype_sha does not match the page; it was saved from an older page (ask the user to "
                   "save again from the current one)")  # O(1)
    need("approved", bool, "true or false")  # O(1)
    need("keep_as_frontend", bool, "true or false")  # O(1)
    if need("target", str, "the target kind"):  # O(1)
        kinds = target_kinds(fb["target"])  # O(1)
        if not kinds or any(k not in KINDS for k in kinds):  # O(1)
            out.append(f"feedback: 'target' must be one or more of {', '.join(sorted(KINDS))}")  # O(1)
        if fb.get("keep_as_frontend") is True and "web" not in kinds:  # O(1)
            out.append("feedback: keep_as_frontend is only for a web target")  # O(1)
    if need("features", list, "a list"):  # O(1)
        ids, orders = [], []  # O(1)
        for i, f in enumerate(fb["features"]):  # O(f)
            if not (isinstance(f, dict) and isinstance(f.get("id"), str) and isinstance(f.get("title"), str)
                    and isinstance(f.get("in"), bool) and isinstance(f.get("order"), int)
                    and not isinstance(f.get("order"), bool) and isinstance(f.get("note", ""), str)):  # O(1)
                out.append(f"feedback: features[{i}] needs id, title, in (bool), order (int) and note (text)")  # O(1)
                continue  # O(1)
            ids.append(f["id"])  # O(1)
            orders.append(f["order"])  # O(1)
        plan_ids = [f["id"] for f in plan["features"]]  # O(f)
        if len(ids) == len(fb["features"]):  # O(1)
            if sorted(ids) != sorted(plan_ids):  # O(f log f)
                out.append(f"feedback: features {sorted(set(ids) ^ set(plan_ids)) or 'repeat'} do not match the plan")  # O(f)
            if sorted(orders) != list(range(1, len(orders) + 1)):  # O(f log f)
                out.append("feedback: feature orders must be 1..n, each once")  # O(1)
    screens = fb.get("screens") if isinstance(fb.get("screens"), list) else []  # O(1)
    if "screens" in fb and need("screens", list, "a list"):  # O(1)
        for i, s in enumerate(screens):  # O(screens)
            edits = s.get("edits", []) if isinstance(s, dict) else None  # O(1)
            if not (isinstance(s, dict) and isinstance(s.get("id"), str) and isinstance(edits, list) and all(
                    isinstance(e, dict) and isinstance(e.get("label"), str) and isinstance(e.get("to"), str) for e in edits)):
                out.append(f"feedback: screens[{i}] needs id and edits [{{label, to}}]")  # O(1)
    if "added" in fb and need("added", list, "a list"):  # O(1)
        for i, a in enumerate(fb["added"]):  # O(added)
            if not (isinstance(a, dict) and isinstance(a.get("title"), str) and a["title"].strip()):  # O(1)
                out.append(f"feedback: added[{i}] needs a title")  # O(1)
    if "notes" in fb:  # O(1)
        need("notes", str, "text")  # O(1)
    if fb.get("source") == "ai" and (fb.get("added") or any(isinstance(s, dict) and s.get("edits") for s in screens)):  # O(screens)
        out.append("feedback: source ai carries no edits or added features")  # O(1)
    return out  # O(1)


def _read(path: str, what: str) -> str:  # O(n)
    p = Path(path)  # O(1)
    if not p.is_file():  # O(1)
        print(f"mvp_check: {what} {path} not found", file=sys.stderr)  # O(1)
        raise SystemExit(2)  # O(1)
    return p.read_text(encoding="utf-8")  # O(n)


def _write(path: str, text: str) -> None:  # O(n)
    Path(path).parent.mkdir(parents=True, exist_ok=True)  # O(1)
    Path(path).write_text(text, encoding="utf-8", newline="\n")  # O(n)


def stamp(text: str) -> str:  # O(n)
    return META.sub(lambda m: m.group(1) + page_sha(text) + m.group(2), text)  # O(n)


def build(plan_path: str, plan: dict, mock: str, template: str) -> str:  # O(n)
    data = {"plan": plan_path.replace("\\", "/"), "title": plan["title"], "target": plan["target"],
            "estimate": plan["estimate"],
            "features": [{k: f[k] for k in ("id", "title", "size", "priority", "in")} for f in plan["features"]]}  # O(f)
    blob = json.dumps(data, indent=1).replace("</", "<\\/")  # never closes the script tag early | O(n)
    page = template.replace("<!--SL:MOCK-->", mock.strip(), 1).replace("/*SL:DATA*/", blob, 1)  # O(n)
    return stamp(page)  # O(n)


def ai_feedback(plan_path: str, plan: dict, sha: str) -> dict:  # O(f)
    return {"schema": 1, "source": "ai", "plan": plan_path.replace("\\", "/"), "prototype_sha": sha, "approved": True,
            "target": plan["target"], "keep_as_frontend": False,
            "features": [{"id": f["id"], "title": f["title"], "in": f["in"], "order": i + 1, "note": ""}
                         for i, f in enumerate(plan["features"])],
            "screens": [], "added": [], "notes": ""}  # O(f)


def fetch(feedback: str, page: str, downloads: str) -> str | None:  # O(files in downloads)
    """The project feedback file, else the newest mvp-feedback*.json in Downloads newer than the page, copied in."""
    dest = Path(feedback)  # O(1)
    since = Path(page).stat().st_mtime if Path(page).is_file() else 0  # O(1)
    cands = [p for p in Path(downloads).glob("mvp-feedback*.json") if p.is_file() and p.stat().st_mtime >= since]  # O(d)
    newest = max(cands, key=lambda p: p.stat().st_mtime, default=None)  # O(d)
    if dest.is_file() and (newest is None or dest.stat().st_mtime >= newest.stat().st_mtime):  # O(1)
        return str(dest)  # O(1)
    if newest is None:  # O(1)
        return None  # O(1)
    dest.parent.mkdir(parents=True, exist_ok=True)  # O(1)
    shutil.copyfile(newest, dest)  # O(size)
    return str(dest)  # O(1)


def open_page(page: str) -> str:  # O(1)
    """Opens the page in the user's default browser (file URI); the message says what happened."""
    uri = Path(page).resolve().as_uri()  # O(1)
    try:  # O(1)
        ok = webbrowser.open(uri)  # O(1) hands off to the OS browser
    except Exception:  # no browser on this machine | O(1)
        ok = False  # O(1)
    return f"opened {uri}" if ok else f"could not open a browser; ask the user to open {uri}"  # O(1)


def report(problems: list[str], ok: str) -> int:  # O(problems)
    if problems:  # O(1)
        print(f"mvp_check: {len(problems)} problem(s)")  # O(1)
        print("\n".join(problems))  # O(problems)
        return 1  # O(1)
    print(f"mvp_check: ok, {ok}")  # O(1)
    return 0  # O(1)


def main(argv=None) -> int:  # O(n)
    ap = argparse.ArgumentParser(description=__doc__.split(chr(10))[0])  # O(1)
    sub = ap.add_subparsers(dest="cmd", required=True)  # O(1)
    for name in ("build", "stamp", "check", "ai", "fetch", "open"):  # O(1)
        s = sub.add_parser(name)  # O(1)
        s.add_argument("--page", default=None, help=f"default: active session mvp page or {PAGE}")  # O(1)
        if name in ("build", "check", "ai"):  # O(1)
            s.add_argument("--plan", required=True)  # O(1)
        if name in ("check", "ai", "fetch"):  # O(1)
            s.add_argument("--feedback", default=None, help=f"default: active session feedback or {FEEDBACK}")  # O(1)
        if name == "build":  # O(1)
            s.add_argument("--mock", required=True, help="the mock's HTML fragment (the main area)")  # O(1)
            s.add_argument("--template", default=str(TEMPLATE))  # O(1)
        if name == "check":  # O(1)
            s.add_argument("--require-feedback", action="store_true")  # O(1)
        if name == "fetch":  # O(1)
            s.add_argument("--downloads", default=str(Path.home() / "Downloads"))  # O(1)
    a = ap.parse_args(argv)  # O(1)
    if getattr(a, "page", None) is None:  # O(1)
        a.page = default_page()  # O(S)
    if hasattr(a, "feedback") and a.feedback is None:  # O(1)
        a.feedback = default_feedback()  # O(S)

    if a.cmd == "fetch":  # O(1)
        got = fetch(a.feedback, a.page, a.downloads)  # O(d)
        if got is None:  # O(1)
            print("mvp_check: no mvp-feedback.json in the project or Downloads; ask the user to press Save feedback "
                  "or paste the Copy feedback block")  # O(1)
            return 1  # O(1)
        print(got)  # O(1)
        return 0  # O(1)
    if a.cmd == "open":  # O(1)
        _read(a.page, "page")  # exit 2 when missing | O(n)
        print(f"mvp_check: {open_page(a.page)}")  # O(1)
        return 0  # O(1)
    if a.cmd == "stamp":  # O(1)
        text = stamp(_read(a.page, "page"))  # O(n)
        _write(a.page, text)  # O(n)
        print(stamped_sha(text))  # O(n)
        return 0  # O(1)

    plan = parse_plan(_read(a.plan, "plan"))  # O(n)
    problems = plan_problems(plan)  # O(f)
    if a.cmd == "build":  # O(1)
        if problems:  # O(1)
            return report(problems, "")  # O(1)
        page = build(a.plan, plan, _read(a.mock, "mock"), _read(a.template, "template"))  # O(n)
        _write(a.page, page)  # O(n)
        return report(page_problems(page, plan), f"wrote {a.page} ({len(plan['features'])} features)")  # O(n)

    text = _read(a.page, "page")  # O(n)
    sha = page_sha(text)  # O(n)
    problems += page_problems(text, plan)  # O(n)
    if stamped_sha(text) not in (None, sha):  # O(n)
        problems.append("page: changed since it was stamped; run sl mvp_check stamp")  # O(1)
    if a.cmd == "ai":  # O(1)
        if problems:  # O(1)
            return report(problems, "")  # O(1)
        _write(a.feedback, json.dumps(ai_feedback(a.plan, plan, sha), indent=2) + "\n")  # O(f)
        return report([], f"wrote {a.feedback} (source ai)")  # O(1)
    fb_path = Path(a.feedback)  # O(1)
    if fb_path.is_file():  # O(1)
        try:  # O(1)
            fb = json.loads(fb_path.read_text(encoding="utf-8"))  # O(size)
        except ValueError as e:  # O(1)
            fb, problems = None, problems + [f"feedback: not JSON ({e})"]  # O(1)
        if fb is not None:  # O(1)
            problems += feedback_problems(fb, plan, a.plan, sha)  # O(f)
    elif a.require_feedback:  # O(1)
        problems.append(f"feedback: {a.feedback} not found (run sl mvp_check fetch)")  # O(1)
    kept = sum(1 for f in plan["features"] if f["in"])  # O(f)
    return report(problems, f"{len(plan['features'])} features ({kept} recommended in), page sha {sha[:12]}")  # O(1)


if __name__ == "__main__":  # O(1)
    sys.exit(main())  # O(1)
