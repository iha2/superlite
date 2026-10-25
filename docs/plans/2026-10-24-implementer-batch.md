# Integration: implementer batches from one exact formula

**Goal:** Port the approved prototype into the plugin so dispatch, guards, and instruction tests match the design. Formula is \(A^*=\arg\min\max(B+A,F/T)\) (ties: smaller \(F\), then smaller \(A\)).
**Architecture:** `split.choose` replaces capped batching; state files feed three coordinator guards; live tests prove agent rules.
**Tech stack:** Python, bash hooks, pytest
**Spec:** docs/plans/2026-10-24-implementer-batch.feature.md
**Detail:** tests
**Target-min:** 40
**Review plan:** docs/plans/2026-10-24-implementer-batch.feature.md
**Design:** docs/plans/2026-10-24-implementer-batch.review.md
**Objective decision:** docs/specs/2026-10-25-implementer-batch-objective.md

## Global constraints

- language: python
- FROZEN: `sl dispatch` stdout line format
- FROZEN: implementer reply statuses
- FROZEN: existing three coordinator hook rules (deny format)
- Port from prototype; no prototype stubs in Owns
- Ruling: AI orchestrator for try-out and integration (user 2026-10-24)
- Ruling: prototype built inline (keep-set, one chain)
- Ruling: keep max(B+A, F/T); finish-first raised wall/cost on sitegen three-rep avg (2026-10-25)

Ported from prototype: `split.py`, dispatch wiring, batch guard, implementer.md batch/question rules, live runner + two tests.

Integration adds: question/tier guards, checkpoint question/blocked state, Wave 0 / STUB UPDATE, compaction 30 min, haiku + SKILL prose, routing lifetime 20.
