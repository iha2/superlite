"""Render a level-free research ledger at four audience levels, and check parity.

Ledger JSON: topic, tiers (list), sources (id, url, date, grade, kind, file_url, size), claims (id, text, sources),
contradictions (claims, cause, status resolved|open). Every level prints one parseable line per record, in a stable
form: `- [claim-id] text (sources: s1, s2)`, `- [source-id] {json of url, date, grade, kind, file_url, size}` and
`- [status:claim-ids] cause (word)`. Those record lines are the payload and are identical in truth and depth at every
level; only headings, the status word and the prose blocks around them differ. The prose (director: Bottom line and
Decision risk; vp: What this means and Confidence; researcher: Method and evidence quality; simple: In short and
plain-language notes) is a template filled from ledger fields and counts of ledger items, never from topic words, and
no prose line starts with `- [`. parity() parses only the record lines of the record sections and compares them record
by record, keyed by id (a contradiction by status, claim ids and cause), so prose never affects it. Ids are any
non-empty string without `]`. Stdlib only.
"""
import json
import re
from collections import Counter

LEVELS = ("director", "vp", "researcher", "simple")
HEADS = {
    "director": ("Briefing", "Evidence", "Conflicts", "Source", "Source tiers"),
    "vp": ("Brief", "Findings", "Disagreements", "Backed by", "Tiers used"),
    "researcher": ("Ledger", "Claims", "Contradictions", "Sources", "Tiers"),
    "simple": ("Topic", "What we found", "Things that do not match", "Where it comes from", "Kinds of source"),
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


def _n(count, word):
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _list(items):
    return ", ".join(items) or "none"


def _ref(src):
    """`S1 (kind, grade, date)` from the non-empty source fields."""
    parts = [_s(src.get(k)) for k in ("kind", "grade", "date")]
    return f"{_id(src['id'])} ({', '.join(p for p in parts if p)})"


def _facts(ledger):
    """Everything the prose may say, taken from the ledger: counts, ids, tiers and source fields."""
    tiers = [_s(t) for t in ledger.get("tiers") or []]
    sources, claims = ledger.get("sources", []), ledger.get("claims", [])
    rank = lambda s: tiers.index(_s(s.get("grade"))) if _s(s.get("grade")) in tiers else len(tiers)  # noqa: E731
    best = min((rank(s) for s in sources), default=0)
    lead = max((s for s in sources if rank(s) == best), key=lambda s: _s(s.get("date")), default=None)
    cites = {_id(c["id"]): sorted({_s(x) for x in c.get("sources", [])}) for c in claims}
    dates = sorted(_s(s.get("date")) for s in sources if _s(s.get("date")))
    cons = _contradictions(ledger)
    return {
        "tiers": tiers, "sources": sources, "claims": claims, "lead": lead, "dates": dates,
        "single": {k: v for k, v in cites.items() if len(v) == 1},
        "multi": [k for k, v in cites.items() if len(v) > 1],
        "open": [x for x in cons if x["status"] == "open"],
        "resolved": [x for x in cons if x["status"] == "resolved"],
        "top": sum(1 for s in sources if tiers and rank(s) == 0),
    }


def _open_ids(f):
    seen = []
    for x in f["open"]:
        seen += [c for c in (_s(c) for c in x["claims"]) if c not in seen]
    return seen


def _counts(f):
    return f"{_n(len(f['claims']), 'claim')} from {_n(len(f['sources']), 'source')}"


def _director(f):
    lead = f["lead"]
    how = "the most recent of the highest-ranked grade" if f["tiers"] else "the most recent"
    top = f"Lead source: {_ref(lead)}, {how}." if lead else "No sources are recorded."
    ids = _open_ids(f)
    risk = f"claims {_list(ids)} are not yet reconciled." if ids else "nothing is recorded as unresolved."
    return ["Bottom line:", f"The evidence holds {_counts(f)}.", top,
            f"Open conflicts: {len(f['open'])}" + (f", involving claims {_list(ids)}." if ids else "."),
            f"Decision risk: {risk}"]


def _confidence(f):
    why = []
    if f["open"]:
        why.append(f"{_n(len(f['open']), 'conflict')} still open")
    if f["single"]:
        why.append(f"{_n(len(f['single']), 'claim')} resting on one source")
    if not f["sources"]:
        why.append("no sources recorded")
    label = "limited" if f["open"] else "moderate" if why or not f["sources"] else "high"
    out = f"Confidence: {label}; " + ("; ".join(why) if why else "no conflict is open and every claim has several sources")
    grade = f" Top-grade sources ({f['tiers'][0]}): {f['top']} of {len(f['sources'])}." if f["top"] else ""
    when = f" The newest source is dated {f['dates'][-1]}." if f["dates"] else ""
    return out + "." + grade + when


def _vp(f):
    state = f"{_n(len(f['open']), 'conflict')} still open and {len(f['resolved'])} resolved"
    return ["What this means:", f"{_counts(f)}; {state}.",
            f"Claims backed by one source: {_list(sorted(f['single']))}.",
            f"Claims backed by several sources: {_list(f['multi'])}.", _confidence(f)]


def _researcher(f):
    rows = [f"  {_ref(s)}" for s in f["sources"]] or ["  none"]
    one = [f"{k} ({', '.join(v)})" for k, v in f["single"].items()]
    causes = [f"  {x['status']} {','.join(_s(c) for c in x['claims'])}: {_s(x['cause'])}" for x in f["open"] + f["resolved"]]
    return ["Method and evidence quality:", ("Tiers, highest first: " + " > ".join(f["tiers"]) if f["tiers"] else
             "No evidence-tier ordering has been set, so grades are shown as recorded and are not ranked against each other."),
            "Sources, id (kind, grade, date):", *rows, f"Claims resting on a single source: {_list(one)}.",
            "Contradiction causes as recorded:", *(causes or ["  none"])]


def _simple(f):
    tiers = ", ".join(f["tiers"])
    grade = f"A grade says how much weight a source gets; from most to least weight: {tiers}." if tiers else \
        "No order of grades has been set, so the grades are shown as recorded and none counts for more than another."
    ids = _open_ids(f)
    short = f"In short: {_counts(f)}; " + (f"{_n(len(f['open']), 'disagreement')} not yet settled." if ids else "no disagreement is left.")
    gap = ['"Sources disagree" means two statements about the same thing do not match.']
    for x in f["open"]:
        gap.append(f"Claims {' and '.join(_s(c) for c in x['claims'])} do not match yet. The recorded reason: {_s(x['cause'])}")
    return [short, grade, *gap]


PROSE = {"director": _director, "vp": _vp, "researcher": _researcher, "simple": _simple}


def render(ledger, level):
    if level not in LEVELS:
        raise ValueError(f"unknown level: {level}")
    head, claims_h, contra_h, src_h, tier_h = HEADS[level]
    words = STATUS_WORD[level]
    out = [f"{head}: {_s(ledger.get('topic'))}", ""]
    tiers = ledger.get("tiers") or []
    if tiers:
        out += [f"{tier_h}: " + ", ".join(_s(t) for t in tiers), ""]
    out += [*PROSE[level](_facts(ledger)), ""]
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
    """{key: [values]} for every record line of a render, found by section heading.

    Only the three record sections count: any other heading line (a prose block) ends the section before it."""
    out, kind = {}, None
    for line in text.splitlines():
        s = line.strip()
        kind = SECTIONS[s] if s in SECTIONS else None if s.endswith(":") and not s.startswith("- ") else kind
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
