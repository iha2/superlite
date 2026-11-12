"""lessons_score.py: scoring math."""
import sys
from pathlib import Path

from hypothesis import given, strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"))
import lessons_score as ls  # noqa: E402


def test_module_key():
    assert ls.module_key("skills/x/y.py") == "skills/x"
    assert ls.module_key("README.md") == "."
    assert ls.module_key("a\\b\\c.py") == "a/b"


def test_weight():
    assert ls.weight(10) == 0.5
    assert ls.weight(0) == 1.0


def test_baseline():
    mods = {"skills/x": {"r1": {"tasks": 4, "ftp": 2}}}
    assert ls.baseline(mods, "skills/x", "r2", ["r1", "r2"]) == 0.5
    assert ls.baseline(mods, "skills/x", "r1", ["r1", "r2"]) == 0.5  # no earlier run
    assert ls.baseline(mods, "other", None, ["r1"]) == 0.5
    mods = {"m": {"r1": {"tasks": 2, "ftp": 2}, "r2": {"tasks": 2, "ftp": 0}, "r3": {"tasks": 4, "ftp": 4}}}
    assert ls.baseline(mods, "m", "r3", ["r1", "r2", "r3"]) == 0.5
    assert ls.baseline(mods, "m", "r2", ["r1", "r2", "r3"]) == 1.0
    assert ls.baseline(mods, "m", None, ["r1", "r2", "r3"]) == 0.75


def test_usefulness():
    assert abs(ls.usefulness([{"run": "r3", "hit": True}] * 4, 0.5, ["r1", "r2", "r3"]) - 0.4) < 1e-9
    assert ls.usefulness([], 0.5, ["r1"]) == 0.0


def test_rank():
    d = {"run": "r1", "hit": True}
    assert ls.rank({"u": -0.2, "deliveries": [d, d]}) == 0.1
    assert ls.rank({"u": -0.2, "deliveries": [d, d, d]}) == -0.2
    assert ls.rank({"u": -0.2, "deliveries": [d, d, d], "grace_from": 1}) == 0.1
    assert ls.rank({"u": 0.3}) == 0.1


def test_line():
    les = {"module": "skills/x", "kind": "practice", "proven": True, "text": "do: run t.py", "runs": ["r1", "r2"]}
    assert ls.line(les) == "- skills/x: do: run t.py (practice, proven, 2 runs)"
    les.update(proven=False, kind="pitfall")
    assert ls.line(les) == "- skills/x: do: run t.py (pitfall, 2 runs)"
    les["text"] = "z" * 500
    assert len(ls.line(les)) <= 160 and ls.line(les).endswith("(pitfall, 2 runs)")


def mk(i, module="m", kind="pitfall", u=0.0, proven=False, text="t"):
    return {"id": f"L{i}", "module": module, "kind": kind, "u": u, "proven": proven, "text": text,
            "runs": ["r1"], "deliveries": [{}] * 3}


def test_select_order():
    a, b, c = mk(1, u=0.1), mk(2, u=0.5), mk(3, u=0.2, proven=True, kind="practice", module="z")
    assert [x["id"] for x in ls.select([a, b, c], 1000)] == ["L3", "L2", "L1"]


def test_select_practice_before_pitfall_of_module():
    pit = mk(1, u=0.9)
    prac = mk(2, u=0.1, kind="practice")
    other = mk(3, u=0.5, module="q")
    assert [x["id"] for x in ls.select([pit, other, prac], 1000)] == ["L2", "L1", "L3"]


def test_select_budget_whole_lines():
    a, b = mk(1, u=0.5), mk(2, u=0.1)
    n = len(ls.line(a)) + 1
    assert ls.select([a, b], n) == [a]
    assert ls.select([a, b], n - 1) == []
    assert ls.select([a, b], 2 * n) == [a, b]


def test_deterministic():
    items = [mk(i, u=(i % 3) / 10, kind="practice" if i % 2 else "pitfall") for i in range(8)]
    assert ls.select(items, 1000) == ls.select(list(reversed(items)), 1000)


RUNS = [f"r{i}" for i in range(40)]


@given(st.lists(st.tuples(st.sampled_from(RUNS), st.booleans()), max_size=30),
       st.floats(0, 1, allow_nan=False))
def test_prop_u_bounded(ds, base):
    u = ls.usefulness([{"run": r, "hit": h} for r, h in ds], base, RUNS)
    assert -1 <= u <= 1


@given(st.integers(0, 500), st.integers(0, 500))
def test_prop_weight_non_increasing(a, b):
    lo, hi = sorted((a, b))
    assert ls.weight(hi) <= ls.weight(lo)


@given(st.lists(st.tuples(st.integers(0, 3), st.integers(0, 1000), st.booleans(),
                          st.sampled_from(["pitfall", "practice"]), st.sampled_from(["a", "b"]),
                          st.text(max_size=300)), max_size=15),
       st.integers(0, 1500))
def test_prop_select_within_budget(items, budget):
    lessons = [mk(i, module=m, kind=k, u=u / 1000, proven=p, text=t)
               for i, (_, u, p, k, m, t) in enumerate(items)]
    chosen = ls.select(lessons, budget)
    assert sum(len(ls.line(x)) + 1 for x in chosen) <= budget


def test_older_same_hits_give_smaller_u():
    runs = [f"r{i}" for i in range(30)]
    new = ls.usefulness([{"run": "r25", "hit": True}] * 4, 0.5, runs)
    old = ls.usefulness([{"run": "r5", "hit": True}] * 4, 0.5, runs)
    assert old < new


def test_line_singular():
    les = {"module": "skills/x", "kind": "practice", "proven": True, "text": "do: run t.py", "runs": ["r1"]}
    assert ls.line(les) == "- skills/x: do: run t.py (practice, proven, 1 run)"
    les["runs"] = []
    assert ls.line(les).endswith("(practice, proven, 0 runs)")
