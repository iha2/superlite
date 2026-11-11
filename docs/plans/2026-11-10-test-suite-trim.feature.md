# Feature plan: trim the test suite to what guards the core

Status: draft, awaiting human approval. Orchestrator: human.
Session: `test-suite-trim` on `superlite/sessions/test-suite-trim` (base `main`, 7898ce8).

## Goal and who it is for

For the superlite maintainer. The suite has about 2,110 tests in 118 files. CI takes 4.4 min on Linux, 9.6 min on macOS (bash 3.2) and 22.2 min on Windows (Git Bash). Most of that time is subprocesses (bash hooks, `superlite-cli`) and a new git repo for every test. Many tests guard no behaviour: they grep markdown wording, restate a table, test the benchmark harness, or repeat a check another file already makes. The goal is a suite that runs faster and still guards the two core features:

1. **Agent orchestration**: plan lint and taskgraph, dispatch and routing, waves and the interface freeze, checkpoints and the test gate, the human/AI approval gate, the review packet, fixer tiers, and the hooks that guard the coordinator.
2. **Session and worktree management**: ensure, create, use, park, split, close, discard, archive, WIP, reconcile, adopt, Claude-created worktrees, the worktree home, and sync.

**Where the time goes (local run, WSL, 2,130 tests, 26 min).** The three `slow` files take 29% of it: `test_session_machine` 310 s (1 test), `test_promises` 78 s and `test_properties` 58 s. The next biggest are `test_session_ensure` (101 s, 61 tests), `test_probe_run` (68 s, 20), `test_human_gate` (53 s, 179 cases), `test_orchestrator` (45 s), `test_run_scripts` (44 s) and `test_flows` (40 s). Doc and lint scans cost more than expected: `test_doc_contract` 29 s and `test_name_scan` 28 s. 3 tests failed locally:
- `test_human_gate::test_latency` and `test_testprobe::test_run_watched_samples_only_after_the_start_threshold` are timing asserts that break on a slow disk.
- `test_session_machine` found a real counterexample: `ensure a`, `ensure b` with `--move yes` and stash, add an untracked file, then `discard a`, and the untracked token is lost. Either the invariant or `discard` is wrong. That is out of scope here, and I flag it separately.

Everything else (cost lines, compaction nudges, the research and MVP extras, reports, widgets, tail, graph) keeps a thin smoke layer, not exhaustive cases.

## What changes for the maintainer

- `uv run pytest tests/scripts` runs fewer tests and finishes faster. CI runs the same three jobs.
- `tests/bench/` leaves CI except `test_real_formats.py`, the only bench file that runs shipped scripts. The rest still runs with `uv run pytest tests/bench`.
- **`slow` tests move to a nightly job.** These are the Hypothesis state machines, `test_promises` and the real-hang `test_testprobe` cases. All three PR jobs run `-m "not slow"`. A new `.github/workflows/nightly.yml` (`schedule` cron plus `workflow_dispatch`, ubuntu) runs `uv run pytest tests/scripts -m slow`, and a red nightly shows on the Actions tab. Locally, `pytest -m slow` runs them on demand.
- Files named after a release or a fix round are folded into their feature files: `test_0_3_1_scripts.py`, `test_session_reconcile_gaps.py`, the `test_pr23_*` doc-grep tests in `test_past_findings.py`, and `tests/regression/*`.
- The hook boilerplate repeated in 4 to 5 files moves into one parametrized `test_hooks_contract.py`: hooks.json registration, LF endings and fail-open on malformed input. The timing tests are cut (D5).
- **Tests run concurrently by default.** `pytest-xdist` joins the dev group, and `pyproject.toml` sets `addopts = "-n auto --dist loadfile"`. Plain `uv run pytest`, locally and in all three CI jobs, then spreads the files across every core. `-n 0` still runs serially for debugging. Tests that share state are fixed or pinned with `@pytest.mark.xdist_group`: a fixed port, a write into the repo tree, a shared `SUPERLITE_HOME`, or a wall-clock assert.
- No shipped code changes. Only `tests/`, `.github/workflows/tests.yml` and `pyproject.toml` (markers, addopts, dev dependency) change.

## Acceptance checks

1. `uv run pytest tests/scripts` and `uv run pytest tests/bench` both pass on the branch.
2. **Faster.** On the PR's CI run (with `slow` deselected), the Windows job is at least 35% shorter than main's last run (22.2 min), and the Linux job is at least 30% shorter (4.4 min).
3. **Core coverage kept.** Line coverage (`coverage run -m pytest tests/scripts`) of each core module does not drop by more than 1 point against main. The core modules are `dispatch.py`, `plan_tools.py`, `plan_lint.py`, `plan_schedule.py`, `checkpoint.py`, `orchestrator.py`, `routing.py`, `wave0.py`, `pt_review.py`, `review_packet.py`, `run_tests.py`, `testcmd.py`, `base.py`, `session*.py`, `claude_create.py`, `worktree_home.py`, `proto.py`, `exclusive.py` and `finish.py`. A before/after table is in the PR.
4. **Sabotage still fails.** Three hand mutations each turn the suite red. Each is reverted after the check.
   - `hooks/pre-tool` allows a coordinator `Edit` during a run.
   - `session close` deletes an adopted worktree's branch.
   - `dispatch` hands one agent two tasks.
5. No test file is named after a release or a fix round. `test_name_scan::test_no_phrase_grep_tests` still passes, and it now catches the markdown-only greps that were cut.
6. The product rules from AGENTS.md keep exactly one test each: the `using-superlite` SKILL.md is at most 2,000 characters, the hooks use LF endings, every `superlite-cli` call in the docs names a real tool (`test_doc_contract`), and no doc has an unlabelled `git worktree add`.
7. **Concurrent by default.** A bare `uv run pytest tests/scripts` runs with xdist workers: the header shows `created: N/N workers`. It passes 3 times in a row with `-n auto`, and once with `-n 0`. No test depends on run order, a fixed port or the repo working tree.

## Cut list (from research; counts are `def test_`, before parametrize)

**Tier A: no behaviour guarded. Cut.**
- Doc and markdown wording:
  - All of `test_worktree_docs.py` except `test_no_unlabelled_git_worktree_add`.
  - `test_past_findings`: the 4 `test_pr23_*` tests and `test_pr18_readme_scanner_badge_is_not_static`.
  - `test_promises`: `using_superlite_loads_through_the_pi_extension`, `_opencode_plugin` and `_gemini`.
  - `test_name_scan`: `no_commit_word` and `no_old_names`.
  - `test_researcher_agent::test_body_rules` and `test_feature_flow::test_skill_size_limits` (the size cap stays in one place).
  - `test_session_start.py`, `tests/regression/test_research_docs.py`, `test_exclusive::test_session_start_mentions_the_suspension_only_while_it_holds` and `test_branch_pref::test_branch_name_flag_listed`.
- Tests of the fixture or table itself:
  - `test_guard_matrix::test_table_covers_the_whole_cross_product`.
  - `test_compact_host`: all but `unknown_host_fallback`.
  - `test_cost_table::cost_line_matches_old_output`.
  - `test_generalized::old_module_name_is_an_alias`.
- Calibration numbers: `test_predict_actuals`, except `schedule_total_is_setup_plus_scaled_build_plus_wrapup`.
- `test_code_shape.py`, a lint against complexity comments.

**Tier B: duplicates. Merge or cut the copy.**
- Hook boilerplate:
  - `test_hooks_json_registers_*` in 4 files plus the one in `test_human_gate`.
  - 3 `*_lf_endings` tests.
  - 5 `*malformed_input_fails_open` / `guard_fails_open` tests.
  - 5 `*_fast` timing tests, which are cut (D5).
  - All of these become `test_hooks_contract.py`.
  - Also cut `test_hooks_cwd_first::test_hooks_parse_under_bash`, which duplicates `test_hooks_bash32`.
- The per-state allow/deny tests in `test_block_coordinator_edits` and `test_human_gate` are already in `test_guard_matrix`. Keep the special cases: batch, tier and question guards, protected paths, the read-only agent check.
- `test_0_3_1_scripts.py` splits into `test_plan_tools`, `test_pt_review` and `test_testcmd`, then is deleted. `test_shape.py` and `test_lang_seam.py` move into `test_plan_tools`. `test_dispatch_swarm.py` moves into `test_routing`, `test_cli.py` into `test_spawn_free`, and `test_finish_order.py` into `test_exclusive`.
- Adopt (6 places today): keep `test_session_adopt` with `_discard` merged into it, plus `test_claude_create`, `test_proto_adopt`, and the two lifecycles in `test_claude_create_e2e`. Shrink `test_session_adopt_dogfood` to `harness_lifecycle` and `detached_lifecycle`.
- Reconcile (6 places): `test_session_reconcile` is the canonical file and takes in `_gaps` and `regression/test_registry_keeps_worktrees`. Shrink `test_session_dogfood` to 3 tests and `_cli` to its wiring. "Report changes nothing" and "second apply is a no-op" will each be asserted once.
- Session:
  - `test_session_ensure`: merge its untracked-note group (5 into 2), the collision pair and the no-commit pair.
  - `test_session_moveout`: cut `rollback_on_failure_leaves_everything_unchanged`, which the `fail_at` matrix covers, and the 2 argv-plumbing sync tests.
  - Fold `regression/test_wip_save_failure` into `test_session_wip`.
  - Shrink `test_worktree_home_e2e` to 2 tests.
- `test_session_finish_mark`: the 4 `orchestrator_set_warns_*` wording tests go to 1. `test_session_run_state`: the session-start wording variants go to 1.
- `test_session_cost_rewrite` and `test_session_cost_testcmd` are not about sessions. They move into `test_rewrite_test_runs` and `test_testcmd`.

**Tier C: oversized cases. Shrink them.**
- Batching and loads:
  - `test_batching::brute_force_agreement_300` goes from 300 cases to 50. Cut `tenths_helper` and `predictor_check_value`.
  - `test_base::locked_update_two_processes_100` goes from 100 to 10.
- Parametrize lists:
  - `test_block_coordinator_edits`: the two big lists go to about 3 cases each.
  - `test_branch_pref::pattern_requires_slug` goes from 8 cases to 3.
  - `test_human_gate`: the 6 YAML-form agent tests become 1 parametrized test.
- Probes: `test_probe_run` keeps crash, hang, shrink-and-regression and commit-mode, and cuts the archive fallback, category timeouts and the 2 server-adapter tests. `test_testprobe` keeps the pure classifier and marks its real-hang tests `slow`.
- Peripheral smoke layer, kept small:
  - `test_compact_flow`: 13 tests to 5.
  - `test_compact_log`: cut the counting trivia.
  - `test_cost_live`: 12 to 4.
  - `test_metrics_history`: about 21 to 10.
  - `test_run_widget`: 20 to 5, keeping the XSS escape and the dispatch-line guard.
  - `test_research`: 37 to about 15, keeping the lint, the download safety and the 2 session-worktree tests.
  - `test_research_render`: 28 to 8, keeping the parity checks.
  - `test_mvp_check`: 14 to 6.
  - `test_suggest_flow`, `test_graphify_gate` and `test_tail`: 1 to 2 tests each.
  - `test_graph`: 9 to 2.
  - `test_agent_specs`: 10 to 4.

**Kept as they are:**
- Session: `test_session`, `test_session_wip`, `test_session_split`, `test_session_sync`, `test_session_archive`, `test_session_close_archive`, `test_step_failures`, `test_worktree_home`, `test_claude_create`, `test_superlite_home`.
- Orchestration: `test_plan_tools`, `test_orchestrator`, `test_routing`, `test_stable_split`, `test_wave0`, `test_run_scripts`, `test_pt_review`, `test_testcmd`, `test_slim_prime`, `test_spawn_free` (dispatch and commit tests), `test_flows`.
- Cheap guards: `test_line_grammar`, `test_doc_contract`, `test_hooks_bash32`, `test_agent_files`, `test_no_popup_windows`, `test_context_tokens`, `test_host_models`, `test_registry_lock_retries`.
- The Hypothesis suites (`test_session_machine`, `test_properties`) stay. They are the broadest guards, and many of the single-path cuts above lean on them.

Estimate: about 350 to 450 fewer collected tests (roughly 20%). Most of the time saved comes from the bench files leaving CI, `slow` moving to nightly, and roughly 150 fewer bash spawns and git repos.

## Code map

- Entry: `pyproject.toml` (`testpaths = ["tests/scripts"]`, markers `slow`, `live`). CI is `.github/workflows/tests.yml` with three jobs: Linux runs `tests/scripts tests/bench`, macOS runs `tests/scripts`, and Windows runs `tests/scripts tests/bench`.
- Shared fixtures: `tests/scripts/conftest.py` isolates `SUPERLITE_HOME` and clears `CLAUDECODE`. `scenario.py` and `fake_agent.py` serve `test_properties` and `test_flows`. These must not change.
- Seams: hook tests run `hooks/pre-tool`, `hooks/prompt` and `hooks/capture-*` through bash. Session tests build a git repo per test and call `session.py` or `superlite-cli`.
- Conventions:
  - `test_name_scan::test_no_phrase_grep_tests` already bans tests that only grep a phrase, so new tests must assert behaviour.
  - Commits carry no agent trailers (AGENTS.md).
  - LF endings.
  - Don't touch `bench/` results.
- Interfaces frozen: all shipped scripts, hooks and docs. This change only deletes, merges and parametrizes tests.

## Prototype scope

In the sandbox `superlite/test-suite-trim-proto`:
- Apply the CI and marker changes, xdist on by default, and the Tier A cuts.
- Build `test_hooks_contract.py` and cut the boilerplate it replaces.
- Measure wall time, collected count and core-module coverage before and after, on this machine and on a pushed CI run of the proto branch (no PR).

That shows whether the bulk of the saving comes cheaply before the long merge work. The try-out is the numbers plus the three sabotage checks.

## Integration outline

One task per area, each keeping its area green:
- T1: CI and markers, plus xdist on by default and the fixes for shared state it exposes.
- T2: the hook contract and the hook-file shrinks.
- T3: the orchestration merges (`0_3_1`, shape, lang_seam, dispatch_swarm, cli).
- T4: the session adopt and reconcile merges.
- T5: the session ensure, moveout and wip shrinks.
- T6: the peripheral smoke layer.
- T7: the coverage and sabotage report.

## Decisions

- D1. **Bench leaves CI** (user, 2026-11-10). It stays in the repo and runs by hand with `uv run pytest tests/bench`. Only `test_real_formats.py` stays in CI, because it runs shipped scripts.
- D2. **`slow` runs as a nightly job** (user, 2026-11-10). It runs on ubuntu only, plus `workflow_dispatch` for on-demand runs. PR jobs deselect `slow`.
- D3. **Coverage is a one-off measurement for this PR, not a blocker** (user, 2026-11-10). It is reported in the PR, with no CI gate.
- D5. **Cut the wall-clock asserts.** About 6 tests time a hook call and fail when it takes longer than a fixed number of milliseconds: `test_human_gate::test_latency`, the `*_fast` tests and `test_long_bash_command_is_decided_fast`. They guard performance, not behaviour, and they break on a slow disk or when tests run in parallel (D6). `test_latency` already failed locally. All of them go, and `test_hooks_contract.py` gets no timing test. A slow hook would show up as a slower suite instead.
- D6. **xdist with `--dist loadfile`** (user, 2026-11-10). Each file's tests stay on one worker, so module-scoped fixtures and caches like `test_doc_contract`'s keep working. The slow single-test files (`test_session_machine` at 310 s) run alongside the rest instead of after it. `test_session_machine` then sets the floor on wall time, so splitting its examples across 2 to 4 parametrized seeds is a follow-up if needed. GitHub runners have 2 cores on Windows and Linux and 3 on macOS, so CI gains less from this than the trim does. Local runs gain the most.
- D4. **Peripheral features keep a smoke layer, not zero tests** (user, 2026-11-10). They still ship, and a crash in a hook-driven extra (compact, cost) could break a session.
