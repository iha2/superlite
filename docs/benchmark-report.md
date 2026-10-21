# Benchmark report: superpowers 6.4.1 vs superlite 0.1.0 (the version benchmarked; current is 1.5.0)

**Date:** 2026-10-16 (corrected 2026-10-17) · **Task:** build `sitegen` from `bench/task/SPEC.md` (9 modules, stdlib Python) · **Reps:** 1 per arm

> **Correction (2026-10-17):** the first version of this report summed every `result` event in each log. A headless session emits a result each time its coordinator idles while background agents run, and every one of them repeats the session's cumulative cost. That inflated costs 3× (superpowers) and 9× (superlite), and it flipped the cost conclusion. The harness now keeps the last result per session. The figures below are corrected.

## Results

| | superpowers 6.4.1 | superlite 0.1.0 | superlite vs superpowers |
|---|---|---|---|
| Wall-clock | 34.4 min | **8.8 min** | **3.9× faster** |
| Cost (API-equivalent) | $8.15 | **$3.28** | **60% cheaper** |
| Opus / Sonnet / Haiku | $6.43 / $1.72 / none | $1.53 / $1.75 / $0.002 | Opus −76% |
| Hidden acceptance tests | 89 / 89 | 89 / 89 | same |
| Own tests written | 298 | 208 | −30% |
| Stops-for-human | 0 | 0 | same |
| Coordinator turns | 68 | 46 | −32% |
| Coordinator context per request (median / max) | 108k / 154k | 70k / 90k | −35% |
| Subagent dispatches | 21 (16 Sonnet, 5 Opus), strictly serial | 10 (8 Sonnet, 2 Haiku), up to 5 concurrent | serial chain 21 → 3 |
| Agent-busy wall time | 25.1 min | 4.8 min | |

## Phase timeline (minutes from start)

| Phase | superpowers | superlite |
|---|---|---|
| Design / spec | 0–1.2 (9.2k-char design doc) | none (pre-approved) |
| Plan | 1.2–1.7 (9.0k chars) | 0.7–1.7 (15.4k chars) |
| Setup | 1.7–2.5 | 1.7–1.95 |
| Implementation | 2.5–25.6: T1…T8 serial, each with a review; 3 fix rounds | 1.95–5.4: wave 1 (5 parallel), then T6 |
| Reviews during build | 8 task reviews + 3 re-reviews ≈ 12 min | 1 wave review + 2 Haiku test runs |
| Final review | 25.9–31.4 (Opus, 5.5 min), fix wave + re-review to 33.4 | 6.0–7.7 (Sonnet) |
| Finish | 33.5–34.2 | 7.85–8.64 |

## What the numbers say

1. **Parallel waves drive the speed.** The serial chain fell from 21 dispatches to 3 levels. Superpowers' review-after-every-task loop alone was about 12 minutes.
2. **Planning is not the bottleneck.** Both arms spent about one minute writing their plans (about 5k Opus output tokens each). Superlite's contract-style plan was actually longer. Taking code out of plans did not save time on this task.
3. **Opus subagents cost time and money, in proportion to how they're used.** Superpowers' 5 Opus dispatches (2 implementers, 2 task reviews, the final review) took 15.8 minutes, 58% of agent time. The final review was 5.5 minutes of that.
4. **A smaller coordinator context is the main cost lever.** Superlite's Opus read 2.3M cached tokens against 8.7M for superpowers: fewer turns, at a smaller median context.
5. **Parallel siblings didn't share a cache.** All 5 wave-1 agents wrote their own about 18k-token prefix, and none read a sibling's. That cost about $0.27 here. It grows with longer shared briefs.

## Caveats

- **n = 1 per arm.** Run-to-run variance (mostly review loops) could move the timings ±30%.
- **The arms overlapped.** Superpowers ran 02:38–03:12, and superlite ran 03:00–03:09, entirely within that window, sharing rate limits.
- **Costs are API-equivalent** (`total_cost_usd`, which includes subagents). On a subscription they show up as usage-limit consumption.
- **Same environment for both arms:** Opus coordinator, `--permission-mode auto`, the user-level orchestrator skill disabled, and the user's Sonnet subagent default and Haiku Explore/test-runner overrides active.
- The hidden suite checks SPEC conformance, not robustness beyond it. Both arms hit the ceiling, so it doesn't measure the value of Opus review or of extra tests.

## Next steps

See `specs/2026-10-17-superlite-architecture-design.md` (the design for superlite 0.2). In short:
- Keep the Opus final review and optional code in plans, and measure both.
- Share caches across siblings: a common brief prefix, warm-then-fan-out, and the 1-hour subagent TTL.
- Add a harder benchmark task where review quality can show.
- Run 3 reps per arm, sequentially.
