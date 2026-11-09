#!/usr/bin/env python3
"""Level-free research evidence ledger.

Subcommands: init, source, claim, contradiction, lint, render, downloads,
download. Ledger lives at <root>/<run folder>/research/<slug>/ledger.json with
keys topic, tiers, sources, claims, contradictions. No audience wording is
stored. Stdlib only.
"""
import argparse
import json
import sys
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402
import research_render  # noqa: E402
import state  # noqa: E402

STATUSES = ("resolved", "open")
PARTS = ("report", "sources", "contradictions")


def ledger_path(args):
    if getattr(args, "ledger", None):
        return Path(args.ledger)
    if not args.slug:
        raise SystemExit("research: --slug or --ledger required")
    return Path(args.root) / state.DIR_NAME / "research" / args.slug / "ledger.json"


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class Fail(Exception):
    """A refused mutation: printed as `research: <msg>`, exit 1, nothing written."""


def ensure_ids(data):
    """Give every contradiction without an id the next free K<n> (in order). Returns data."""
    items = data.setdefault("contradictions", [])
    used = {k.get("id") for k in items if k.get("id")}
    n = 0
    for k in items:
        if k.get("id"):
            continue
        n += 1
        while f"K{n}" in used:
            n += 1
        k["id"] = f"K{n}"
        used.add(k["id"])
    return data


def lint_ledger(data):
    """Return a list of problem strings; empty means clean."""
    problems = []
    sources = data.get("sources", [])
    claims = data.get("claims", [])
    tiers = data.get("tiers") or []
    source_ids = {s.get("id") for s in sources}
    claim_ids = {c.get("id") for c in claims}
    for s in sources:
        for key in ("id", "url", "date", "grade"):
            if not s.get(key):
                problems.append(f"source {s.get('id', '?')}: missing {key}")
        if tiers and s.get("grade") and s["grade"] not in tiers:
            problems.append(f"source {s.get('id', '?')}: grade {s['grade']!r} not in tiers")
    for c in claims:
        cid = c.get("id", "?")
        if not c.get("text"):
            problems.append(f"claim {cid}: missing text")
        srcs = c.get("sources") or []
        if not srcs:
            problems.append(f"claim {cid}: no source")
        for sid in srcs:
            if sid not in source_ids:
                problems.append(f"claim {cid}: unknown source id {sid}")
    seen = Counter(k.get("id") for k in data.get("contradictions", []) if k.get("id"))
    for i, k in enumerate(data.get("contradictions", []), 1):
        label = f"contradiction {k.get('id') or '#' + str(i)}"
        if k.get("id") and seen[k["id"]] > 1:
            problems.append(f"{label}: duplicate id")
        if not (k.get("cause") or "").strip():
            problems.append(f"{label}: missing cause")
        if k.get("status") not in STATUSES:
            problems.append(f"{label}: bad status {k.get('status')!r}")
        for cid in k.get("claims") or []:
            if cid not in claim_ids:
                problems.append(f"{label}: unknown claim id {cid}")
        if len(k.get("claims") or []) < 2:
            problems.append(f"{label}: needs at least two claims")
    return problems


def _need(args):
    path = ledger_path(args)
    if not path.is_file():
        print(f"research: no ledger at {path}", file=sys.stderr)
        return path, None
    return path, load(path)


def _mutate(args, fn, create=False):
    """Run fn(data) on the ledger under base.locked_update: read, check, change and atomic write in one lock."""
    path = ledger_path(args)
    if not create and not path.is_file():
        print(f"research: no ledger at {path}", file=sys.stderr)
        return 1

    def apply(data):
        if create and path.exists():
            raise Fail(f"ledger exists at {path}")
        if not create and not data:
            raise Fail(f"cannot read ledger at {path}")
        return fn(data) or data

    try:
        base.locked_update(path, apply)
    except Fail as e:
        print(f"research: {e}", file=sys.stderr)
        return 1
    return 0


def _add(args, key, entry, check=None):
    def fn(data):
        items = data.setdefault(key, [])
        if "id" in entry and any(e.get("id") == entry["id"] for e in items):
            raise Fail(f"duplicate id {entry['id']}")
        err = check(data) if check else None
        if err:
            raise Fail(err)
        items.append(entry)

    return _mutate(args, fn)


def cmd_init(args):
    tiers = [t.strip() for t in (args.tiers or "").split(",") if t.strip()]
    ok = _mutate(args, lambda _: {"topic": args.topic, "tiers": tiers, "sources": [],
                                  "claims": [], "contradictions": []}, create=True)
    if ok == 0:
        print(ledger_path(args))
    return ok


def cmd_source(args):
    entry = {"id": args.id, "url": args.url, "date": args.date,
             "grade": args.grade, "kind": args.kind}
    if args.file_url:
        entry["file_url"] = args.file_url
    if args.size:
        entry["size"] = args.size

    def check(data):
        tiers = data.get("tiers") or []
        if not args.grade.strip():
            return "grade must be a non-empty string"
        if tiers and args.grade not in tiers:
            return f"grade {args.grade!r} not in tiers {','.join(tiers)}"
        return None

    return _add(args, "sources", entry, check)


def cmd_claim(args):
    return _add(args, "claims", {"id": args.id, "text": args.text,
                                 "sources": args.source or []})


def _contradict(args, data):
    items = ensure_ids(data)["contradictions"]
    if not args.id:
        ids = {k["id"] for k in items}
        n = len(items) + 1
        while f"K{n}" in ids:
            n += 1
        items.append({"id": f"K{n}", "claims": args.claim or [], "cause": args.cause or "",
                      "status": args.status or "open"})
        return
    same = [k for k in items if k["id"] == args.id]
    if len(same) > 1:
        raise Fail(f"duplicate id {args.id}")
    if not same:
        items.append({"id": args.id, "claims": args.claim or [], "cause": args.cause or "",
                      "status": args.status or "open"})
        return
    old = dict(same[0])
    if args.claim:
        same[0]["claims"] = args.claim
    if args.cause is not None:
        same[0]["cause"] = args.cause
    if args.status:
        same[0]["status"] = args.status
    if same[0] == old:
        raise Fail(f"duplicate id {args.id}")


def cmd_contradiction(args):
    return _mutate(args, lambda data: _contradict(args, data))


def cmd_lint(args):
    path = ledger_path(args)
    try:
        data = load(path)
        problems = lint_ledger(data)
        if args.parity:
            texts = {str(Path(f).resolve()): Path(f).read_text(encoding="utf-8")
                     for f in args.parity}
            problems += research_render.parity(texts, data)
    except (OSError, ValueError) as e:
        print(f"research: cannot read {path}: {e}", file=sys.stderr)
        return 1
    for p in problems:
        print(p)
    if problems:
        return 1
    print("ok")
    return 0


def cmd_render(args):
    path, data = _need(args)
    if data is None:
        return 1
    try:
        if args.part == "sources":
            out = research_render.render_sources(data)
        elif args.part == "contradictions":
            out = research_render.render_contradictions(data)
        elif not args.level:
            raise ValueError("--level required for --part report")
        else:
            out = research_render.render(data, args.level)
    except ValueError as e:
        print(f"research: {e}", file=sys.stderr)
        return 1
    sys.stdout.write(out)
    return 0


MAX_BYTES = 200 * 1024 * 1024
SCHEMES = ("http", "https")


def _safe_name(file_url, sid):
    name = Path(urllib.parse.urlparse(file_url).path).name
    if name in ("", ".", "..") or "\\" in name or "/" in name:
        name = ""
    if not name:
        name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(sid)) or "file"
    return name


def _downloads(data):
    out = []
    for s in data.get("sources", []):
        if s.get("file_url"):
            out.append((_safe_name(s["file_url"], s.get("id", "")), s, s["file_url"]))
    return out


def cmd_downloads(args):
    path, data = _need(args)
    if data is None:
        return 1
    for name, s, _ in _downloads(data):
        print(f"{name}\t{s['id']}\t{s.get('size', '')}")
    return 0


def fetch(url, dest):
    """Fetch url to dest. Only called by download --yes."""
    if urllib.parse.urlparse(url).scheme.lower() not in SCHEMES:
        raise ValueError(f"unsupported scheme in {url}")
    with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310
        body = r.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError(f"{url} exceeds {MAX_BYTES} bytes")
    Path(dest).write_bytes(body)


def cmd_download(args):
    if not args.yes:
        print("research: download needs --yes; run downloads to list files",
              file=sys.stderr)
        return 1
    path, data = _need(args)
    if data is None:
        return 1
    items = _downloads(data)
    names = [n for n, _, _ in items]
    if len(set(names)) != len(names):
        print("research: duplicate output file names", file=sys.stderr)
        return 1
    for _, _, url in items:
        if urllib.parse.urlparse(url).scheme.lower() not in SCHEMES:
            print(f"research: unsupported scheme in {url}", file=sys.stderr)
            return 1
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, s, url in items:
        try:
            fetch(url, out / name)
        except (OSError, ValueError) as e:
            print(f"research: {name}: {e}", file=sys.stderr)
            return 1
        print(f"{name}\t{s['id']}\t{out / name}")
    return 0


def build_parser():
    ap = argparse.ArgumentParser(prog="research")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn):
        p = sub.add_parser(name)
        p.add_argument("--slug")
        p.add_argument("--root", default=".")
        p.add_argument("--ledger")
        p.set_defaults(fn=fn)
        return p

    p = add("init", cmd_init)
    p.add_argument("--topic", default="")
    p.add_argument("--tiers", default="", help="comma list, e.g. A,B,C")
    p = add("source", cmd_source)
    p.add_argument("--id", required=True)
    p.add_argument("--url", required=True)
    p.add_argument("--date", required=True)
    p.add_argument("--grade", required=True)
    p.add_argument("--kind", default="")
    p.add_argument("--file-url", dest="file_url")
    p.add_argument("--size")
    p = add("claim", cmd_claim)
    p.add_argument("--id", required=True)
    p.add_argument("--text", required=True)
    p.add_argument("--source", action="append")
    p = add("contradiction", cmd_contradiction)
    p.add_argument("--id", help="K1, K2... auto-assigned; an existing id updates that record")
    p.add_argument("--claim", action="append")
    p.add_argument("--cause")
    p.add_argument("--status", choices=STATUSES)
    p = add("lint", cmd_lint)
    p.add_argument("--parity", nargs="+", metavar="FILE")
    p = add("render", cmd_render)
    p.add_argument("--level")
    p.add_argument("--part", choices=PARTS, default="report")
    add("downloads", cmd_downloads)
    p = add("download", cmd_download)
    p.add_argument("--yes", action="store_true")
    p.add_argument("--out", default="downloads")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
