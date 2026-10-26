#!/usr/bin/env python3
"""Exact implementer split for dispatch.

A* = argmin max(B + A, F(A)/T); ties go to smaller F(A), then smaller A.
F(A) is the exact minimum makespan over splits onto A new implementers, within
the context budget. Critical-path tasks run alone. Minutes are whole tenths.
"""
from __future__ import annotations

import itertools  # O(1)


def tenths(minutes: float) -> int:  # O(1)
    """Nearest tenth of a minute as an int (6 seconds)."""
    return int(round(float(minutes) * 10))  # O(1)


def lpt(ms, ctx, A, cap):  # O(n log n + n*A)
    loads, cl, bins = [0] * A, [0] * A, [[] for _ in range(A)]  # O(A)
    for i in sorted(range(len(ms)), key=lambda i: -ms[i]):  # O(n log n)
        ok = [j for j in range(A) if cl[j] + ctx[i] <= cap]  # O(A)
        if not ok:  # O(1)
            return None  # O(1)
        j = min(ok, key=lambda j: loads[j])  # O(A)
        loads[j] += ms[i]  # O(1)
        cl[j] += ctx[i]  # O(1)
        bins[j].append(i)  # O(1)
    return max(loads, default=0), bins  # O(A)


def fits(items, A, cap, ccap, memo):  # O(exponential worst; memoized)
    """Pack items into A bins of at most cap minutes and ccap context, or None.

    items: (minutes, context, id) longest first. The longest opens a bin; every
    fullest-first completion is tried. Identical twins are excluded together.
    """
    if not items:  # O(1)
        return []  # O(1)
    if A == 0:  # O(1)
        return None  # O(1)
    total = sum(m for m, _, _ in items)  # O(n)
    if total > A * cap:  # O(1)
        return None  # O(1)
    if any(m > cap or c > ccap for m, c, _ in items):  # O(n)
        return None  # O(1)
    if A >= len(items):  # O(n); one task per implementer — avoids O(A·n) recursion depth
        return [[i] for _, _, i in items]  # O(n)
    if A == 1:  # O(1)
        return [[i for _, _, i in items]] if sum(c for _, c, _ in items) <= ccap else None  # O(n)
    key = (A, tuple((m, c) for m, c, _ in items))  # O(n)
    if key in memo:  # O(1)
        return None  # O(1)
    (m0, c0, i0), rest = items[0], items[1:]  # O(1)
    need = total - (A - 1) * cap  # O(1)
    suffix = [0] * (len(rest) + 1)  # O(n)
    for k in range(len(rest) - 1, -1, -1):  # O(n)
        suffix[k] = suffix[k + 1] + rest[k][0]  # O(1)
    chosen: list[int] = []  # O(1)
    chosen_set: set[int] = set()  # O(1)

    def complete(pos, load, cload):  # O(2^n)
        if load + suffix[pos] < need:  # O(1)
            return None  # O(1)
        if pos == len(rest):  # O(1)
            left = [it for k, it in enumerate(rest) if k not in chosen_set]  # O(n)
            sub = fits(left, A - 1, cap, ccap, memo)  # recurse
            return None if sub is None else [[i0] + [rest[k][2] for k in chosen]] + sub  # O(n)
        m, c, _ = rest[pos]  # O(1)
        if load + m <= cap and cload + c <= ccap:  # O(1)
            chosen.append(pos)  # O(1)
            chosen_set.add(pos)  # O(1)
            r = complete(pos + 1, load + m, cload + c)  # O(2^n)
            chosen.pop()  # O(1)
            chosen_set.discard(pos)  # O(1)
            if r is not None:  # O(1)
                return r  # O(1)
        nxt = pos + 1  # O(1)
        while nxt < len(rest) and rest[nxt][:2] == rest[pos][:2]:  # O(twins)
            nxt += 1  # O(1)
        return complete(nxt, load, cload)  # O(2^n)

    r = complete(0, m0, c0) if c0 <= ccap and m0 <= cap else None  # O(2^n)
    if r is None:  # O(1)
        memo.add(key)  # O(1)
    return r  # O(1)


def exact_split(ms, ctx, A, cap):  # O(log(sum) * packing)
    """Exact minimum makespan (tenths) and batches for A implementers."""
    n = len(ms)  # O(1)
    if n == 0:  # O(1)
        return 0, [[] for _ in range(A)]  # O(A)
    if A <= 0:  # O(1)
        return None  # O(1)
    items = sorted(((ms[i], ctx[i], i) for i in range(n)), key=lambda t: (-t[0], -t[1]))  # O(n log n)
    srt = [t[0] for t in items]  # O(n)
    lo = max(srt[0], -(-sum(srt) // A), srt[A - 1] + srt[A] if A < n else 0)  # O(n)
    start = lpt(ms, ctx, A, cap)  # O(n*A)
    hi, best = (int(start[0]), start[1]) if start else (sum(srt), None)  # O(1)
    if best is not None and hi <= lo:  # O(1)
        return hi, best  # O(1)
    if best is None:  # O(1)
        got = fits(items, A, hi, cap, set())  # O(packing)
        if got is None:  # O(1)
            return None  # O(1)
        best = got  # O(1)
    while lo < hi:  # O(log sum)
        mid = (lo + hi) // 2  # O(1)
        got = fits(items, A, mid, cap, set())  # O(packing)
        if got is None:  # O(1)
            lo = mid + 1  # O(1)
        else:  # O(1)
            hi, best = mid, got  # O(1)
    return hi, best  # O(1)


def choose(ms, busy=(), ctx=None, cap=float("inf"), cp=()):  # O(A * packing)
    """Pick A* and batches. ms/busy/ctx in whole tenths; cap is context budget.

    Score is max(B + A, F/T). Returns (score, F, A, batches) where batches are
    task indices into ms, or None when nothing fits. No batch is empty.
    """
    n = len(ms)  # O(1)
    if n == 0:  # O(1)
        return None  # O(1)
    ctx = list(ctx) if ctx is not None else [0] * n  # O(n)
    busy = tuple(int(b) for b in busy if b)  # O(B)
    B = len(busy)  # O(1)
    T = max(list(ms) + list(busy))  # O(n + B)
    if T <= 0:  # O(1)
        T = 1  # O(1)
    solo = [i for i in range(n) if i in cp]  # O(n)
    rest = [i for i in range(n) if i not in cp]  # O(n)
    rm, rc = [ms[i] for i in rest], [ctx[i] for i in rest]  # O(n)
    fixed = max([ms[i] for i in solo] + list(busy), default=0)  # O(n + B)
    counts = [A for A in range(max(1, len(solo)), n + 1) if not (rest and A - len(solo) == 0)]  # O(n)
    upper = {}  # O(1)
    for A in counts:  # O(n)
        k = A - len(solo)  # O(1)
        r = lpt(rm, rc, k, cap) if k else (0, [])  # O(n*k)
        if r is not None:  # O(1)
            upper[A] = max(B + A, max(r[0], fixed) / T)  # O(1)
    if not upper:  # O(1)
        return None  # O(1)
    U = min(upper.values())  # O(n)

    def lower_score(A):  # O(n log n)
        k = A - len(solo)  # O(1)
        lb = 0  # O(1)
        if rm:  # O(1)
            srt = sorted(rm, reverse=True)  # O(n log n)
            lb = max(srt[0], -(-sum(rm) // k), srt[k - 1] + srt[k] if k < len(rm) else 0)  # O(n)
        return max(B + A, max(lb, fixed) / T)  # O(1)

    best = None  # O(1)
    for A in counts:  # O(n)
        if lower_score(A) > U + 1e-12:  # O(n log n)
            continue  # O(1)
        k = A - len(solo)  # O(1)
        if k == 0:  # O(1)
            r = (0, [])  # O(1)
        else:  # O(1)
            r = exact_split(rm, rc, k, cap)  # O(packing)
        if r is None:  # O(1)
            continue  # O(1)
        F = max(r[0], fixed)  # O(1)
        score = max(B + A, F / T)  # O(1)
        groups = [[rest[i] for i in b] for b in r[1] if b] + [[i] for i in solo]  # O(n)
        groups = [g for g in groups if g]  # O(n)
        cand = (score, F, A, groups)  # O(1)
        if best is None or (score, F, A) < (best[0], best[1], best[2]):  # O(1)
            best = cand  # O(1)
    return best  # O(1)


def brute(ms, busy=(), ctx=None, cap=float("inf"), cp=()):  # O(A^n)
    """Reference: try every non-empty assignment of n tasks onto A labels."""
    n = len(ms)  # O(1)
    ctx = list(ctx) if ctx is not None else [0] * n  # O(n)
    busy = tuple(int(b) for b in busy if b)  # O(B)
    B, T = len(busy), max(list(ms) + list(busy) or [1])  # O(n + B)
    if T <= 0:  # O(1)
        T = 1  # O(1)
    best = None  # O(1)
    for A in range(1, n + 1):  # O(n)
        Fbest = None  # O(1)
        for assign in itertools.product(range(A), repeat=n):  # O(A^n)
            if len(set(assign)) != A:  # O(n)
                continue  # O(1)
            bins = [[i for i in range(n) if assign[i] == j] for j in range(A)]  # O(n)
            if any(sum(ctx[i] for i in b) > cap for b in bins):  # O(n)
                continue  # O(1)
            if any(i in cp and len(b) > 1 for b in bins for i in b):  # O(n)
                continue  # O(1)
            F = max([sum(ms[i] for i in b) for b in bins] + list(busy))  # O(n + B)
            Fbest = F if Fbest is None else min(Fbest, F)  # O(1)
        if Fbest is None:  # O(1)
            continue  # O(1)
        score = max(B + A, Fbest / T)  # O(1)
        if best is None or (score, Fbest, A) < (best[0], best[1], best[2]):  # O(1)
            best = (score, Fbest, A)  # O(1)
    return best  # O(1)
