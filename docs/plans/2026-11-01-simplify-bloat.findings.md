# Simplify bloat: Copilot findings map

Source: the 122 Copilot review findings on PRs 7, 14, 15, 18, 21, 22, 23, 25 and 26. Test paths are under `tests/scripts/`.

## Findings

| # | PR | File | Finding | Class | Covered by |
|---|----|------|---------|-------|------------|
| 1 | #26 | `.github/workflows/tests.yml` | CI step makes the blocking reverse-DNS call before pytest | platform | `test_past_findings.py::test_pr26_ci_reverse_dns_step_cannot_hang_the_job` |
| 2 | #25 | `commands/review.md` | review target resolved before ensure changes checkout | lifecycle | `test_flows.py::test_flow_review` |
| 3 | #25 | `hooks/block-coordinator-edits` | hook cwd lookup misses a worktree path with a double quote | platform | `test_hooks_cwd_first.py::test_guard_resolves_run_from_cwd_with_escaped_quote` |
| 4 | #25 | `skills/shipping/SKILL.md` | shipping merge switches a main checkout that hosts another session | lifecycle | `test_session_ensure.py::test_close_host_refuses_when_base_checked_out_elsewhere` |
| 5 | #25 | `skills/swarm/scripts/session.py` | parallel checkout writes orchestrator.json into the host's .superlite | state location | `test_orchestrator.py::test_set_in_parallel_worktree_writes_its_own_state` |
| 6 | #25 | `skills/swarm/scripts/session.py` | registry base stays set after the host session closes | lifecycle | `test_session_ensure.py::test_base_follows_main_checkout_branch_after_close` |
| 7 | #25 | `skills/swarm/scripts/session.py` | closing an owned parallel session force-removes without parking or stashing | partial failure | `test_session_ensure.py::test_close_owned_parallel_stashes_work` |
| 8 | #25 | `commands/debug.md` | debug opens a session and never marks the run finished | lifecycle | `test_flows.py::test_flow_debug` |
| 9 | #25 | `commands/feature.md` | feature step 6 does not register or transfer the opened session | lifecycle | `test_flows.py::test_flow_feature` |
| 10 | #25 | `commands/small.md` | small opens a session and never marks the run finished | lifecycle | `test_flows.py::test_flow_small` |
| 11 | #25 | `hooks/session-start` | SUPERLITE_NO_SESSION_HUD makes a finished session read as in progress | other | `test_session_run_state.py::test_session_start_finished_run_without_hud` |
| 12 | #25 | `skills/swarm/scripts/session.py` | collision suffix pushes a 41-char slug over the limit | identity | `test_session_ensure.py::test_collision_on_41_char_slug_yields_valid_slug` |
| 13 | #25 | `skills/swarm/scripts/session.py` | mandatory ensure fails in a non-git folder | lifecycle | `test_session_ensure.py::test_ensure_outside_git_continues_without_session` |
| 14 | #25 | `skills/swarm/scripts/session.py` | ensure is not idempotent after a slug collision | identity | `test_session_ensure.py::test_ensure_is_idempotent_after_a_collision` |
| 15 | #25 | `skills/swarm/scripts/session.py` | registering an unregistered linked worktree leaves .superlite untracked | state location | `test_session_ensure.py::test_ensure_in_unregistered_linked_worktree_hides_superlite` |
| 16 | #25 | `tests/scripts/test_session_run_state.py` | syntax check runs /bin/bash on native Windows | platform | `test_hooks_bash32.py::test_tests_resolve_bash_instead_of_bin_bash` |
| 17 | #25 | `skills/swarm/scripts/session.py` | park_active stashes WIP before a switch that fails | partial failure | `test_session_moveout.py::test_base_checked_out_elsewhere_is_refused_before_any_change` |
| 18 | #25 | `skills/inventing/SKILL.md` | bounded path opens a session and never finishes it | lifecycle | `test_flows.py::test_flow_small` |
| 19 | #25 | `skills/swarm/scripts/session_hud.py` | nested unregistered worktree inherits the host's session row | identity | `test_session_run_state.py::test_hud_in_nested_unregistered_worktree_does_not_name_main_session` |
| 20 | #25 | `tests/scripts/test_hooks_cwd_first.py` | hard-coded /bin/bash in a hook test fails on Windows | platform | `test_hooks_bash32.py::test_tests_resolve_bash_instead_of_bin_bash` |
| 21 | #25 | `skills/swarm/scripts/session_sync.py` | sync --abort/--continue bypass the branch check | identity | removed: session sync has no --abort/--continue |
| 22 | #25 | `skills/swarm/scripts/session.py` | check-ignore passes only because a tracked .gitignore excludes .superlite | state location | `test_session_ensure.py::test_exclusion_does_not_depend_on_tracked_gitignore` |
| 23 | #25 | `skills/swarm/scripts/session.py` | session status never computes main:+N | docs vs code | `test_session_ensure.py::test_status_shows_main_plus_n_when_behind` |
| 24 | #25 | `skills/swarm/scripts/session.py` | unregistered-worktree path skips hide_superlite | state location | `test_session_ensure.py::test_ensure_in_unregistered_linked_worktree_hides_superlite` |
| 25 | #25 | `skills/swarm/scripts/session.py` | ensure --branch from an open linked session returns the wrong branch | identity | `test_session_ensure.py::test_ensure_branch_from_open_parallel_session_targets_requested_branch` |
| 26 | #25 | `commands/review.md` | review treats a path target as a branch | docs vs code | `test_flows.py::test_flow_review` |
| 27 | #25 | `skills/swarm/scripts/session.py` | REDIRECT for an unregistered worktree creates no row and no SESSION line | lifecycle | `test_session_ensure.py::test_redirect_to_unregistered_worktree_registers_it` |
| 28 | #25 | `skills/swarm/scripts/session.py` | adopting the registry base makes a second flow choose move-out | lifecycle | `test_session_ensure.py::test_session_on_base_branch_stays_in_place_when_second_flow_starts` |
| 29 | #25 | `skills/swarm/scripts/session_sync.py` | a later session changes the registry-wide base, so sync merges the wrong branch | identity | `test_session_sync.py::test_sync_merges_the_rows_own_base` |
| 30 | #25 | `commands/feature.md` | feature cleanup closes by slug and can close an older session | identity | `test_flows.py::test_flow_feature` |
| 31 | #25 | `commands/start.md` | start's generic ensure runs before review resolves its target | lifecycle | `test_flows.py::test_flow_review` |
| 32 | #25 | `skills/swarm/scripts/session.py` | ensure on the current branch stashes a finished session's uncommitted fix | lifecycle | `test_session_ensure.py::test_review_of_finished_sessions_branch_keeps_its_uncommitted_work` |
| 33 | #25 | `skills/swarm/scripts/session.py` | legacy adoption lets cleanup force-delete a user branch | lifecycle | `test_session_ensure.py::test_adopted_branch_survives_close_and_discard` |
| 34 | #25 | `skills/swarm/scripts/session.py` | switching the free checkout overwrites the shared base | identity | `test_session_ensure.py::test_finished_active_is_parked_and_new_session_keeps_recorded_base` |
| 35 | #25 | `skills/swarm/scripts/session.py` | reuse check ignores an explicit --branch | identity | `test_session_ensure.py::test_ensure_branch_from_linked_checkout_does_not_move_the_host_session` |
| 36 | #25 | `skills/swarm/scripts/session.py` | close of a clean worktree deletes a branch with unmerged commits | partial failure | `test_past_findings.py::test_pr25_close_clean_worktree_keeps_branch_with_unmerged_commits` |
| 37 | #25 | `skills/swarm/scripts/session.py` | registering a worktree records today's base tip it has not merged | identity | `test_session_moveout.py::test_base_sha_of_fills_missing_from_merge_base` |
| 38 | #25 | `skills/swarm/scripts/session.py` | legacy orchestrator.json: ensure switches branches before ASK move/untracked | partial failure | `test_session_moveout.py::test_check1_move_needs_confirmation_and_changes_nothing` |
| 39 | #25 | `skills/shipping/SKILL.md` | shipping checks only run:open before switching a checkout | lifecycle | `test_session_ensure.py::test_close_host_parks_and_switches_back_keeping_branch` |
| 40 | #23 | `commands/feature.md` | AI-mode feature run opens .feature.md without checking open_in_harness | docs vs code | `test_past_findings.py::test_pr23_feature_ai_mode_open_is_guarded` |
| 41 | #23 | `commands/mvp.md` | AI MVP run can skip opening .mvp.md | docs vs code | `test_past_findings.py::test_pr23_mvp_open_happens_before_the_mode_split` (xfail: bug is back) |
| 42 | #23 | `commands/resume.md` | resume opens machine, integration or proto plans | docs vs code | `test_past_findings.py::test_pr23_resume_opens_only_human_facing_documents` |
| 43 | #23 | `skills/drafting/SKILL.md` | drafting rule opens every plan without checking open_in_harness | docs vs code | `test_past_findings.py::test_pr23_drafting_open_rule_checks_the_flag_in_both_modes` |
| 44 | #22 | `docs/config.md` | docs say max_tasks bounds the exact split; dispatch only passes the context budget | docs vs code | `test_batching.py::test_batch_cap_is_the_tier_context_budget` |
| 45 | #21 | `skills/swarm/scripts/session.py` | moving all of .superlite relocates prototype sandboxes and worktrees | state location | `test_session.py::test_park_keeps_sandboxes_in_place` |
| 46 | #21 | `skills/swarm/scripts/session.py` | checkout fails when the open run is in a linked worktree and main is elsewhere | lifecycle | `test_session_split.py::test_split_from_linked_feature_worktree_keeps_run_in_place` |
| 47 | #21 | `skills/swarm/scripts/session.py` | moved checkpoints.json still records the original branch | state location | `test_session_split.py::test_split_keeps_sandboxes_and_rebinds_checkpoints` |
| 48 | #21 | `skills/swarm/scripts/session.py` | git failures exit and bypass recovery | partial failure | `test_step_failures.py::test_split_each_step_fails_cleanly_apart_from_gitignore` |
| 49 | #21 | `skills/swarm/scripts/session.py` | stash restore without --index loses staged changes | partial failure | `test_session_split.py::test_split_preserves_staged_changes` |
| 50 | #21 | `skills/swarm/scripts/session.py` | --show-toplevel from the linked worktree misses the hub registry | state location | `test_session.py::test_commands_find_hub_registry_from_a_session_worktree` |
| 51 | #21 | `skills/swarm/scripts/session.py` | slug host is accepted and collides with the serial-host path | identity | `test_session.py::test_host_slug_is_reserved` |
| 52 | #21 | `skills/swarm/scripts/session.py` | two concurrent create --worktree calls overwrite each other's registry update | concurrency | `test_session.py::test_concurrent_parallel_creates_all_register` |
| 53 | #21 | `skills/swarm/scripts/session.py` | host .superlite holds real worktrees (sandboxes, integration) | state location | `test_session_split.py::test_split_keeps_sandboxes_and_rebinds_checkpoints` |
| 54 | #21 | `skills/swarm/scripts/session.py` | create ignores the branch split recorded for the row | identity | `test_session_split.py::test_use_honors_recorded_branch` |
| 55 | #21 | `skills/swarm/scripts/session.py` | substring match lets auth take auth-api's stash | identity | `test_session.py::test_auth_never_takes_auth_api_stash` |
| 56 | #21 | `skills/swarm/scripts/session.py` | use A after discarding B never applies A's stash | lifecycle | `test_session.py::test_use_after_discard_restores_parked_wip` |
| 57 | #21 | `skills/swarm/scripts/session.py` | discarding the active serial session leaves branch and dirty files in the host | lifecycle | `test_session.py::test_discard_active_serial_cleans_wip_and_branch` |
| 58 | #21 | `hooks/cost-report` | HUD sits behind cost-only early exits | other | `test_session.py::test_prompt_hook_emits_hud_without_cost_or_run_dir` |
| 59 | #21 | `hooks/cost-report` | HUD only on the user-prompt hook, not session start | other | `test_session_run_state.py::test_session_start_open_run_in_progress` |
| 60 | #21 | `skills/swarm/scripts/session.py` | conflict message says --continue, which re-applies the stash | lifecycle | `test_session.py::test_continue_drops_stash_without_reapplying` |
| 61 | #21 | `skills/swarm/scripts/session_hud.py` | string-prefix match treats sibling worktrees as descendants | identity | `test_session.py::test_hud_uses_path_components_and_works_from_worktree` |
| 62 | #21 | `skills/swarm/scripts/session.py` | failed restore loses run files on retry | partial failure | `test_session.py::test_restore_run_state_can_retry_after_partial_copy` |
| 63 | #21 | `skills/swarm/scripts/session.py` | a directory nested in the hub passes the repository check | identity | `test_past_findings.py::test_pr21_create_refuses_an_existing_directory_that_is_not_a_worktree` |
| 64 | #21 | `skills/swarm/scripts/session.py` | parked and active serial sessions share one host path | identity | `test_past_findings.py::test_pr21_split_will_not_pair_a_parked_keep_session_with_the_active_branch` |
| 65 | #21 | `skills/swarm/scripts/session.py` | first run-state move happens before any undo is registered | partial failure | `test_session_split.py::test_split_restores_run_state_after_partial_move` |
| 66 | #21 | `skills/swarm/scripts/session.py` | failed worktree removal is ignored on discard | partial failure | `test_past_findings.py::test_pr21_discard_stops_when_git_refuses_to_remove_the_worktree` |
| 67 | #21 | `skills/swarm/scripts/session.py` | splitting from an owned worktree overwrites owned=True | lifecycle | `test_past_findings.py::test_pr21_split_from_an_owned_worktree_keeps_it_owned` (xfail: bug is back) |
| 68 | #21 | `skills/swarm/scripts/session.py` | rollback after a failed rebind leaves checkpoints.json stale | partial failure | `test_session_split.py::test_split_rolls_back_on_failure` |
| 69 | #21 | `skills/swarm/scripts/session_hud.py` | HUD fallback names the host session in an unregistered worktree | identity | `test_session_run_state.py::test_hud_in_nested_unregistered_worktree_does_not_name_main_session` |
| 70 | #21 | `skills/swarm/scripts/session.py` | restore copies from the parked store and removes it only after success | partial failure | `test_session.py::test_restore_run_state_can_retry_after_partial_copy` |
| 71 | #21 | `skills/swarm/scripts/session.py` | rollback registered before run-state movement | partial failure | `test_session_split.py::test_split_restores_run_state_after_partial_move` |
| 72 | #21 | `hooks/cost-report` | session-start calls session_hud.py (reply to #21) | other | `test_session_run_state.py::test_session_start_open_run_in_progress` |
| 73 | #21 | `skills/swarm/scripts/session.py` | rollback skips restore when no file moved | partial failure | `test_session_split.py::test_split_does_not_restore_run_state_when_first_move_fails` |
| 74 | #18 | `README.md` | README badge always shows passing | docs vs code | `test_past_findings.py::test_pr18_readme_scanner_badge_is_not_static` (xfail: bug is back) |
| 75 | #15 | `skills/swarm/scripts/pt_review.py` | pt_review name lookup dropped builtins | other | `test_past_findings.py::test_pr15_serve_args_resolve_builtins` (xfail: bug is back) |
| 76 | #15 | `skills/swarm/scripts/pt_review.py` | pt_review treats a dict unpack key as an omitted entry | other | `test_past_findings.py::test_pr15_serve_args_merge_dict_unpack` (xfail: bug is back) |
| 77 | #15 | `skills/swarm/scripts/pt_review.py` | pt_review discards keyword unpacking | other | `test_past_findings.py::test_pr15_serve_args_expand_keyword_unpacking` (xfail: bug is back) |
| 78 | #14 | `hooks/_sl_state_dir.sh` | hooks pick the shared active session; Python tools pick by_host | state location | `test_hooks_cwd_first.py::test_reply_recorded_in_worktree_not_hub` |
| 79 | #14 | `hooks/block-coordinator-edits` | guard lets Write/Edit reach per-session orchestrator.json and replies | state location | `test_past_findings.py::test_pr14_guard_denies_writes_to_protected_state_files` |
| 80 | #14 | `skills/swarm/scripts/finish.py` | finish with an explicit ledger loses the session identity | identity | removed: per-session state dirs |
| 81 | #14 | `skills/swarm/scripts/finish.py` | finishing A removes the peer suspension while B still runs | concurrency | `test_finish_order.py::test_last_finish_releases_peers` |
| 82 | #14 | `skills/swarm/scripts/plan_tools.py` | lint --stable writes a per-session stable.json the readers never read | state location | `test_stable_split.py::test_dispatch_and_next_tasks_offer_only_stable_tasks` |
| 83 | #14 | `skills/swarm/scripts/session.py` | ancestor walk from an integration worktree reaches the parent's .superlite | state location | `test_base.py::test_linked_worktree_does_not_see_parent_state` |
| 84 | #14 | `skills/swarm/scripts/session.py` | concurrent start()/end() lose registry updates | concurrency | `test_session.py::test_concurrent_parallel_creates_all_register` |
| 85 | #14 | `skills/swarm/scripts/session.py` | end() deletes a directory from an unvalidated id | identity | `test_past_findings.py::test_pr14_slug_cannot_walk_out_of_the_session_store` (session end() is gone; discard builds its path from the slug) |
| 86 | #14 | `commands/mvp.md` | start mvp creates a session, then mvp starts another without host binding | lifecycle | `test_flows.py::test_flow_mvp` |
| 87 | #14 | `skills/swarm/scripts/finish.py` | finish reads session logs while checkpoint, run_tests, incident write flat | state location | removed: per-session state dirs |
| 88 | #14 | `skills/swarm/scripts/testcmd.py` | testcmd array branch drops integer counts in other *_tests fields | other | removed: testcmd no longer parses reporter JSON counts (parse_output reads plain text) |
| 89 | #14 | `commands/mvp.md` | mvp step 5 and skill read flat mock/feedback paths | state location | `test_flows.py::test_flow_mvp` |
| 90 | #14 | `hooks/block-coordinator-edits` | per-session guard files are never populated by dispatch | state location | removed: per-session state dirs |
| 91 | #14 | `skills/swarm/scripts/orchestrator.py` | feature_status reads session feature.json; proto.py writes the project one | state location | `test_feature_flow.py::test_start_cuts_a_worktree_and_records_feature_json` |
| 92 | #14 | `skills/swarm/scripts/session.py` | explicit path inside session A resolves to session B | identity | `test_base.py::test_subfolder_resolves_to_toplevel` |
| 93 | #14 | `skills/swarm/scripts/checkpoint.py` | checkpoint_one uses the shared checkpoints.json while the ledger is per session | state location | removed: per-session state dirs |
| 94 | #14 | `skills/swarm/scripts/dispatch.py` | pool.json default stays flat while sessions are per directory | state location | removed: per-session state dirs |
| 95 | #14 | `skills/swarm/scripts/exclusive.py` | concurrent suspensions overwrite each other's owner additions | concurrency | `test_exclusive.py::test_release_takes_the_registry_lock` |
| 96 | #14 | `skills/swarm/scripts/mvp_check.py` | mvp_check defaults ignore --plan | state location | `test_flows.py::test_flow_mvp` |
| 97 | #14 | `skills/swarm/scripts/run_tests.py` | pt_review packet defaults to flat summary and incidents | state location | `test_pt_review.py::test_packet_default_paths_follow_root_not_cwd` |
| 98 | #14 | `skills/swarm/scripts/session.py` | empty-registry cleanup runs after the lock is released | concurrency | removed: session end() and its empty-registry cleanup |
| 99 | #14 | `skills/swarm/scripts/plan_tools.py` | THEN LOG appends dispatch records to the flat progress file | state location | removed: per-session state dirs |
| 100 | #14 | `skills/swarm/scripts/session.py` | end() leaves its owner in peers.json | lifecycle | `test_finish_order.py::test_last_finish_releases_peers` |
| 101 | #14 | `hooks/_sl_state_dir.sh` | start/MVP never bind a native host-session id | identity | removed: native host-session ids (by_host) |
| 102 | #14 | `hooks/_sl_state_dir.sh` | host id interpolated as an unescaped regex in a hook | identity | removed: hooks/_sl_state_dir.sh and host ids |
| 103 | #14 | `skills/swarm/scripts/autocommit.py` | autocommit clean() enumerates every session's refs | identity | removed: autocommit.py deleted |
| 104 | #14 | `skills/swarm/scripts/finish.py` | explicit ledger outside sessions/<id> not anchored to a session | identity | removed: per-session state dirs |
| 105 | #14 | `hooks/capture-agent-launch` | metrics/budget readers not migrated to the session dir | state location | removed: per-session state dirs |
| 106 | #14 | `skills/swarm/SKILL.md` | build_prime writes a flat prime file the docs say is per session | state location | removed: per-session state dirs |
| 107 | #14 | `skills/swarm/scripts/autocommit.py` | autocommit falls back to flat paths after finish deletes the session | state location | removed: per-session state dirs |
| 108 | #14 | `skills/swarm/scripts/pt_review.py` | pt_review looks for probe results in the session; probe_run writes flat | state location | `test_pt_review.py::test_packet_reads_default_probe_and_test_summary_paths` |
| 109 | #14 | `hooks/rewrite-test-runs` | rewritten run_tests command carries no session binding | identity | removed: per-session state dirs (rewrite-test-runs binds no session) |
| 110 | #14 | `skills/swarm/scripts/finish.py` | finish writes history.jsonl and stats.json into the session dir | state location | `test_metrics_history.py::test_finish_records_run_and_reports_predicted_vs_actual` |
| 111 | #14 | `skills/swarm/probes/probe_run.py` | probe_run display_path is relative to cwd, --out resolved against --root | other | `test_past_findings.py::test_pr14_probe_run_default_out_follows_root_not_cwd` |
| 112 | #14 | `skills/swarm/probes/probe_run.py` | probe_run filters space-separated --out/--log before consuming values | other | `test_past_findings.py::test_pr14_probe_run_background_keeps_space_separated_out_and_log` |
| 113 | #14 | `skills/swarm/scripts/finish.py` | history.run_rows detects language from the sessions dir parent | state location | `test_metrics_history.py::test_finish_records_run_and_reports_predicted_vs_actual` |
| 114 | #14 | `skills/swarm/scripts/finish.py` | report.feature_parts reads feature.json from the session dir | state location | `test_report.py::test_normal_run_with_saved_projection_and_tests` |
| 115 | #14 | `skills/swarm/scripts/history.py` | history.project_stats reads stats.json from the wrong directory | state location | `test_metrics_history.py::test_history_write_stats_and_read` |
| 116 | #14 | `commands/start.md` | start menu starts a session before routing to Resume/Status/Finish | lifecycle | `test_flows.py::test_flow_regular` |
| 117 | #14 | `commands/sessions.md` | sessions use changes the shared pointer without rebinding by_host | identity | `test_session.py::test_use_parks_and_restores_dirty_work` |
| 118 | #14 | `skills/swarm/scripts/run_tests.py` | background run forwards the original arguments and pins only the summary path | state location | `test_testcmd.py::test_background_and_wait_work_for_a_non_python_runner` |
| 119 | #7 | `skills/swarm/scripts/exclusive.py` | exclusive.py writes .claude/settings.local.json when it is not ignored | state location | `test_past_findings.py::test_pr7_exclusive_settings_write_does_not_block_checkpoints` (xfail: bug is back) |
| 120 | #7 | `commands/finish.md` | finish.md completion path never calls finish after orchestrator set | lifecycle | `test_flows.py::test_flow_small` |
| 121 | #7 | `hooks/session-start` | session-start checks a relative marker before resolving the project dir | platform | `test_hooks_cwd_first.py::test_guard_odd_cwd_keeps_hub_run_and_never_crashes` |
| 122 | #7 | `.opencode/INSTALL.md` | OpenCode bootstrap does not implement suspension across sessions | other | `test_past_findings.py::test_pr7_opencode_install_claim_matches_the_bootstrap` (xfail: bug is back) |

## Deleted phrase tests

| Deleted test | Rule it checked | Now covered by |
|---|---|---|
| `test_flow_session_wiring.py (all 13 functions)` | every flow runs `session ensure` before another `superlite-cli` call; docs mention ASK untracked / ASK move / sync; finish after verification; review resolves target before ensure; shipping merge via temporary checkout; feature step 6 closes the started session | test_flows.py (runs each flow's real call sequence) and test_doc_contract.py (every call in the docs is real); ASK lines by test_line_grammar.py |
| `test_flow_diagrams.py::test_every_mode_has_a_stored_diagram_that_fits_the_screen` | stored flow diagrams are ASCII, at most 79 columns and 50 lines | test_name_scan.py |
| `test_flow_diagrams.py::test_each_mode_command_prints_its_own_stored_diagram` | each mode command names its assets/flows file and says never redraw | dropped: wording only |
| `test_flow_diagrams.py::test_prototype_modes_draw_the_sandbox_and_build_modes_draw_the_parallel_fork` | diagram text contains SANDBOX and the parallel-fork row | dropped: wording only |
| `test_open_in_harness.py::test_flow_steps_open` | flow files mention the open_in_harness flag | dropped: wording only (findings 40-43: see test_past_findings.py) |
| `test_open_in_harness.py::test_harness_rows` | each *-tools.md has exactly one open row | test_doc_contract.py |
| `test_open_in_harness.py::test_planless_flows_open_nothing` | small, debug, review do not mention the flag | dropped: wording only |
| `test_open_in_harness.py::test_five_line_summary_kept` | drafting, inventing, orchestrator keep the five-line summary | dropped: wording only |
| `test_open_in_harness.py::test_drafting_ai_rule_in_after_writing` | drafting's AI-orchestrator open rule sits in one place | dropped: wording only |
| `test_feature_flow.py::test_feature_command_has_the_six_steps` | feature.md names the six steps and the cleanup rules; shipping removes only worktrees it made | test_flows.py::test_flow_feature and test_doc_contract.py |
| `test_feature_flow.py::test_start_command_shows_the_suggestion_first` | start.md runs suggest_flow and marks (Recommended) | test_doc_contract.py (suggest_flow is a real call) and test_flows.py::test_flow_regular; size limit kept in test_feature_flow.py::test_skill_size_limits |
| `test_feature_flow.py::test_skills_and_docs_name_the_feature_flow` | feature skill, drafting, help and tools docs name proto, suggest_flow, UI component set | test_doc_contract.py; the rest is dropped: wording only |
| `test_mvp_check.py::test_skill_command_design_and_tool_docs_agree` | mvp skill, command and design doc share the approve line, rulings and mvp_check subcommands; size limits | test_doc_contract.py and test_flows.py::test_flow_mvp; size limits now in test_feature_flow.py::test_skill_size_limits |
| `test_slim_prime.py::test_sl_launcher_knows_task` | launcher header lists task and task.py exists | test_cli.py |
| `test_gitignore.py::test_agent_files_carry_the_ignored_paths_rule` | implementer agent files carry the git-ignored paths rule | test_agent_files.py |

Size limits (using-superlite/SKILL.md 2,000; swarm/SKILL.md 12,500) are kept once, in `test_feature_flow.py::test_skill_size_limits`.

## Findings with no covering test

All 122 findings now name a test or a removal. The 22 that had no covering test (rows 1, 21, 36, 40-43, 63, 64, 66, 67, 74-77, 85, 88, 103, 111, 112, 119, 122) are in `test_past_findings.py` or marked removed above. Rows 79, 80, 84, 87, 90, 93, 94, 98, 99, 101, 102 and 104-107, 109 pointed at tests that did not exercise the finding (`test_state_path_example` only pins `<checkout>/.superlite/<name>`), so they were reclassified: a test, or removed with the per-session state dirs, host ids and `end()`.

Eight findings are back as strict xfails (bug present in today's code): 41 (MVP open only under the Human heading), 67 (split from an owned worktree sets owned=False), 74 (static HOL Guard "passing" badge), 75, 76, 77 (serve args drop builtins, `**` dict unpack and `**` keyword unpack), 119 (checkpoint rejects `.claude/settings.local.json`), 122 (OpenCode bootstrap never reads `peers.json`).
