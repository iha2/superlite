# Tools

Mechanical steps are scripts, not agents. Every tool runs as `bash <skill-dir>/scripts/sl <tool> ...`, where `<skill-dir>` is `skills/subagent-driven-development`. The `sl` launcher runs a `.sh` tool under bash and a `.py` tool under the first working Python, and exits 3 when there is none. It caches that interpreter, so each later call is one Python start (`SUPERLITE_PYTHON` skips the probe entirely). Tools that need each other import them in process instead of spawning `sl` again. The tools are Python, but the project can be any language.

| Tool | What it does |
|---|---|
| `plan_tools` | Plan lint (`--stable` freezes tasks and tiers), task graph, stubs, schedule, prune, and the `adapter`, `packet` and `contract` review subcommands |
| `wave0` | Builds the Wave 0 skeleton brief for `implementer-haiku` from the plan alone |
| `routing` | Maps each task's plan features to a tier (haiku or sonnet) using `routing.json` |
| `shape` | Build shape: contract examples, removable edges, prune, chain ids |
| `predict` | Predicts each task's time, context and cost; `--refit` fits the coefficients |
| `build_prime` | Builds the shared prime every agent reads first. By tier (`prime` in `routing.json`): `slim` for sonnet (rules, plan header, Global constraints, stable tasks' title, Owns, dependencies and interfaces; about 8 KB where the whole plan is 20 KB), `mini` for haiku (`--tier haiku`), or `--prime full` (whole plan and `--sections` spec text) |
| `task` | `sl task Tn`: task Tn's block from the current plan plus the Produces lines of every task it consumes; how agents read their task and neighbours |
| `dispatch` | Ready tasks and who runs each, in one call (`next_tasks` + `assign`) |
| `next_tasks` | Computes which tasks are ready |
| `assign` | Scores idle agents and picks the most relevant one |
| `commit_task` | Checks scope, then checkpoints (default: hidden `refs/superlite/*` commits, branch untouched) or commits (autocommit on) one or more tasks' owned files; `--dispatch` also prints the next dispatch |
| `autocommit` | `on`/`off`/`status` opt-in real commits (`.superlite/autocommit.json`, env `SUPERLITE_AUTOCOMMIT=1`); `apply` turns checkpoints into commits on the current branch; `clean` deletes them |
| `run_tests` | Runs the project's test command, with `--background`, then `--wait` for the summary; writes `.superlite/test-summary.json`. Probes for a hung run (see below) |
| `testprobe` | The hung-test probe `run_tests` uses; run alone it prints the CPU-time method in use and the effective config |
| `testcmd` | Resolves the project's test command: `.superlite/test-cmd.json`, else detection (npm, go, cargo, dotnet, Python) |
| `pt_review` | `adapter`, `packet` (the final review packet) and `contract` (seam checks) |
| `probe_run` | Parallel robustness probes with no agents (Python projects only) |
| `incident` | Records and queries the shared incident log (file, line, context) |
| `budget` | One line of elapsed time and cost against the plan's target and cap |
| `metrics` | Per-task time, tokens and cost from `.superlite/metrics.jsonl` and agent transcripts |
| `history` | Run history: `write` rows to `~/.superlite/history.jsonl`, `read`, `derive` stats, `import` bench scores |
| `graphify_gate` | Gates Graphify for search in non-trivial codebases |
| `orchestrator` | Who orchestrates: `set human|ai`, `approve --review <plan>` (needs an approving human reply newer than the plan), `status` |
| `report` | The end-of-build report: a short summary in the session, the full file next to the plan, projected against actual |
| `finish` | Collects the finish summary (rulings, commits or checkpoints, test counts, open incidents) |

`_pyrun.py` is a shared helper, not a tool. Probes live in `probes/` with the probe pack in `probes/pack/`.

## Hung test run probe

A probe, not a watchdog: tests can legitimately run long, so by default it reports and never kills. While `run_tests` has a test process alive it samples every `sample_interval_s` (12 s) the output bytes of the process tree, its CPU time and its set of child processes, but only after the run passes 3x its expected time (`--expect SECONDS`, else env `SUPERLITE_TEST_EXPECT_S`, else the last green run of the same mode, else `fallback_expected_s`), so a normal run pays nothing. Two consecutive samples classify the run: output and children flat with CPU flat is `blocked` (deadlock or wait), with CPU busy `spinning` (likely an infinite loop), with CPU time unavailable `stalled`; anything else keeps waiting. A verdict that holds for `min_flat_s` is a SUSPECT HANG.

On a suspect `run_tests` prints one `SUSPECT HANG: <VERDICT> <test file> - no output for Ns, cpu P% (...); process tree: ...` line, logs a `perf` incident, and writes a provisional `test-summary.json` (`failed: 1`, `running: true`) plus a `test-summary-<id>.suspect` sidecar for a background run. The run itself keeps going. `run_tests --wait` returns at once with exit 3 and that line when it sees the sidecar (`--patient` keeps waiting); a wait that runs out exits 2 with `TIMEOUT`. A killed or crashed test command is worded `process killed or crashed (exit -1)` (Windows exit 4294967295), never a bare exit code.

`config/testprobe.json` holds the thresholds (data, not code): `enabled`, `multiplier` (3), `fallback_expected_s` (60), `min_start_s` (30), `sample_interval_s` (12), `min_flat_s` (30), `cpu_idle_pct` (2), `cpu_busy_pct` (20), `auto_kill_blocked` (false), `auto_kill_after_s` (300), `hard_timeout_s` (0, off), `max_tree_listed` (8). `auto_kill_blocked` kills a BLOCKED run at about 0% CPU after `auto_kill_after_s`; `hard_timeout_s` kills any run after that many seconds and records a TIMEOUT. Both are process-tree kills by PID (`taskkill /T /F /PID`, or `killpg`), never by name. Override the file with env `SUPERLITE_TESTPROBE_CONFIG=<path>`, turn the probe off with `SUPERLITE_TESTPROBE=0`.

Summary fields, all additive (a run the probe saw nothing in keeps the old shape exactly): `verdict` (`SUSPECT HANG` or `TIMEOUT`), `suspect_hang` (`verdict`, `flat_s`, `cpu_pct`, `cpu_method`, `test_file` when known, `tree` as pid and name, `recovered`, `elapsed_s`, `killed_after_s` when killed, and the printed `line`), `timeout_s` (hard timeout kill) and `running: true` (provisional summary only). `failures` leads with the same SUSPECT HANG or TIMEOUT text.

CPU time of the process tree, first that works: psutil if importable; Windows, ctypes (Toolhelp32 process snapshot for the tree, `GetProcessTimes` for the CPU, no spawn and no PowerShell); Linux `/proc/<pid>/stat`; macOS and others `ps`. `SUPERLITE_TESTPROBE_BACKEND=psutil|ctypes|proc|ps|none` forces one. When none works the probe degrades to output and children only (verdict `stalled`) and never errors.
