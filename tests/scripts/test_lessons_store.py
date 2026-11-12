"""lessons_store.py: the store and the cache step; lessons_score is faked unless SUPERLITE_REAL=1."""
import hashlib
import os
import sys
import types
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import base  # noqa: E402
import lessons_store as lessons  # noqa: E402


FAKE_U_ONLY = {"test_step_twice_no_new_events", "test_evict_past_grace", "test_full_cache_displaces_lowest_rank",
               "test_readmit_grace_from_and_dropped", "test_proven"}


@pytest.fixture(autouse=True)
def score(monkeypatch, request):
    if os.environ.get("SUPERLITE_REAL") == "1":
        if request.node.name in FAKE_U_ONLY:
            pytest.skip("injects U through the fake usefulness")
        return
    fake = types.SimpleNamespace()
    fake.baseline = lambda modules, module, before, run_index: 0.5
    fake.usefulness = lambda deliveries, base, run_index: deliveries[-1]["u"] if deliveries else 0.0
    fake.rank = lambda les: les["u"] if len(les["deliveries"]) >= 3 else 0.1
    monkeypatch.setattr(lessons, "lessons_score", fake)


def mk(store, key="a", kind="pitfall", runs=("r1", "r2"), module="skills/x", **kw):
    for r in runs:
        lid = lessons.add_signal(store, kind, module, key, "t", "s" + r, r, **kw)
    return lid


def deliver(store, lid, n, u):
    store["lessons"][lid]["deliveries"] += [{"run": "r1", "hit": True, "u": u}] * n


def test_empty_and_load(tmp_path):
    assert lessons.empty() == {"v": 1, "run_index": [], "modules": {}, "lessons": {}}
    assert lessons.load(tmp_path) == lessons.empty()


def test_update_saves(tmp_path):
    def fn(s):
        s["run_index"].append("r1")
        return s
    out = lessons.update(tmp_path, fn)
    assert out["run_index"] == ["r1"]
    assert lessons.load(tmp_path)["run_index"] == ["r1"]


def test_lesson_id():
    assert lessons.lesson_id("pitfall", "skills/x", "test-failure") == \
        "L-" + hashlib.sha1(b"pitfall|skills/x|test-failure").hexdigest()[:6]


def test_add_signal_dedupes_and_cuts():
    s = lessons.empty()
    lid = lessons.add_signal(s, "pitfall", "m", "k", "old", "src", "r2")
    lessons.add_signal(s, "pitfall", "m", "k", "n" * 300, "src", "r1")
    les = s["lessons"][lid]
    assert les["sources"] == ["src"] and les["runs"] == ["r1", "r2"] and len(les["text"]) == 160


def test_note_run_once():
    s = lessons.empty()
    lessons.note_run(s, "r2"); lessons.note_run(s, "r2")
    assert s["run_index"] == ["r2"]


def test_admit_two_runs_or_verified():
    s = lessons.empty(); lessons.note_run(s, "r2")
    a = mk(s, "a"); b = mk(s, "b", runs=("r1",)); c = mk(s, "c", runs=("r1",), verified=True)
    ev = lessons.step(s)
    assert f"admitted {a}" in ev and f"admitted {c}" in ev
    assert s["lessons"][b]["status"] == "candidate"
    assert s["lessons"][a]["admitted_run"] == "r2" and s["lessons"][a]["baseline"] == 0.5


def test_retracted_practice_demoted_at_next_step():
    """An admitted practice whose second run was retracted (revised outcome) leaves the cache at once,
    not after three deliveries; verified fixes and pinned practices keep their slot."""
    s = lessons.empty(); lessons.note_run(s, "r2")
    a = mk(s, "a", kind="practice"); v = mk(s, "v", kind="practice", runs=("r1",), verified=True)
    p = mk(s, "p", kind="practice"); lessons.step(s); lessons.control(s, p, "pin")
    for lid in (a, p):
        s["lessons"][lid]["runs"] = ["r1"]
    assert lessons.step(s) == [f"demoted {a}"]
    les = s["lessons"][a]
    assert les["status"] == "candidate" and les["admitted_run"] is None and les["proven"] is False
    assert s["lessons"][v]["status"] == "cached" and s["lessons"][p]["status"] == "cached"
    s["lessons"][a]["runs"] = ["r1", "r3"]
    assert lessons.step(s) == [f"admitted {a}"]


def test_step_twice_no_new_events():
    s = lessons.empty(); a = mk(s); lessons.step(s)
    deliver(s, a, 3, -0.05)
    assert lessons.step(s) != []
    assert lessons.step(s) == []


def test_evict_past_grace():
    s = lessons.empty(); a = mk(s); lessons.step(s)
    deliver(s, a, 2, -0.05)
    assert lessons.step(s) == []
    deliver(s, a, 1, -0.05)
    assert lessons.step(s) == [f"evicted {a} (U -0.05)"]
    assert s["lessons"][a]["status"] == "retired"


def test_pinned_never_evicted():
    s = lessons.empty(); a = mk(s); lessons.step(s)
    lessons.control(s, a, "pin"); deliver(s, a, 5, -0.5)
    assert lessons.step(s) == [] and s["lessons"][a]["status"] == "cached"


def test_full_cache_displaces_lowest_rank(monkeypatch):
    monkeypatch.setattr(lessons, "CAP", 2)
    s = lessons.empty(); a = mk(s, "a"); b = mk(s, "b"); lessons.step(s)
    deliver(s, a, 3, 0.3); deliver(s, b, 3, 0.1)
    n = mk(s, "n")
    ev = lessons.step(s)
    assert f"evicted {b} (U 0.10)" in ev and f"admitted {n}" in ev
    assert s["lessons"][b]["status"] == "retired"


def test_full_cache_no_victim_keeps_candidate(monkeypatch):
    monkeypatch.setattr(lessons, "CAP", 1)
    s = lessons.empty(); a = mk(s, "a"); lessons.step(s)
    n = mk(s, "n")
    assert lessons.step(s) == [] and s["lessons"][n]["status"] == "candidate"


def test_readmit_grace_from_and_dropped():
    s = lessons.empty(); a = mk(s); lessons.step(s)
    deliver(s, a, 3, -0.1); lessons.step(s)
    assert lessons.step(s) == []
    lessons.add_signal(s, "pitfall", "skills/x", "a", "t", "new", "r3")
    assert lessons.step(s) == [f"readmitted {a}"]
    les = s["lessons"][a]
    assert les["status"] == "cached" and len(les["deliveries"]) == 3 and les["grace_from"] == 3
    deliver(s, a, 2, -0.1)
    assert lessons.step(s) == []  # grace counts from readmission
    deliver(s, a, 1, -0.1)
    assert lessons.step(s) == [f"evicted {a} (U -0.10)"]
    lessons.control(s, a, "drop")
    lessons.add_signal(s, "pitfall", "skills/x", "a", "t", "new2", "r4")
    assert lessons.step(s) == [] and s["lessons"][a]["status"] == "dropped"


def test_proven():
    s = lessons.empty(); p = mk(s, "p", kind="practice"); q = mk(s, "q", kind="practice"); lessons.step(s)
    deliver(s, p, 5, 0.2); deliver(s, q, 5, 0.19)
    ev = lessons.step(s)
    assert f"proven {p}" in ev and s["lessons"][p]["proven"] and not s["lessons"][q]["proven"]
    deliver(s, p, 1, 0.1)
    lessons.step(s)
    assert s["lessons"][p]["proven"] is False


def test_control():
    s = lessons.empty(); a = mk(s)
    assert lessons.control(s, a, "drop") and s["lessons"][a]["status"] == "dropped"
    assert lessons.control(s, a, "restore") and s["lessons"][a]["status"] == "candidate"
    assert lessons.control(s, a, "pin") and s["lessons"][a]["pinned"]
    assert lessons.control(s, "L-nope", "drop") is False


def test_hub_dir_worktree_shares_hub(tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    base.git("init", "-q", cwd=main)
    base.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x", cwd=main)
    wt = tmp_path / "wt"
    base.git("worktree", "add", "-q", str(wt), "-b", "b", cwd=main)
    assert lessons.hub_dir(wt).resolve() == lessons.hub_dir(main).resolve() == (main / ".superlite").resolve()


def test_hub_dir_outside_repo_falls_back(tmp_path):
    assert lessons.hub_dir(tmp_path) == tmp_path / ".superlite"


def test_readmit_obeys_capacity_and_victim_rule():
    s = lessons.empty()
    a = mk(s, "a"); lessons.step(s)
    deliver(s, a, 3, -0.1); lessons.step(s)
    assert s["lessons"][a]["status"] == "retired"
    for i in range(lessons.CAP):
        lid = mk(s, f"k{i}")
        s["lessons"][lid].update(status="cached", pinned=True)
    lessons.add_signal(s, "pitfall", "skills/x", "a", "t", "new", "r3")
    assert lessons.step(s) == []
    assert s["lessons"][a]["status"] == "retired"
    assert len(lessons._cached(s)) == lessons.CAP
    s["lessons"][lid]["pinned"] = False
    s["lessons"][lid]["deliveries"] = [{"run": "r1", "hit": True, "u": 0.5}] * 3
    ev = lessons.step(s)
    assert f"readmitted {a}" in ev and s["lessons"][a]["status"] == "cached"
    assert len(lessons._cached(s)) == lessons.CAP
