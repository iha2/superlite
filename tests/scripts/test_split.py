"""Exact split: scenario table, brute-force agreement, timing, no empty batches."""
from __future__ import annotations

import random
import time

from test_plan_tools import SCRIPTS

import split  # noqa: E402  # scripts on path via SCRIPTS


def sizes(batches):
    return sorted(len(b) for b in batches)


def test_five_equal_gives_three_with_2_2_1():
    r = split.choose([100] * 5)  # 10.0 min each in tenths
    assert r is not None
    assert r[2] == 3
    assert sizes(r[3]) == [1, 2, 2]
    assert r[1] == 200  # finish 20.0 min


def test_scenario_table_rows():
    # minutes as tenths; busy left as tenths. From review plan scenario table (max(B+A, F/T)).
    rows = [
        ([100] * 4, (), 2, [2, 2], 200),
        ([100] * 5, (), 3, [1, 2, 2], 200),
        ([60] * 16, (), 4, [4, 4, 4, 4], 240),
        ([60] * 17, (), 5, [3, 3, 3, 4, 4], 240),
        ([60] * 20, (), 5, [4, 4, 4, 4, 4], 240),
        ([60] * 21, (), 5, [4, 4, 4, 4, 5], 300),
        ([60] * 25, (), 5, [5, 5, 5, 5, 5], 300),
        ([60] * 26, (), 6, [4, 4, 4, 4, 5, 5], 300),
        ([60] * 100, (), 10, [10] * 10, 600),
        ([100, 40, 30, 30], (), 2, [1, 3], 100),  # 10 alone, and 4+3+3
        ([100, 60, 60], (), 2, [1, 2], 120),
        ([300, 100, 100, 100, 80, 60, 40, 20], (), 2, [3, 5], 400),  # 30+8+2 and 10*3+6+4
        ([300] + [20] * 12, (), 1, [13], 540),
        ([300] + [20] * 20, (), 2, [4, 17], 360),  # 30+2+2+2 and seventeen of 2
        ([100] + [10] * 8, (), 1, [9], 180),
        ([40, 20, 20], (), 2, [1, 2], 40),  # CP handled separately below
        ([60] * 3, (200,), 1, [3], 200),
        ([60] * 3, (200, 150, 120, 80), 1, [3], 200),
        ([30] * 10, (300,), 1, [10], 300),
        ([60] * 4, (120, 50), 1, [4], 240),
    ]
    for ms, busy, A, want_sizes, F in rows:
        r = split.choose(ms, busy)
        assert r is not None, (ms, busy)
        assert r[2] == A, (ms, busy, r[2], A)
        assert sizes(r[3]) == sorted(want_sizes), (ms, busy, sizes(r[3]), want_sizes)
        assert r[1] == F, (ms, busy, r[1], F)
        assert all(b for b in r[3]), (ms, busy)


def test_critical_path_alone():
    r = split.choose([40, 20, 20], (), [0, 0, 0], float("inf"), cp={0})
    assert r is not None
    assert r[2] == 2
    alone = [b for b in r[3] if 0 in b]
    assert alone == [[0]]
    assert sizes(r[3]) == [1, 2]


def test_context_budget_forces_extra_implementer():
    # minutes fit on one, context does not
    r = split.choose([10, 10], (), [60, 60], cap=100)
    assert r is not None
    assert r[2] == 2
    assert sizes(r[3]) == [1, 1]


def test_brute_force_agreement_300():
    random.seed(7)
    bad = 0
    for _ in range(300):
        n = random.randint(1, 6)
        ms = [random.choice([10, 20, 30, 40, 50, 60, 80, 100]) for _ in range(n)]
        busy = tuple(b for b in (random.choice([0, 50, 120, 200]) for _ in range(random.randint(0, 2))) if b)
        ctx = [random.choice([10, 20, 30, 40]) for _ in range(n)]
        cap = random.choice([float("inf"), 80, 60])
        cp = set(random.sample(range(n), random.randint(0, min(1, n))))
        e, b = split.choose(ms, busy, ctx, cap, cp), split.brute(ms, busy, ctx, cap, cp)
        if (e is None) != (b is None):
            bad += 1
            continue
        if e and (abs(e[0] - b[0]) > 1e-9 or abs(e[1] - b[1]) > 1e-9 or e[2] != b[2]):
            bad += 1
        if e:
            assert all(g for g in e[3])
            assert sorted(i for g in e[3] for i in g) == list(range(n))
    assert bad == 0


def test_sixty_tasks_under_one_second():
    random.seed(3)
    ms = [random.randint(15, 60) for _ in range(60)]
    t0 = time.perf_counter()
    r = split.choose(ms)
    assert time.perf_counter() - t0 < 1.0
    assert r is not None
    assert all(g for g in r[3])


def test_tenths_helper():
    assert split.tenths(2.354) == 24
    assert split.tenths(2.35) == 24
    assert split.tenths(1.0) == 10


def test_predictor_check_value():
    # review plan: 1 component, 120 loc, slow, Sonnet -> 2.354 min
    import predict

    coef = predict.load_coef()
    models = predict.load_models()
    feats = {"components": 1, "loc": 120, "tests": "slow"}
    assert abs(predict.time_from(feats, "sonnet", models, coef) - 2.354) < 1e-3
