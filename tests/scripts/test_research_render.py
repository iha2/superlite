"""research_render: four levels of one level-free ledger, and the parity check against it."""
import copy
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
