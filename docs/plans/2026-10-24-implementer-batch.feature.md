# Feature: Implementer batches from one exact formula

**Review plan (approved 2026-10-24 17:39 by the human):** `docs/plans/2026-10-24-implementer-batch.review.md`. It holds the formula, the pseudocode, the scenario table, and every decision. This feature plan does not restate them.

## Goal

Dispatch picks how many implementers run and which ready tasks each one holds from one exact formula over predicted task minutes, with busy implementers counted. Implementers carry a batch to the end, raise questions at once, and resume after the answer. Wave 0 starts alongside lint. Small live instruction tests prove the agents follow the new rules, for about a cent each.

Who it is for: anyone running a superlite build. A run finishes as soon as the predicted long pole allows, then packs as many tasks per implementer as that finish allows; no agent sits idle with no task.

## What changes

**cli (the `sl` tools)**

- `sl dispatch` prints batches chosen by \(A^*=\arg\min\max(B+A,F/T)\) (ties: smaller \(F\), then smaller \(A\)), not by a fixed cap of 4 implementers with a fifth past 16 tasks. Task minutes come from the predictor in whole tenths of a minute. Critical-path tasks run alone, the context budget per implementer is kept, and idle implementers are reused first.
- `sl dispatch` counts busy implementers and their remaining minutes.
- `plan_tools lint --stable` no longer prints Wave 0 when Wave 0 was already sent. It names any Produces line a lint fix changed.
- `sl wave0 --plan` is run when the machine plan is written, and its agent is sent in the same message as `lint --stable`.

**Agent instructions (other)**

- `agents/implementer.md` and `agents/implementer-haiku.md`: a batch of any size, carry on after a failed task, skip the tasks whose tests run its code, reply NEEDS_CONTEXT at once with done and unstarted tasks, and build the answered task first.
- `skills/subagent-driven-development/SKILL.md`: how the count is chosen, Wave 0 alongside lint, always answer a question (with a `Ruling:` line when the plan is silent), BLOCKED goes up a tier, compact every 30 minutes.

**Hooks (other)**

- `hooks/block-coordinator-edits` gains the batch guard, the question guard, and the tier guard.

**Tests (library)**

- Script checks for the formula, the exact split, and every scenario row.
- Free guard tests, and live instruction tests: one rule, one short agent call, three outcomes (Followed, Guarded, Wrong), with cost shown.

## Acceptance checks

1. `pytest tests/scripts -q` passes with no live tests run.
2. The scenario table in the review plan is reproduced by a test: every row gives its implementer count, its batches, and its finish.
3. On 300 random lists of up to 6 tasks, with busy implementers, context budgets, and critical-path tasks, the chosen count, finish, and score equal those from trying every split. No chosen implementer is ever empty.
4. The exact split of 60 tasks of mixed minutes takes under one second.
5. `sl dispatch` on a fixture plan with five equal ready tasks prints three implementers sized 2/2/1.
6. `lint --stable` after `sl wave0` was sent prints no START FIRST line. A Produces line changed by a lint fix is named.
7. Each guard denies its wrong call, names the right move in the reason, and lets the right call pass, without a model.
8. `pytest -m live` runs the twelve instruction tests. Each prints Followed, Guarded, or Wrong with seconds and dollars, stays within 30 seconds and $0.02, and the whole run stays under $0.15. A test whose instruction file is unchanged since its last pass is skipped.
9. Each instruction test fails on the instructions from `main` before this change and ends Followed after it.
10. One component, 120 lines, slow tests, and Sonnet give 2.354 minutes.

## Code map

**Entry / how this repo stands up**

- Scripts live in `skills/subagent-driven-development/scripts/` and run as `sl <name>`. Tests: `pytest tests/scripts` (`pyproject.toml` sets that as `testpaths`; there are no markers today). CI also runs `tests/bench`.
- Hooks are bash in `hooks/`. Tests launch them with Git's `bash.exe` through the `_GIT_BASH` pattern in `tests/scripts/test_human_gate.py`.
- Single-agent live runs already exist in `bench/mechanical_ab.py`. `agent_def` turns `agents/<name>.md` into a `--agents` entry. `claude_cmd` runs `claude -p` with `--max-budget-usd` and turns superpowers off. `execute` reads `total_cost_usd`, `modelUsage` and the `tool_use` events from the last `result`. `harness.arm_env` and `harness.bash_exe` are reusable.

**Files this feature touches, and the seam**

| File | Seam |
|---|---|
| `scripts/dispatch.py` | `make_batches` (lines 76–107) is replaced by a call to the new split. `plan_dispatch` (lines 114–201) keeps `asg.eligibility`, `score`, `decide`, the lane reuse, and the `render` output format. It writes `.superlite/dispatch.json` with the batches it printed. |
| `scripts/split.py` (new) | `exact_split`, `fits`, `lpt`, and `choose(minutes, busy_left, ctx, cap, cp)`, as in the review plan's pseudocode. |
| `scripts/next_tasks.py` | `ready_tasks` (lines 301–312). The `cap - len(running)` slot limit stays as the upper bound for A. |
| `scripts/predict.py`, `plan_tools.effective_times` (lines 490–507) | Dispatch reads predicted minutes through `effective_times`, rounded to tenths. |
| `scripts/budget.py` (lines 62–78), `metrics.jsonl` | Each busy implementer's remaining minutes. |
| `config/routing.json` | The `batch` block (`max_tasks 3`, `capacity`) is retired. `critical_path_solo` and the tier `max_context_k` stay. |
| `scripts/plan_tools.py` `cmd_lint_stable` (lines 1397–1519) | It already skips START FIRST when `briefs/wave0.md` exists (lines 1476–1487). It also prints `STUB UPDATE:` for a Produces change found by the existing `interfaces` comparison (lines 1417–1440). |
| `scripts/wave0.py` | Unchanged. The coordinator runs it right after the plan is written. |
| `scripts/compact_due.py` | `window_s` changes from 2280 to 1800. |
| `agents/implementer.md`, `agents/implementer-haiku.md` | Lines 20 and 25–31: any batch size, carry on after BLOCKED, skip the tasks whose tests depend on the failure, NEEDS_CONTEXT at once with done and unstarted lists, answered task first. |
| `skills/subagent-driven-development/SKILL.md`, `fixing.md` | Lines 20–21 (Wave 0 alongside lint), 48–51 (batches, always answer with a `Ruling:`, BLOCKED goes one tier up), 68–70, compaction. |
| `hooks/block-coordinator-edits` | A fourth rule on coordinator Agent/Task calls while a run is active. It reads `subagent_type` and the prompt, plus `dispatch.json`, `questions.json` and `blocked.json`. The deny path (lines 192–214) is reused. |
| `scripts/checkpoint.py` | `--question T` and `--answered T` write `questions.json`. `--blocked T --tier X` writes `blocked.json`. |
| `tests/scripts/test_batching.py`, `test_routing.py`, `test_wave0.py`, `test_block_coordinator_edits.py`, `test_compact_due.py`, `test_gitignore.py` (lines 218–222) | Updated for the new rules. Every existing assertion that does not mention the 3-task cap stays. |
| `tests/scripts/test_split.py`, `tests/live/` (new) | Scenario rows, brute-force agreement, and timing. `live` marker instruction tests with cost logging. |

**Interfaces that must not change (FROZEN)**

- The `sl dispatch` stdout line format (`Tn,Tm agent=ID`, `fresh tier=T agent=TYPE`, `poll`, `evict=` on stderr, the `T0 wave0` line, SWARM).
- The `pool.json` agent fields, the `stable.json` keys, `checkpoint.py` existing flags, and the `wave0.json` and `briefs/wave0.md` paths.
- The implementer reply statuses (DONE, DONE_WITH_CONCERNS, NEEDS_CONTEXT, BLOCKED) and the per-task reply blocks.
- The deny format in `block-coordinator-edits`, which is stderr with exit 2, plus the Cursor JSON `user_message`. Its existing three rules stay.

**Conventions / toolchain**

- LF line endings, Python 3 standard library only in scripts, pytest. Versions change only through `scripts/bump-version.sh`.
- This repo has no UI component set. The target kinds are cli (`sl`), library (scripts), and other (agent instructions and hooks).

## Prototype scope

**Keep:** `dispatch.py` with the new `split.py`, `effective_times` minutes, busy remaining minutes, and `dispatch.json`. The batch guard in `block-coordinator-edits`. The batch and question rules in `agents/implementer.md`. The live test runner with two instruction tests: the implementer carries on after a BLOCKED task, and it stops at once on a question.

**Stub:** the question and tier guards, Wave 0 alongside lint, the compaction change, the haiku implementer text, and the SKILL.md prose. The integration ports these; they are text and small rules that follow the same pattern.

**Level:** L1 (the scripts and hooks run on the worktree checkout, with no services).

**Run:** `python -m pytest tests/scripts/test_split.py tests/scripts/test_batching.py -q && python -m pytest tests/live -m live -q` inside the prototype worktree. The try-out shows `sl dispatch` on the five-equal-task fixture and the live tests' cost lines.

## Integration outline

1. Port `split.py`, the dispatch wiring, and `dispatch.json` from the prototype. Retire the `batch` cap in `routing.json`. Update `test_batching.py` and `test_routing.py`.
2. Lint: print `STUB UPDATE:` for changed Produces lines. Move the SKILL.md Wave 0 line to the plan-written step. Update `test_wave0.py`.
3. `checkpoint.py` question, answered, and blocked state. Add the question and tier guards next to the batch guard.
4. Instruction text for both implementers, SKILL.md, and fixing.md. Set compaction to 1800 seconds.
5. Ten more instruction tests, for twelve in total, plus the skip-if-unchanged hash and the cost log.
6. Seam check: the full `pytest tests/scripts tests/bench` run passes, the scenario table matches, and `pytest -m live` costs under $0.15.

## Rulings

- Ruling: the orchestrator is human (the user said so). The review plan was approved through `sl orchestrator approve` on 2026-10-24.
- Ruling: guards read small state files (`dispatch.json`, `questions.json`, `blocked.json`), because dispatch output and incidents are not persisted in a form a hook can match.
- Ruling: the old 4/5 batching draft is kept in `stash@{0}` for reference only. The approved formula replaces it.
