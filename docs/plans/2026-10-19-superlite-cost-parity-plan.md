# Plan: bring superlite's cost to or below superpowers

## Context
Superlite's latest run (k1, 16.1 min) cost $9.38. Superpowers on the same task averages $7.10 (inline $6.33, orchestrated $7.62; outlier run excluded). Opus spend is already at parity ($4.01 vs $4.18); the gap is Sonnet ($5.35 vs $2.92), mostly the per-agent overhead of 21 dispatches, each writing its own 26 KB prime to the cache. Full numbers: `C:\Work\Planning\Specs6-10-19-superlite-cost-baseline.md`.

**Design rule: k1 is the reference. Its shape stays.** k1 is the best run so far (16.1 min, 177/177, the current stack): a plan of 17 tasks inside the size band, Wave 0 skeleton on Haiku, a ready-queue over up to 18 agents, package reviewers, a packet-only final review. Every change below cuts the cost of that shape (what each agent carries, who does the mechanical work, how often the coordinator wakes). None changes the task graph, the size band or the parallelism. Merging tasks (the older b1 build did 6) is not in this plan; it is only an experiment, run after the rest is measured and only if the cost target is still missed.

Target: mean cost over at least 3 superlite reps of $7.10 or less, with 177/177 hidden tests, wall-clock at or under about 16 min, and no drop in first-time pass rate.

## Workspace
New git worktree off main via EnterWorktree (name `cost-parity`), on its own branch. Nothing is committed without the user's yes; no Claude trailer; no push. The benchmark harness runs `--plugin-dir` against the worktree.

## Steps (each one measured before the next)

1. **Instrument first (no behavior change).**
   - `bench/` : add a `cost_by_role` block to the harness output (coordinator planning vs loop, implementers, reviewers, final, fixers) from `dispatches` + `model_usage`; reuse `taskmetrics.cost_of`.
   - `skills/subagent-driven-development/scripts/predict.py`: charge each task its prime and context writes so the forecast stops undercounting by about 2x (calibrate on `~/.superlite/history.jsonl`, 18 rows; test in `tests/scripts/test_metrics_history.py`).
   - `report.py`: add the `cost_by_role` line (projected vs actual) next to the existing cost split.

2. **Slim Sonnet prime (lever 2). Estimated saving on k1: about $0.9 to 1.1 (the prime cost $1.17: 16 implementers wrote about 15k tokens each on their READY turn, then re-read it).**
   - Staleness fix, found in k1: the prime was built at 16:55:29 while T7, T15 and T16 were still `FIX`; the plan was patched at 16:57 and the prime was never rebuilt, so those three agents read the pre-patch plan. Build the slim prime so it holds only what cannot go stale (rules, constraints, interface lines of STABLE tasks), and send each agent its own current task text in the task message, generated from the plan at dispatch time.
   - `scripts/build_prime.py`: make the Sonnet prime preamble + constraints + Produces/Consumes lines of all tasks (the mini prime path `build_mini` already does this for one tier); drop the whole plan and the spec sections. The task message and the agent's own SPEC reads carry the rest.
   - `config/routing.json`: sonnet tier `prime` from `full` to a new `slim` value; keep `full` selectable.
   - Tests: `test_routing.py`, `test_run_scripts.py` (prime hash, size cap).

3. **More Haiku inside k1's graph (lever 3).** Only tasks k1 already has as small leaf tasks (for example T7 sitegen cli, T14 csvio, T8 refs) are candidates; no new tasks.
   - `skills/writing-plans/SKILL.md` line 36 and the `Kind` table: stronger guidance to split out mechanical pieces (tables, fixtures, thin CLI and config wrappers, docs, plain adapters) as separate `Kind: mechanical` tasks of 100 LOC or less.
   - Keep the routing rule and `stats_gate` (80% first-time pass) so Haiku turns itself off if it misses; never on the critical path.
   - Evidence: run `bench/mechanical_ab.py` (3 reps) on three more mechanical task shapes before widening beyond 100 LOC.
   - Try a Haiku reviewer-triage pass and Haiku digests of long coordinator reads only as a separate experiment, not in this change.

   Findings from the Haiku review (agent read the k1 data and router code):
   - k1 tagged 0 of 17 tasks `Kind: mechanical`, so the router sent none to Haiku. Tagging in the plan is the blocker.
   - Add a slack gate: `min_slack_s` 120 exists only in `bench/routing_evidence.py`, not in `routing.json` or the router; add a `slack_s_gte` key to `routing.matches` (history.py near line 139 already computes slack). Today ties on the critical path pass, so T4 and T8 (slack 0) would be eligible.
   - `ready_tasks` sorts longest-first and truncates to `cap - running` (SKILL.md uses `--cap 10`): exempt the Haiku lane or sort it first; the coordinator emits the Haiku Agent calls first.
   - Candidates in k1 (slack at least 120 s, 1 to 2 components, fast tests): T14 (100 LOC, slack 196 s, $0.224 on Sonnet), T5 (110 LOC, 282 s, $0.132), T9 (120 LOC, 274 s, $0.166). Estimated saving $0.20 (T14 only) to $0.47 (all three); about $1.16 if tasks up to 200 LOC went to Haiku (no evidence there).
   - Added time is hidden by slack: about +10 to 17 s per task, extrapolated from T8 (72 s to 78.7 s), against 196 to 282 s of slack. A Haiku miss costs about 100 s of fixer time, still under the slack.
   - Sonnet itself passed first time on only 11 of 15 observed k1 tasks, so small does not mean safe. Widen beyond 100 LOC only after about 10 passes with at most 1 miss; drop a shape after 2 first-time misses.
   - Digest agents (under $0.1), reviewer triage (at most $0.23, no data), docs and finish chores (under $0.05) and relevance polls ($0) are not worth building now.
   - Experiment: replay routing on the k1 plan with T5, T9, T14 retagged; `mechanical_ab.py` 3 reps on 3 new shapes (about $0.5); then one superlite rep with those three on Haiku, judged on per-task cost, wall-clock vs 16.1 min and 177/177.
4. **Coordinator turns (lever 5).** Check `sl dispatch` and the SKILL.md loop for wake-ups that can be batched; measure the call count in a rep before changing anything. Skip if the saving is under $0.3.

5. **Optional experiments, none changes the shape:** (a) `FORCE_PROMPT_CACHING_5M=1` for one rep of each arm; (b) Sonnet session with an Opus planner agent; (c) last resort, only if still above target: a task-count target of about 9 (merging), compared against k1 for time and cost.

## Rep s1 (ts-sitegen, slim prime), stopped at 17 min, not scored
Prime for that task: 5,048 bytes against 18,364 in f1; first-call cache writes about 9k tokens per implementer. No cost or time comparison is valid. Two bugs found, neither caused by the prime:
1. A test run hung about 7.4 min (`tests/markdown.test.ts`, four stuck vitest processes, fast suite 442.8 s, exit code 4294967295). Cause not yet found.
2. **Git-ignore ordering bug.** Wave 0 (Haiku) ran `npm install` at 18:49:51 but wrote `.gitignore` at 18:51:38; T4's scope check at 18:50:43 saw node_modules as untracked and logged 1,292 "change outside Owns" incidents (1,362 incident lines against 2 in f1). Fix, in progress:
   - `config/gitignore.json`: per-language ignore lists plus common entries; `wave0.py` appends the missing entries to any existing `.gitignore` (append-only, idempotent) before any agent runs, detected from the plan's language.
   - The planner states project-specific generated paths in an optional plan header line `**Ignore:**`; the script merges it with the language template. `lint` prints a NOTE if absent.
   - Every implementer sees the current `.gitignore` in its slim prime (no tool call) and must report, not edit, a generated folder that is not ignored.
   - `commit_task.py` never blocks on those folders (union list), and groups out-of-scope incidents one row per folder.
Next: rerun ts-sitegen (f1 is the reference: 8.5 min, $3.39, 132/132) once the hang is understood.

## Keep green
`python -m pytest tests/scripts -q`; caps: using-superlite SKILL.md at most 2000 chars, sdd/SKILL.md at most 11600; plan at most 15k chars; language-agnostic (no Python assumptions in prime or routing).

## Verification
1. After steps 1 to 3 (instrument, slim prime, Haiku): unit tests green, then 3 superlite reps via `bench/run_combo.sh` (one at a time, stop and analyze at 15 min per the benchmark protocol) against the same SPEC.
2. Compare to the baseline table: cost mean at or below $7.10, hidden 177/177, wall-clock at or under 16 min, first-time pass rate and robustness probes not worse than 4 of 7.
3. Step 3 judged on its own reps: Haiku share of tasks, Haiku first-time pass, cost per task.
4. If a lever raises cost or drops quality it is reverted; the result goes into the cost baseline doc.

## Risks
- One rep has about $2 of noise; every claim needs 3 reps.
- Every claim is measured against k1 (cost, 16.1 min, 177/177); a change that moves k1's time or pass rate the wrong way is reverted.
- Slim prime could cost agents context; first-time pass rate is the check.
- Haiku evidence is one task shape.
