# Implementer batch objective: keep `max(B+A, F/T)`

Date: 2026-10-25. Decision after A/B on `sitegen-core`. Design authority for the formula lives in `docs/plans/2026-10-24-implementer-batch.review.md`. Code: `skills/subagent-driven-development/scripts/split.py` (`choose`).

## Decision

**Ship \(A^*=\arg\min\max(B+A,\,F(A)/T)\),** with ties to smaller \(F\), then smaller \(A\). Do **not** use finish-first \(A^*=\arg\min(F(A),\,A)\) as the default packing objective.

Equal tasks land near \(\sqrt{n}\) implementers (five of equal length → three agents sized 2/2/1). Critical-path tasks still run alone; context budget still caps a batch; busy implementers still count.

## Why finish-first looked attractive

Finish-first minimizes the predicted build-wave finish \(F\), then packs as densely as that finish allows. On paper it shortens the parallel build span and uses more implementers when lengths are equal (one task each). A single early sitegen run (`ff1`) did show a shorter build span than the prior `max(B+A, F/T)` run (`p2`), which raised the question of whether more parallelism would win wall clock.

## What we measured

Same task `sitegen-core`, arm `superlite`, `BENCH_BUDGET=20`, preapproved. Three fresh reps per objective after the code matched that objective.

| Objective | reps | avg wall_s | avg cost_usd | avg implementer_dispatches | avg build_span_s | avg tail_after_build_s | avg post_final_s | hidden |
|---|---|---:|---:|---:|---:|---:|---:|---|
| \(\max(B+A, F/T)\) | p3–p5 | **687.8** | **4.0** | **5** | 260.3 | **185.1** | **67.8** | 56/56 ×3 |
| finish-first \(\arg\min(F,A)\) | ff2–ff4 | 864.0 | 5.1 | 6 | 237.4 | 383.7 | 228.3 | 56/56 ×3 |

Raw tables: `bench/results/implementer-batch-sitegen-p3-5-avg.md`, `bench/results/implementer-batch-sitegen-ff2-4-avg.md`. Earlier single-run compare: `bench/results/implementer-batch-sitegen-ff1-compare.md`.

## Reading

- **Wall and cost favor \(\max(B+A, F/T)\):** about 20% less wall (~688s vs ~864s) and about $1 less (~$4.0 vs ~$5.1) on the same hidden quality.
- **Finish-first did pack harder** (always 6 implementer dispatches vs ~5) and sometimes shortened the build span, but **post-build and post-final tails grew** (~384s / ~228s vs ~185s / ~68s). Overall wall lost there, not in the packing math for the build wave alone.
- **First implementer start** was similar (~231s vs ~233s); late start is not what separates the two averages.
- **Variance:** finish-first had a heavy outlier (ff3 ~1180s wall, ~631s tail). The p2-formula range was tighter (582–753s).
- **No evidence** in this set that more implementers reliably reduce post-final probe/fix work; the denser pack correlated with longer tails.

## Ruling kept

`Ruling: keep max(B+A, F/T) as the dispatch objective - finish-first shortened some build waves but raised wall and cost on three-rep sitegen averages - cost if wrong: leave parallelism on the table for tasks where post-build work is negligible.`

Finish-first remains a documented alternative in the review plan's "Decisions you can change" list, not the default.

## Pointers

- Formula, scenarios, pseudocode: `docs/plans/2026-10-24-implementer-batch.review.md`
- Feature acceptance (five equal → 2/2/1): `docs/plans/2026-10-24-implementer-batch.feature.md`
- Coordinator dispatch wording: `skills/subagent-driven-development/SKILL.md`
