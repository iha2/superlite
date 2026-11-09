"""research_render: four levels of one level-free ledger, and the parity check against it."""
import copy
import re
import sys

import pytest

from test_plan_tools import SCRIPTS

sys.path.insert(0, str(SCRIPTS))
import research_render as rr  # noqa: E402

LEDGER = {
    "topic": "battery storage cost trends",
    "tiers": ["primary data", "peer-reviewed", "trade press"],
    "sources": [
        {"id": "S1", "url": "https://example.org/papers/storage-cost", "date": "2026-09-15", "grade": "primary data",
         "kind": "paper", "file_url": "file:///r/s1.pdf", "size": "2048 KB"},
        {"id": "S2", "url": "https://example.org/reports/grid-outlook", "date": "2024-03-02", "grade": "trade press",
         "kind": "report", "file_url": "", "size": ""},
    ],
    "claims": [
        {"id": "C1", "text": "Lithium-ion storage cost fell to 120 USD per kWh in 2025.", "sources": ["S1"]},
        {"id": "C2", "text": "Storage cost was about 150 USD per kWh in 2025.", "sources": ["S2"]},
        {"id": "C3", "text": "Installed capacity reached 180 GWh.", "sources": ["S1", "S2"]},
        {"id": "C4", "text": "Home batteries cost around 900 USD per kWh.", "sources": ["S2"]},
    ],
    "contradictions": [
        {"claims": ["C1", "C2"], "cause": "S1 uses contract prices, S2 adjusted survey data.", "status": "resolved"},
        {"claims": ["C3", "C4"], "cause": "C3 counts all segments, C4 only homes.", "status": "open"},
    ],
}


def texts(ledger=LEDGER):
    return {lv: rr.render(ledger, lv) for lv in rr.LEVELS}


def test_levels_tuple():
    assert rr.LEVELS == ("director", "vp", "researcher", "simple")


@pytest.mark.parametrize("lv", rr.LEVELS)
def test_each_level_shows_everything(lv):
    t = rr.render(LEDGER, lv)
    for c in LEDGER["claims"]:
        assert c["id"] in t and c["text"] in t
    for s in LEDGER["sources"]:
        assert s["id"] in t and s["url"] in t and s["date"] in t and s["grade"] in t
    assert "file:///r/s1.pdf" in t and "2048 KB" in t
    for x in LEDGER["contradictions"]:  # resolved ones carry their cause too
        assert x["cause"] in t and f"[{x['status']}:{','.join(x['claims'])}]" in t
    for tier in LEDGER["tiers"]:
        assert tier in t
    for n in ("120", "150", "180", "900"):
        assert n in t


def test_wording_differs_facts_do_not():
    t = texts()
    assert len(set(t.values())) == 4
    assert rr.parity(t) == [] and rr.parity(t, LEDGER) == []
    assert rr.parity(list(t.values()), LEDGER) == []


def test_any_grade_label_renders():
    led = copy.deepcopy(LEDGER)
    led["sources"][0]["grade"] = "gold standard"
    assert all("gold standard" in t for t in texts(led).values())
    assert rr.parity(texts(led), led) == []


def test_empty_ledger_renders_and_agrees():
    led = {"topic": "t"}
    t = texts(led)
    assert all("none" in x for x in t.values()) and rr.parity(t, led) == []


def test_parity_flags_dropped_claim_id():
    t = texts()
    t["vp"] = t["vp"].replace("[C1]", "[C-]")
    bad = rr.parity(t)
    assert "vp: missing claim C1" in bad and "vp: extra claim C-" in bad


def test_parity_flags_changed_number():
    t = texts()
    t["simple"] = t["simple"].replace("900", "800")
    assert any("C4" in m and m.startswith("simple: differs") for m in rr.parity(t))
    assert any("C4" in m and m.startswith("simple: differs") for m in rr.parity(t, LEDGER))


def test_parity_flags_changed_cause_of_resolved_and_open():
    for i in (0, 1):
        t = texts()
        cause = LEDGER["contradictions"][i]["cause"]
        t["director"] = t["director"].replace(cause, "something else")
        assert any(m.startswith("director") and "missing contradiction" in m and cause in m for m in rr.parity(t))


def test_parity_flags_missing_marker():
    t = texts()
    t["director"] = t["director"].replace("[open:C3,C4]", "[open:C3]")
    assert any("director" in m and "open" in m for m in rr.parity(t))


def test_parity_against_ledger_flags_item_missing_everywhere():
    t = {lv: x.replace(LEDGER["contradictions"][0]["cause"], "gone") for lv, x in texts().items()}
    assert rr.parity(t) == []  # the levels agree with each other
    assert any("missing contradiction" in m for m in rr.parity(t, LEDGER))
    no_s2 = {lv: "\n".join(ln for ln in x.splitlines() if not ln.startswith("- [S2]")) for lv, x in texts().items()}
    assert rr.parity(no_s2) == []
    assert "vp: missing source S2" in rr.parity(no_s2, LEDGER)


def test_swapped_quantities_between_claims_fail_parity():
    t = texts()
    t["vp"] = t["vp"].replace("120", "@").replace("150", "120").replace("@", "150")
    assert rr.parity(t, LEDGER)
    assert any("C1" in m for m in rr.parity(t, LEDGER)) and any("C2" in m for m in rr.parity(t, LEDGER))
    assert any(m.startswith("vp") for m in rr.parity(t))


def test_changed_citation_fails_parity():
    t = texts()
    t["simple"] = t["simple"].replace("(sources: S1)", "(sources: S2)", 1)
    assert any("differs claim C1" in m and m.startswith("simple") for m in rr.parity(t, LEDGER))
    assert any("differs claim C1" in m for m in rr.parity(t))


def test_arbitrary_ids_render_and_pass_parity():
    led = {
        "topic": "t",
        "sources": [{"id": "paper-1", "url": "u1", "date": "d", "grade": "g", "kind": "paper"},
                    {"id": "paper-2", "url": "u2", "date": "d", "grade": "g", "kind": "report"}],
        "claims": [{"id": "claim-1", "text": "Cost is 5 (rough).", "sources": ["paper-1", "paper-2"]},
                   {"id": "claim-2", "text": "Cost is 6.", "sources": ["paper-2"]}],
        "contradictions": [{"claims": ["claim-1", "claim-2"], "cause": "unit (kWh) differs", "status": "resolved"}],
    }
    t = texts(led)
    assert all("[claim-1] Cost is 5 (rough). (sources: paper-1, paper-2)" in x for x in t.values())
    assert rr.parity(t, led) == [] and rr.parity(t) == []
    t["vp"] = t["vp"].replace("(sources: paper-1, paper-2)", "(sources: paper-1)")
    assert rr.parity(t, led)


def test_render_missing_resolved_contradiction_fails():
    t = texts()
    t["researcher"] = "\n".join(ln for ln in t["researcher"].splitlines() if "[resolved:" not in ln)
    assert any(m.startswith("researcher: missing contradiction [resolved:C1,C2]") for m in rr.parity(t, LEDGER))
    assert any(m.startswith("researcher: missing contradiction") for m in rr.parity(t))


def test_render_with_extra_claim_fails():
    t = texts()
    t["director"] = t["director"].replace("\nSource:", "- [C9] An invented claim. (sources: S1)\n\nSource:")
    assert "director: extra claim C9" in rr.parity(t, LEDGER)
    assert "director: extra claim C9" in rr.parity(t)


def test_render_sources_and_contradictions_list_every_record():
    src = rr.render_sources(LEDGER)
    for s in LEDGER["sources"]:
        assert f"- [{s['id']}]" in src and s["url"] in src and s["date"] in src and s["grade"] in src
    assert "file:///r/s1.pdf" in src and "2048 KB" in src
    con = rr.render_contradictions(LEDGER)
    for x in LEDGER["contradictions"]:
        assert f"[{x['status']}:{','.join(x['claims'])}] {x['cause']}" in con
    assert "(resolved)" in con and "(open)" in con
    assert rr.render_sources(LEDGER) == src and "none" in rr.render_contradictions({})


def test_bad_id_raises():
    led = {"claims": [{"id": "a]b", "text": "x", "sources": []}]}
    with pytest.raises(ValueError):
        rr.render(led, "vp")


def test_unknown_level_raises():
    with pytest.raises(ValueError):
        rr.render(LEDGER, "ceo")


RECORD = re.compile(r"^- \[")
HEAD_WORDS = {h for hs in rr.HEADS.values() for h in hs}


def prose(text):
    """Non-blank lines that are not records, not none-markers and not a plain heading of the record layout."""
    return [ln for ln in text.splitlines()
            if ln.strip() and not RECORD.match(ln) and ln != "- none" and ln.split(":")[0] not in HEAD_WORDS]


def records(text):
    return [ln for ln in text.splitlines() if RECORD.match(ln) and ln != "- none"]


def test_prose_differs_per_level_with_three_distinct_lines():
    t = texts()
    for lv, text in t.items():
        others = {ln for o, x in t.items() if o != lv for ln in prose(x)}
        assert len([ln for ln in prose(text) if ln not in others]) >= 3, lv
    for a in rr.LEVELS:
        for b in rr.LEVELS:
            assert a == b or set(prose(t[a])) != set(prose(t[b]))


def test_record_lines_identical_across_levels():
    t = texts()
    base = records(t["researcher"])
    assert len(base) == 8
    for lv in rr.LEVELS:  # only the status word at the end of a contradiction differs
        assert [ln.rsplit(" (", 1)[0] for ln in records(t[lv])] == [ln.rsplit(" (", 1)[0] for ln in base]
        assert [ln for ln in records(t[lv]) if "[S" in ln or "[C" in ln] == [ln for ln in base if "[S" in ln or "[C" in ln]


def test_prose_never_looks_like_a_record():
    for text in texts().values():
        for ln in text.splitlines():
            if not ln.startswith("- "):
                assert not ln.lstrip().startswith("- [")


def test_level_prose_blocks_name_their_audience_content():
    t = texts()
    d, v, r, s = (t[lv] for lv in rr.LEVELS)
    assert "Bottom line:" in d and "Decision risk:" in d and "S1 (paper, primary data, 2026-09-15)" in d
    assert "claims C3, C4" in d and "method" not in d.lower()
    assert "What this means:" in v and "Confidence:" in v
    assert "Claims backed by one source: C1, C2, C4." in v and "Claims backed by several sources: C3." in v
    assert "Method and evidence quality:" in r and "primary data > peer-reviewed > trade press" in r
    assert "  S2 (report, trade press, 2024-03-02)" in r and "C1 (S1), C2 (S2), C4 (S2)" in r
    assert "  open C3,C4: C3 counts all segments, C4 only homes." in r
    assert "In short:" in s and "Claims C3 and C4 do not match yet. The recorded reason: C3 counts" in s
    assert "A grade says how much weight a source gets" in s and '"Sources disagree" means' in s


def test_rewriting_or_deleting_prose_keeps_parity_but_altering_a_record_does_not():
    base = texts()
    for lv in rr.LEVELS:
        t = dict(base)
        keep = [ln for ln in base[lv].splitlines() if ln not in prose(base[lv])]
        t[lv] = "\n".join(keep) + "\n"  # prose deleted
        assert rr.parity(t, LEDGER) == [] and rr.parity(t) == [], lv
        t[lv] = "\n".join(f"{ln} (rewritten)" if ln in prose(base[lv]) else ln for ln in base[lv].splitlines())
        assert rr.parity(t, LEDGER) == [] and rr.parity(t) == [], lv
        t[lv] = base[lv].replace("[C3] Installed capacity reached 180 GWh.", "[C3] Installed capacity reached 190 GWh.")
        assert any(m.startswith(f"{lv}: differs claim C3") for m in rr.parity(t, LEDGER)), lv
        assert any(m.startswith(lv) for m in rr.parity(t)), lv


def test_prose_cannot_smuggle_a_record_into_parity():
    t = texts()
    t["vp"] = t["vp"].replace("What this means:", "What this means:\n  - [C9] Invented. (sources: S1)")
    assert rr.parity(t, LEDGER) == []


def counts(led):
    """Every number the prose may state: counts of ledger items, derived independently of the renderer."""
    cites = [set(c["sources"]) for c in led.get("claims", [])]
    cons = led.get("contradictions", [])
    n_open = sum(x["status"] == "open" for x in cons)
    n_top = sum(s["grade"] == led["tiers"][0] for s in led.get("sources", [])) if led.get("tiers") else 0
    return {len(led.get("claims", [])), len(led.get("sources", [])), n_open, len(cons) - n_open,
            sum(len(c) == 1 for c in cites), n_top}


def test_prose_numbers_are_ledger_counts():
    led = LEDGER
    allowed = counts(led)
    assert allowed == {4, 2, 1, 3}
    fields = [led["topic"], *led["tiers"], *(x["cause"] for x in led["contradictions"])]
    for s in led["sources"]:
        fields += [s["id"], s["date"], s["kind"], s["grade"]]
    fields += [c["id"] for c in led["claims"]]
    for lv, text in texts().items():
        for ln in prose(text):
            for f in sorted(fields, key=len, reverse=True):
                ln = ln.replace(f, " ")
            assert {int(n) for n in re.findall(r"\d+", ln)} <= allowed, (lv, ln)


def test_prose_counts_follow_the_ledger():
    led = copy.deepcopy(LEDGER)
    led["contradictions"][1]["status"] = "resolved"
    t = texts(led)
    assert "Open conflicts: 0." in t["director"] and "nothing is recorded as unresolved" in t["director"]
    assert "0 conflicts still open and 2 resolved" in t["vp"] and "Confidence: moderate" in t["vp"]
    assert "no disagreement is left" in t["simple"] and "do not match yet" not in t["simple"]
    assert rr.parity(t, led) == []


def test_prose_is_template_only_no_topic_words():
    led = {"topic": "zebra migration", "claims": [{"id": "k1", "text": "x", "sources": ["a"]}],
           "sources": [{"id": "a", "date": "2020-01-01", "grade": "g", "kind": "k"}], "tiers": ["g"]}
    for text in texts(led).values():
        assert all("zebra" not in ln for ln in prose(text))
