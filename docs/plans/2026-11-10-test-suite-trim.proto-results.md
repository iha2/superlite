# Test-suite trim: prototype results

Measured on Windows (Git Bash, uv Python 3.15, 16 xdist workers). Proto is this worktree; main is `ad14bd2`.

## Collected tests and wall time (`tests/scripts -m "not slow"`)

| Tree | Collected | Wall time |
|---|---|---|
| main, serial | 1990 (of 2027, 37 deselected) | local run stopped; main's last Windows CI was 22.2 min serial, Linux 4.4 min |
| proto, `-n auto` (16 workers) | 1962 (of 1996, 34 deselected) | 1198 s / 1320 s / 1451 s, 1948 passed |
| proto, serial | not run | n/a |

The proto cut removes only 28 collected tests; the saving comes from running concurrently and from dropping the wall-clock and spawn-heavy cases, not from fewer tests. CI numbers (acceptance check 2) will come from a pushed run later.

Coverage runs (xdist, `--cov`): proto 1 failed (the live-run-state test below), 1948 passed, 12 skipped, 1 xfailed in 1515 s; main 4 failed, 1973 passed, 12 skipped, 1 xfailed in 1671 s. Main's 4 failures are all wall-clock asserts that trip under 16-worker load: `test_rewrite_test_runs::test_fast`, `test_guard_matrix::test_long_bash_command_is_decided_fast`, `test_block_coordinator_edits::test_typical_allowed_call_is_fast`, `test_human_gate::test_latency`. The trim deletes exactly these.

## Coverage of the core modules (percent of lines, `--cov=skills/swarm/scripts`)

| Module | main | proto | change |
|---|---|---|---|
| base.py | 82.1 | 82.1 | +0.0 |
| checkpoint.py | 28.0 | 28.0 | +0.0 |
| claude_create.py | 49.2 | 49.2 | +0.0 |
| dispatch.py | 68.2 | 68.2 | +0.0 |
| exclusive.py | 40.6 | 40.6 | +0.0 |
| finish.py | 17.2 | 17.2 | +0.0 |
| orchestrator.py | 33.7 | 33.7 | +0.0 |
| plan_lint.py | 15.2 | 15.2 | +0.0 |
| plan_schedule.py | 53.7 | 53.7 | +0.0 |
| plan_tools.py | 89.1 | 89.1 | +0.0 |
| proto.py | 15.5 | 15.5 | +0.0 |
| pt_review.py | 90.3 | 90.3 | +0.0 |
| review_packet.py | 90.4 | 90.4 | +0.0 |
| routing.py | 61.7 | 61.7 | +0.0 |
| run_tests.py | 25.9 | 25.9 | +0.0 |
| session.py | 78.7 | 78.7 | +0.0 |
| session_archive.py | 97.4 | 97.4 | +0.0 |
| session_close.py | 70.2 | 70.2 | +0.0 |
| session_ensure.py | 64.5 | 64.5 | +0.0 |
| session_hud.py | 46.7 | 46.7 | +0.0 |
| session_reconcile.py | 99.1 | 99.1 | +0.0 |
| session_registry.py | 86.5 | 86.5 | +0.0 |
| session_switch.py | 56.7 | 56.7 | +0.0 |
| session_sync.py | 85.0 | 85.0 | +0.0 |
| session_wip.py | 80.2 | 80.2 | +0.0 |
| testcmd.py | 60.9 | 60.9 | +0.0 |
| wave0.py | 24.6 | 24.6 | +0.0 |
| worktree_home.py | 98.9 | 98.9 | +0.0 |

No module drops over 1 point; total 11682 statements, 4366 missed on main and 4367 on proto (63% both). Caveat: most scripts run as subprocesses, which this measurement does not follow (hence low numbers for checkpoint.py or plan_lint.py), so the table says in-process coverage is unchanged, not that subprocess-executed lines are. The trim removed no test that calls these modules, so no change is expected.

## Sabotage checks (each applied here, run with `-n 0`, then reverted with `git checkout -- <file>`)

| Mutation | Result | Test that went red |
|---|---|---|
| `hooks/_edits`: the Edit/Write path check never marks a file as project code (coordinator Edit allowed in a run) | red | `test_block_coordinator_edits::test_blocks_coordinator_edit_of_source` |
| `session_close._close_adopted`: delete the adopted worktree's branch ref | red | `test_claude_create::test_close_adopted_unregisters_only`, `test_session_adopt::test_close_adopted_keeps_dirty_tree` |
| `dispatch.make_batches`: `--batch 1` hands all ready tasks to one agent | red | `test_batching::test_batch_one_turns_it_off` |

Gap found (same on main, not caused by the trim): dropping `not row.get("adopted_branch")` from the branch-deletion guard in `session_close._close_owned` (parallel worktree on an adopted user branch) stayed green across `test_session_close_archive`, `test_session_adopt`, `test_session_adopt_discard` and `test_session_ensure::test_adopted_branch_survives_close_and_discard`; that last test closes the host session and never reaches the guard. The mutation in the table above is used instead. A test that closes a parallel worktree on an adopted branch would close the gap.

All edits reverted; `git status` shows no shipped file modified.

## `test_run_scripts::test_next_tasks_longest_first_and_cap`

Passes on main in a clean worktree. Fails here (and in the proto's coverage run) because `next_tasks` reads the live run's `.superlite/stable.json` (T1 to T5 only), which hides T6. Not a Python 3.15 problem and not caused by the trim. It is the "depends on the repo working tree" case from acceptance check 7: it would pass in a clean checkout and CI.
