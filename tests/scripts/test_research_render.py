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
    t["vp"] = t["vp"].replace("C1", "")
    assert "vp: missing claim/source id C1" in rr.parity(t)


def test_parity_flags_changed_number():
    t = texts()
    t["simple"] = t["simple"].replace("900", "800")
    assert any("900" in m and m.startswith("simple") for m in rr.parity(t))


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
    t = {lv: x.replace("S2", "").replace("150", "") for lv, x in texts().items()}
    assert rr.parity(t) == []  # the levels agree with each other
    bad = rr.parity(t, LEDGER)
    assert "vp: missing claim/source id S2" in bad and "vp: missing number 150" in bad
    dropped = {lv: x.replace(LEDGER["contradictions"][0]["cause"], "") for lv, x in texts().items()}
    assert any("contradiction" in m for m in rr.parity(dropped, LEDGER))


def test_unknown_level_raises():
    with pytest.raises(ValueError):
        rr.render(LEDGER, "ceo")
