# Plan: coordinator health signals and escalation counting

## Context
Goal: know when a cheaper coordinator (Sonnet loop agent) is struggling, escalate on facts, and count every escalation. Background and cost reasoning: `Specs/2026-10-19-coordinator-model-decision.md` (sections 5, 6, 6.1) and `Plans/2026-10-19-superlite-cost-parity-plan.md`. This plan builds the detection and the counting only. It does not build the Sonnet loop agent, the planner or the advisor agents, or the takeover; those wait for the hook decision and one rep.

**Design rule: two layers. The model's own report is primary; files and scripts are the backup.** The coordinator is told to escalate when it is unsure and does so through `sl escalate` (step 4). `sl health` is the safety net for when it does not report: a loop it does not notice, state it lost track of, a rule it keeps breaking, or a ruling it is overconfident about. A weaker model overrates itself, so the backup cannot be dropped; but it should rarely be the first to fire, and the counts show which layer caught what (trigger `self` against `health`). `sl health` reads the plan, ledger, `pool.json`, `stable.json`, `incidents.jsonl`, `metrics.jsonl` and `lint-log.jsonl`. It works the same for today's Opus coordinator, so it is useful before any model change and gives the baseline that the Sonnet rep is compared against.

The signals (from the review):

| # | Signal | Source | Exists today? |
|---|---|---|---|
| 1 | Same `sl` command fails twice in a row | `PostToolUseFailure` hook on Bash | no: new hook |
| 2 | 2 or more `rule-block` incidents | incidents | yes (k1 had 2) |
| 3 | Stall: ready tasks, idle pool, no dispatch for N min | lint-log, metrics, pool | partly (`idle_gaps_s`, report-lag in the bench harness only) |
| 4 | Ledger drift: `pool.json` busy with nothing running, DONE but uncommitted, `FROZEN:` | pool, ledger, metrics launch/stop events | no: new check |
| 5 | FIX tasks left after the second patch round; unresolved `MISSED TARGET` | lint-log | yes |
| 6 | Seam mismatches (2 or more), tier-3 fix, 2 failed rounds at one tier | incidents, `pt_review` | yes |
| 7 | Calls per committed task far above normal; coordinator 40% of cost | metrics, `budget.py` hint | hint exists, ratio does not |

## Workspace
New git worktree off main via EnterWorktree (name `coord-health`), own branch. Nothing is committed without the user's yes; no Claude trailer; no push. The cost session has uncommitted edits in the main checkout (`routing.json`, `build_prime.py`, `routing.py`, `task.py`, `sl`, SDD `SKILL.md`, tests), so a worktree cut from main does not include them: keep this plan's changes to new files plus small edits in `incident.py`, `hooks.json`, `report.py`, `dispatch.py` and `plan_tools.py` (printing only); merge order with the cost branch is for the user to decide, and `report.py` is the likely conflict (the cost plan adds `cost_by_role` there).

## Steps (each tested before the next)

1. **Replay first (no repo behavior change).** `bench/health_replay.py`: compute signals 2, 3, 5, 6, 7 offline from `bench/results/combo-superlite-{k1,v2,v3,w3}.jsonl` and their `.json` (tool calls, incidents, seam counts, call counts). Output: one table per run. This gives the first distribution (n=4 runs, one on the current stack) and shows whether the thresholds below would have raised false alarms on k1 (clean, expected `ok`) or caught v3 (9 of 9 seam mismatches, 117 incidents) and w3. Thresholds are set from this table, not guessed.

2. **`sl health`.**
   - `scripts/health.py` (stdlib, language-agnostic): `health.py --plan P [--dir .superlite] [--changed] [--json]`. Prints `health: ok`, `health: WARN <signal, one line each>`, or `health: ESCALATE <why>` when 2 warnings are open or a single hard one (a repeated failure, a ledger drift that survived one `dispatch`).
   - New data file `config/health.json` (like `routing.json`): `stall_min`, `repeat_fail`, `rule_blocks`, `fix_after_rounds`, `seam_mismatches`, `failed_rounds`, `calls_per_task`. Starting values from step 1.
   - Stall rule must not fire while the human orchestrator awaits approval (`orchestrator.json`), nor while only long-running tasks are in flight; it fires only when ready tasks exist, the pool has an idle agent and nothing was dispatched.
   - Print it from `dispatch` and `lint --stable` with `--changed`, the same pattern as `budget.py` (state in `.superlite/health.state`).
   - Tests: `tests/scripts/test_health.py`, one fixture per signal, plus "clean run prints `ok`".

3. **Repeat-failure hook (signal 1).** `hooks/capture-bash-failure` on `PostToolUseFailure` with matcher Bash, so it runs only on failures (a hook on every `PostToolUse` costs about 340 ms per call, across hundreds of agent calls). It appends `{t, agent_id, tool, args_hash}` to `.superlite/failures.jsonl`; `health` reads the last entries for the loop's own calls. Register in `hooks/hooks.json`. Tests in `test_capture_hooks.py`. Check first that `PostToolUseFailure` fires inside subagents and carries `agent_id`.

4. **`sl escalate` and counting.**
   - `incident.py` `KINDS` is a fixed tuple: add `coordinator-escalation`.
   - `scripts/escalate.py`: `escalate --kind advisor|planner|takeover --trigger self|health|type --reason TEXT [--task Tn] [--agent ID]` writes the incident with level, trigger, task, agent id and outcome (`resolved|overruled|no-change`, set by a later `escalate --close ID --outcome …`).
   - Cost per escalation: join the agent id to its stop event in `metrics.jsonl` (existing capture hooks), so no new cost accounting.
   - Cap: 3 advisor or planner consults per run; `escalate` refuses a 4th and says why.
   - `report.py` adds one line: `escalations: 3 (2 advisor, 1 planner, 0 takeover) $0.19, self 1 / health 1 / type 1, overruled 1 of 3`; the bench harness adds the same fields to its output.
   - Tests: `test_incident.py`, `test_report.py`, new `test_escalate.py`.

5. **Guards that skip subagents (needed only for the Sonnet loop agent).** `block-coordinator-edits` and `rewrite-test-runs` exit 0 for any call that carries an `agent_id`. Add a `coordinator_agent_types` list (empty by default, so nothing changes today) read from config; calls from those agent types get the main-session rules. Tests in `test_block_coordinator_edits.py` and `test_rewrite_test_runs.py`. Ship dormant; it is switched on by the loop-agent work.

6. **Wording, within the size caps.** `subagent-driven-development/SKILL.md` is at its 11,600-character cap, so the text goes in a new `skills/subagent-driven-development/escalation.md` (read when `health` prints WARN or ESCALATE) plus a one-line pointer that replaces an existing line. It states: read the `health:` line each round; on ESCALATE or when unsure run `sl escalate` and ask; never guess a ruling with a real "cost if wrong". `using-superlite` (cap 2,000 chars) is untouched. Docs: `docs/tools.md`, `docs/how-a-run-works.md`.

7. **Observe-only, then act.** Run `health` in print-only mode for the next 3 superlite reps (the cost plan's reps; no extra runs). Escalation actions stay off until the signals have been compared with what happened. Turning actions on, with a Sonnet loop agent, is a separate decision.

## Keep green
`python -m pytest tests/scripts -q`; cap tests (using-superlite at most 2000 chars, sdd/SKILL.md at most 11600); no Python assumptions in anything a target project sees (health reads superlite's own files only); each new script has a one-line header and complexity notes like its siblings.

## Verification
1. Step 1 table reviewed by the user before thresholds are fixed. k1 expected `ok`; v3 and w3 expected to warn on seam mismatches and incidents.
2. Unit tests green; `health` prints one short line (under 3 lines even with warnings) so it adds under 100 tokens per round to the coordinator context.
3. Hook cost: `capture-bash-failure` adds nothing on success paths; measured on one rep.
4. After 3 observe-only reps: for each warning, did trouble follow? Count false alarms and misses; adjust `health.json`.
5. A change that adds coordinator calls or a hook that slows successful calls is reverted.

## Risks
- Thresholds rest on 4 replayable runs, only one on the current stack; treat all of them as provisional.
- A stall check can fire during legitimate waits (human approval, one long task): the conditions above guard it, and observe-only mode shows false alarms before any action.
- Ledger-drift detection depends on the capture hooks' launch and stop events; if a run lacks them, that signal reports `unknown`, not `ok`.
- `PostToolUseFailure` behavior inside nested subagents is not confirmed in the docs; step 3 starts with a 5-minute check.
- Counting escalations proves nothing about quality alone; `overruled` and the offline replay of rulings to Opus (decision note, section 6) are the quality check.
- Over-escalation costs money (each advisor consult about $0.05 to 0.12): the cap of 3 and the counts are the control.

## Not measured
Everything above is design. No run has a Sonnet coordinator, and the "normal" range for each signal comes from at most 4 runs.
