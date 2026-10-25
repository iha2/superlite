# Prototype try-out: implementer-batch

Recorded: 2026-10-25T03:27:10.732499+00:00
Orchestrator: AI

## Free suite (proto keep-set)
- `pytest tests/scripts/test_split.py tests/scripts/test_batching.py tests/scripts/test_block_coordinator_edits.py`: **72 passed**
- Scenario table, brute-force 300, 60-task timing, five equal -> 2/2/1, batch guard: **pass**

## Live instruction tests
- `pytest tests/live -m live`: **2 passed** (BLOCKED continues; NEEDS_CONTEXT at once)
- Recent costs (usd): see `.superlite/instruction-test-costs.jsonl`

## Sitegen-core smoke (plugin load)
- arm=superlite rep=p1-smoke wall=13.2s cost=$0.23 completed_marker=True

## Sitegen-core small build (BENCH_BUDGET=20)
- arm=superlite rep=p2 wall=580.8s cost=$3.99
- hidden: 56/56
- implementer_dispatches=3 agent_dispatches=16
- orchestrated=True src_loc=659 last_subtype=success
- own_tests: 122 passed in 0.09s

## Verdict
Prototype keep-set works. Sitegen smoke loaded the plugin. Capped sitegen build recorded above for the AI try-out.
