# Tools

Mechanical steps are scripts, not agents. Every tool runs as `bash <skill-dir>/scripts/sl <tool> ...`, where `<skill-dir>` is `skills/subagent-driven-development`. The `sl` launcher runs a `.sh` tool under bash and a `.py` tool under the first working Python, and exits 3 when there is none. It caches that interpreter, so each later call is one Python start (`SUPERLITE_PYTHON` skips the probe entirely). Tools that need each other import them in process instead of spawning `sl` again. The tools are Python, but the project can be any language.

A checkpoint is a hidden save of one task under `refs/superlite/*`; a commit is a real git commit on your branch, made only by `/superlite:commit` or `sl autocommit on`.

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
| `checkpoint` | Checks scope, then records one or more tasks' owned files as checkpoints (default: hidden `refs/superlite/*`, branch untouched; real commits with autocommit on); `--dispatch` also prints the next dispatch. `commit_task` is its old name and still works |
| `autocommit` | `on`/`off`/`status` opt-in real commits (`.superlite/autocommit.json`, env `SUPERLITE_AUTOCOMMIT=1`); `apply` turns checkpoints into commits on the current branch; `clean` deletes them |
| `run_tests` | Runs the project's test command, with `--background`, then `--wait` for the summary; writes `.superlite/test-summary.json` |
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
| `finish` | Collects the finish summary (rulings, checkpoints, or commits with autocommit on, test counts, open incidents) |

`_pyrun.py` is a shared helper, not a tool. Probes live in `probes/` with the probe pack in `probes/pack/`.
