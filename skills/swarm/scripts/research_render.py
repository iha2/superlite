"""Render a level-free research ledger at four audience levels, and check parity.

Ledger JSON: topic, tiers (list), sources (id, url, date, grade, kind, file_url, size), claims (id, text, sources),
contradictions (claims, cause, status resolved|open). Every level prints one parseable line per record, in a stable
form: `- [claim-id] text (sources: s1, s2)`, `- [source-id] {json of url, date, grade, kind, file_url, size}` and
`- [status:claim-ids] cause (word)`. Only headings and the status word differ by level. parity() parses those lines and
compares them record by record, keyed by id (a contradiction by status, claim ids and cause). Ids are any non-empty
string without `]`. Stdlib only.
"""
import json
import re
from collections import Counter

LEVELS = ("director", "vp", "researcher", "simple")
HEADS = {
    "director": ("Bottom line", "Evidence", "Conflicts", "Source", "Source tiers"),
    "vp": ("Summary for decision", "Findings", "Disagreements", "Backed by", "Tiers used"),
    "researcher": ("Ledger", "Claims", "Contradictions", "Sources", "Tiers"),
    "simple": ("In short", "What we found", "Things that do not match", "Where it comes from", "Kinds of source"),
}
STATUS_WORD = {
    "director": {"open": "unresolved", "resolved": "settled"},
    "vp": {"open": "still open", "resolved": "resolved"},
    "researcher": {"open": "open", "resolved": "resolved"},
    "simple": {"open": "not yet reconciled", "resolved": "now explained"},
}
SRC_FIELDS = ("url", "date", "grade", "kind", "file_url", "size")
CLAIM = re.compile(r"^- \[([^\]]+)\] (.*) \(sources: (.*)\)$")
SOURCE = re.compile(r"^- \[([^\]]+)\] (\{.*\})$")
CONTRA = re.compile(r"^- \[(open|resolved):([^\]]*)\] (.*) \([^()]*\)$")
SECTIONS = {f"{h[i]}:": kind for h in HEADS.values() for i, kind in ((1, "claim"), (2, "contradiction"), (3, "source"))}


def _s(v):
    """A value as one line of text: None is empty, runs of whitespace collapse to one space."""
    return " ".join(("" if v is None else str(v)).split())


def _id(v):
    v = _s(v)
    if not v or "]" in v:
        raise ValueError(f"bad id (empty or holds ]): {v!r}")
    return v


def _contradictions(ledger):
    return [x for x in ledger.get("contradictions", []) if x.get("status") in ("open", "resolved")]


def _claim_line(c):
    return f"- [{_id(c['id'])}] {_s(c['text'])} (sources: {', '.join(_s(x) for x in c.get('sources', []))})"


def _source_line(s):
    payload = {k: _s(s.get(k)) for k in SRC_FIELDS}
    return f"- [{_id(s['id'])}] {json.dumps(payload, ensure_ascii=False)}"


def _contra_line(x, word):
    return f"- [{x['status']}:{','.join(_s(c) for c in x['claims'])}] {_s(x['cause'])} ({word})"


def _lines(items, line):
    return [line(i) for i in items] or ["- none"]


def render_sources(ledger):
    """Every source record, one line each, in the stable record form (level-free)."""
    return "\n".join([f"{HEADS['researcher'][3]}:", *_lines(ledger.get("sources", []), _source_line)]) + "\n"


def render_contradictions(ledger):
    """Every contradiction record, resolved and open, with cause and status (level-free)."""
    words = STATUS_WORD["researcher"]
    lines = _lines(_contradictions(ledger), lambda x: _contra_line(x, words[x["status"]]))
    return "\n".join([f"{HEADS['researcher'][2]}:", *lines]) + "\n"


def render(ledger, level):
    if level not in LEVELS:
        raise ValueError(f"unknown level: {level}")
    head, claims_h, contra_h, src_h, tier_h = HEADS[level]
    words = STATUS_WORD[level]
    out = [f"{head}: {_s(ledger.get('topic'))}", ""]
    tiers = ledger.get("tiers") or []
    if tiers:
        out += [f"{tier_h}: " + ", ".join(_s(t) for t in tiers), ""]
    out += [f"{claims_h}:", *_lines(ledger.get("claims", []), _claim_line), ""]
    out += [f"{src_h}:", *_lines(ledger.get("sources", []), _source_line), ""]
    out += [f"{contra_h}:", *_lines(_contradictions(ledger), lambda x: _contra_line(x, words[x["status"]]))]
    return "\n".join(out) + "\n"


def _record(kind, line):
    """(key, value) for one record line of the given section kind, or None when it is not a record."""
    if kind == "claim":
        m = CLAIM.match(line)
        return m and (("claim", m[1]), (m[2], tuple(m[3].split(", ")) if m[3] else ()))
    if kind == "source":
        m = SOURCE.match(line)
        return m and (("source", m[1]), _source_value(m[2]))
    m = CONTRA.match(line)
    return m and (("contradiction", (m[1], tuple(m[2].split(",")), m[3])), ())


def _source_value(raw):
    try:
        d = json.loads(raw)
    except ValueError:
        return ("unparseable", raw)
    return tuple(_s(d.get(k)) for k in SRC_FIELDS) if isinstance(d, dict) else ("unparseable", raw)


def _text_records(text):
    """{key: [values]} for every record line of a render, found by section heading."""
    out, kind = {}, None
    for line in text.splitlines():
        kind = SECTIONS.get(line.strip(), kind)
        rec = _record(kind, line.rstrip()) if kind else None
        if rec:
            out.setdefault(rec[0], []).append(rec[1])
    return out


def _consensus(all_records):
    """The records at least half the texts hold, each with the value list most of those texts give it."""
    seen = {}
    for recs in all_records.values():
        for key, vals in recs.items():
            seen.setdefault(key, []).append(tuple(sorted(vals, key=str)))
    half = len(all_records) / 2
    return {key: list(Counter(v).most_common(1)[0][0]) for key, v in seen.items() if len(v) >= half}


def _label(key):
    kind, k = key
    return f"{kind} [{k[0]}:{','.join(k[1])}] {k[2]}" if kind == "contradiction" else f"{kind} {k}"


def _problem(name, key, want, have):
    w, h = Counter(want or []), Counter(have or [])
    if w == h:
        return None
    if not h:
        return f"{name}: missing {_label(key)}"
    if not w:
        return f"{name}: extra {_label(key)}"
    return f"{name}: differs {_label(key)}: expected {sorted(w.elements(), key=str)}, got {sorted(h.elements(), key=str)}"


def parity(texts, ledger=None):
    """texts: dict level->text (or list of texts). Returns mismatch strings.

    Parses the record lines of each text and compares them per record. With a ledger the records the ledger renders
    are the truth: a missing record, a record whose values differ, and an extra record are each flagged. Without one
    each text is compared with the records most texts agree on."""
    items = list(texts.items()) if isinstance(texts, dict) else [(f"#{i}", t) for i, t in enumerate(texts)]
    records = {name: _text_records(t) for name, t in items}
    want = _text_records(render(ledger, "researcher")) if ledger is not None else _consensus(records)
    out = []
    for name, recs in records.items():
        keys = sorted(set(want) | set(recs), key=str)
        out += [p for p in (_problem(name, k, want.get(k), recs.get(k)) for k in keys) if p]
    return out
