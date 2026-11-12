---
title: Benchmark
description: superlite 0.1.0 against superpowers 6.4.1 on one build task.
sidebar:
  order: 1
---

| Metric | superpowers 6.4.1 | superlite 0.1.0 |
|---|---|---|
| Wall-clock | 34.4 min | 8.8 min (3.9x faster) |
| Cost (API-equivalent) | $8.15 | $3.28 (60% cheaper) |
| Opus / Sonnet / Haiku cost | $6.43 / $1.72 / none | $1.53 / $1.75 / $0.002 |
| Hidden acceptance tests | 89 / 89 | 89 / 89 |
| Own tests written | 298 | 208 |
| Stops-for-human | 0 | 0 |
| Coordinator turns | 68 | 46 |
| Context per request (median / max) | 108k / 154k | 70k / 90k |
| Subagent dispatches | 21, strictly serial | 10, up to 5 concurrent |
| Agent-busy wall time | 25.1 min | 4.8 min |

The task: build `sitegen` from `bench/task/SPEC.md` (9 modules, stdlib Python), run on 2026-10-16 and corrected on 2026-10-17. The correction fixed a bug that summed every `result` event and inflated costs 3x and 9x.

## What the numbers say

Parallel waves drive the speed. The serial chain fell from 21 dispatches to 3 levels. Superpowers' review after every task took about 12 minutes. Planning took about one minute in both arms. Superlite's Opus read 2.3M cached tokens against 8.7M.

## Caveats

There is one run per arm, so timings could move by 30%. The arms overlapped in time and shared rate limits. Costs are API-equivalent. The hidden suite checks the SPEC only, so it does not measure review quality.
