"""Render a level-free research ledger at four audience levels, and check parity.

Ledger JSON: topic, tiers (list), sources (id, url, date, grade, kind, file_url, size), claims (id, text, sources),
contradictions (claims, cause, status resolved|open). Every level shows the same claim ids, citations, numbers and
contradictions with their causes; only the headings and wording differ. Stdlib only.
"""
import re

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
NUM = re.compile(r"(?<![\w.])\d+(?:,\d{3})*(?:\.\d+)?")
ID = re.compile(r"\b[CS]\d+\b")
CONTRA = re.compile(r"^- \[(open|resolved):([^\]]*)\] (.*)$", re.MULTILINE)


def _s(v):
    return "" if v is None else str(v)


def _contradictions(ledger):
    return [x for x in ledger.get("contradictions", []) if x.get("status") in ("open", "resolved")]


def render(ledger, level):
    if level not in LEVELS:
        raise ValueError(f"unknown level: {level}")
    head, claims_h, contra_h, src_h, tier_h = HEADS[level]
    words = STATUS_WORD[level]
    src = ledger.get("sources", [])
    out = [f"{head}: {_s(ledger.get('topic'))}", ""]
    tiers = ledger.get("tiers") or []
    if tiers:
        out += [f"{tier_h}: " + ", ".join(_s(t) for t in tiers), ""]
    out.append(f"{claims_h}:")
    for c in ledger.get("claims", []):
        out.append(f"- [{c['id']}] {c['text']} ({src_h} {', '.join(c.get('sources', []))})")
    out += ["", f"{src_h}:"]
    for s in src:
        extra = "".join(f", {k} {_s(s.get(k))}" for k in ("file_url", "size") if s.get(k) not in (None, ""))
        if level == "researcher":
            out.append(f"- {s['id']} {_s(s.get('url'))} {_s(s.get('date'))} grade {_s(s.get('grade'))} "
                       f"{_s(s.get('kind'))}{extra}")
        else:
            out.append(f"- {s['id']}: {_s(s.get('url'))} ({_s(s.get('date'))}, grade {_s(s.get('grade'))}, "
                       f"{_s(s.get('kind'))}{extra})")
    out += ["", f"{contra_h}:"]
    xs = _contradictions(ledger)
    for x in xs:
        out.append(f"- [{x['status']}:{','.join(x['claims'])}] {x['cause']} ({words[x['status']]})")
    if not xs:
        out.append("- none")
    return "\n".join(out) + "\n"


def _contra_facts(text):
    return {(st, tuple(ids.replace(" ", "").split(",")), re.sub(r"\s*\((?:[^()]*)\)$", "", cause).strip())
            for st, ids, cause in CONTRA.findall(text)}


def _facts(text):
    return {"ids": set(ID.findall(text)), "numbers": set(NUM.findall(text)), "contradictions": _contra_facts(text)}


def _ledger_facts(ledger):
    fields = [_s(ledger.get("topic"))] + [_s(t) for t in ledger.get("tiers") or []]
    fields += [c["text"] for c in ledger.get("claims", [])]
    for s in ledger.get("sources", []):
        fields += [_s(s.get(k)) for k in ("url", "date", "grade", "kind", "file_url", "size")]
    ids = {c["id"] for c in ledger.get("claims", [])} | {s["id"] for s in ledger.get("sources", [])}
    contra = {(x["status"], tuple(x["claims"]), x["cause"].strip()) for x in _contradictions(ledger)}
    return {"ids": ids, "numbers": set(NUM.findall("\n".join(fields))), "contradictions": contra}


def parity(texts, ledger=None):
    """texts: dict level->text (or list of texts). Returns mismatch strings.

    Each text must carry every id, number and contradiction (status, claims, cause) found in any other text; with a
    ledger it must also carry every one the ledger holds, so an item dropped from all levels is flagged too."""
    items = list(texts.items()) if isinstance(texts, dict) else [(f"#{i}", t) for i, t in enumerate(texts)]
    facts = {k: _facts(t) for k, t in items}
    names = (("ids", "claim/source id"), ("numbers", "number"), ("contradictions", "contradiction"))
    want = {n: set().union(*(v[n] for v in facts.values())) if facts else set() for n, _ in names}
    if ledger is not None:
        for n, v in _ledger_facts(ledger).items():
            want[n] |= v
    out = []
    for k, f in facts.items():
        out.extend(_missing(k, f, want, names))
    return out


def _fmt_missing(name, m):
    if name != "contradictions":
        return m
    st, ids, cause = m
    return f"[{st}:{','.join(ids)}] {cause}"


def _missing(k, f, want, names):
    return [
        f"{k}: missing {label} {_fmt_missing(name, m)}"
        for name, label in names
        for m in sorted(want[name] - f[name], key=str)
    ]
