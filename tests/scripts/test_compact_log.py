"""compact_log: nudge ledger and compact reconcile (T1 faked)."""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"))
import compact_log  # noqa: E402


class FakeCT:
    def __init__(self):
        self.boundary = None
        self.tokens = 0

    def last_compact_boundary(self, path):
        return self.boundary

    def current_context(self, path):
        return self.tokens

    def snapshot(self, path):
        return {"boundary": self.boundary, "tokens": self.tokens}


@pytest.fixture
def ct(monkeypatch):
    fake = FakeCT()
    if os.environ.get("SUPERLITE_REAL") != "1":
        monkeypatch.setattr(compact_log, "context_tokens", fake)
    return fake


def events(sl):
    p = Path(sl) / "compact.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_counts_1_2_3(tmp_path, ct):
    got = [compact_log.record_nudge(str(tmp_path), 160000, 150000, 1.0 + i) for i in range(3)]
    assert got == [1, 2, 3]
    assert [e["ev"] for e in events(tmp_path)].count("compact_nudge") == 3


def test_missed_once(tmp_path, ct):
    for i in range(3):
        compact_log.record_nudge(str(tmp_path), 160000, 150000, float(i))
    assert [e for e in events(tmp_path) if e["ev"] == "compact_missed"] != []
    assert len([e for e in events(tmp_path) if e["ev"] == "compact_missed"]) == 1


def test_missed_written_once(tmp_path, ct):
    for i in range(4):
        compact_log.record_nudge(str(tmp_path), 160000, 150000, float(i))
    assert len([e for e in events(tmp_path) if e["ev"] == "compact_missed"]) == 1


def test_reconcile_none(tmp_path, ct):
    assert compact_log.reconcile(str(tmp_path), "t.jsonl", 5.0) == "none"


def test_reconcile_pending(tmp_path, ct):
    compact_log.record_nudge(str(tmp_path), 160000, 150000, 1.0, boundary_line=3, transcript="t.jsonl")
    ct.boundary = {"line": 3, "pre_tokens": 1, "trigger": "auto"}
    assert compact_log.reconcile(str(tmp_path), "t.jsonl", 5.0) == "pending"
    ct.boundary = None
    assert compact_log.reconcile(str(tmp_path), "t.jsonl", 5.0) == "pending"


def test_reconcile_done_logs_before_after(tmp_path, ct):
    compact_log.record_nudge(str(tmp_path), 160000, 150000, 1.0, boundary_line=3, transcript="t.jsonl")
    ct.boundary = {"line": 7, "pre_tokens": 162000, "trigger": "auto"}
    ct.tokens = 2000
    assert compact_log.reconcile(str(tmp_path), "t.jsonl", 5.0) == "done"
    done = [e for e in events(tmp_path) if e["ev"] == "compact_done"]
    assert len(done) == 1 and done[0]["before"] == 160000 and done[0]["after"] == 2000
    assert compact_log.reconcile(str(tmp_path), "t.jsonl", 6.0) == "none"


def test_count_resets_after_done(tmp_path, ct):
    compact_log.record_nudge(str(tmp_path), 160000, 150000, 1.0, transcript="t")
    compact_log.record_nudge(str(tmp_path), 160000, 150000, 2.0, transcript="t")
    ct.boundary = {"line": 9, "pre_tokens": 1, "trigger": "manual"}
    ct.tokens = 2000
    assert compact_log.reconcile(str(tmp_path), "t", 3.0) == "done"
    assert compact_log.record_nudge(str(tmp_path), 160000, 150000, 4.0, transcript="t") == 1


def test_unwritable_dir_never_raises(tmp_path, ct):
    if os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0):
        pytest.skip("cannot make read-only dir")
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        assert compact_log.record_nudge(str(ro), 160000, 150000, 1.0) == 1
        assert compact_log.reconcile(str(ro), "t", 2.0) in ("none", "pending", "done")
    finally:
        ro.chmod(0o700)


def test_missing_dir_and_corrupt_state(tmp_path, ct):
    sl = tmp_path / "new" / "sl"
    assert compact_log.record_nudge(str(sl), 1, 1, 1.0) == 1
    (sl / "compact_state.json").write_text("{not json\xe9", encoding="utf-8")
    assert compact_log.record_nudge(str(sl), 1, 1, 2.0) == 1


def test_state_is_scoped_per_transcript(tmp_path, ct):
    sl = str(tmp_path)
    assert compact_log.record_nudge(sl, 160000, 150000, 1.0, transcript="a") == 1
    assert compact_log.record_nudge(sl, 160000, 150000, 2.0, transcript="a") == 2
    assert compact_log.record_nudge(sl, 160000, 150000, 3.0, transcript="b") == 1
    assert compact_log.record_nudge(sl, 160000, 150000, 4.0, transcript="a") == 3
    assert [e for e in events(tmp_path) if e["ev"] == "compact_missed"][0]["transcript"] == "a"
    assert len([e for e in events(tmp_path) if e["ev"] == "compact_missed"]) == 1


def test_boundary_compared_only_within_same_transcript(tmp_path, ct):
    sl = str(tmp_path)
    compact_log.record_nudge(sl, 160000, 150000, 1.0, boundary_line=50, transcript="a")
    compact_log.record_nudge(sl, 160000, 150000, 1.0, boundary_line=2, transcript="b")
    ct.boundary = {"line": 10, "pre_tokens": 1, "trigger": "auto"}
    ct.tokens = 2000
    assert compact_log.reconcile(sl, "a", 5.0) == "pending"  # line 10 <= a's 50
    assert compact_log.reconcile(sl, "b", 5.0) == "done"
    assert compact_log.reconcile(sl, "a", 6.0) == "pending"  # b's done left a's state alone


def test_reconcile_waits_for_post_compact_measurement(tmp_path, ct):
    compact_log.record_nudge(str(tmp_path), 160000, 150000, 1.0, boundary_line=3, transcript="t")
    ct.boundary = {"line": 7, "pre_tokens": 162000, "trigger": "auto"}
    ct.tokens = 0
    assert compact_log.reconcile(str(tmp_path), "t", 5.0) == "pending"
    assert not [e for e in events(tmp_path) if e["ev"] == "compact_done"]
    ct.tokens = 2000
    assert compact_log.reconcile(str(tmp_path), "t", 6.0) == "done"


def test_concurrent_nudges_preserve_state_and_valid_json(tmp_path, ct):
    sl = str(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        counts = list(pool.map(
            lambda i: compact_log.record_nudge(sl, 160000, 150000, float(i), transcript="t"),
            range(40)))
    assert max(counts) == 40
    state = json.loads((tmp_path / "compact_state.json").read_text(encoding="utf-8"))
    assert state["sessions"]["t"]["count"] == 40
    assert len([e for e in events(tmp_path) if e["ev"] == "compact_nudge"]) == 40
