# Facts: reference pages

Every fact ends with `(src: path)`. Paths are relative to the repo root. Note: the config folder is `skills/swarm/config/`, not a top-level `config/`.

## Skills (page: reference/skills)

| Skill | When it applies (frontmatter description) |
|---|---|
| `using-superlite` | Starting any conversation: explains superlite, how to size a request, which skill fits which task; injected at session start (src: skills/using-superlite/SKILL.md) |
| `inventing` | A request to build a feature or component or change behavior, before any implementation code (src: skills/inventing/SKILL.md) |
| `drafting` | An approved design or requirements for a multi-step task, to write an implementation plan before touching code (src: skills/drafting/SKILL.md) |
| `swarm` | Executing a plan that has a `%% taskgraph`: ready-queue scheduler over an idle agent pool, scripts for mechanical steps, selective reviews, packet-only final review (src: skills/swarm/SKILL.md) |
| `testing` | Implementing any feature or bugfix, before writing implementation code (src: skills/testing/SKILL.md) |
| `diagnosis` | Any bug, test failure or unexpected behavior, before proposing fixes (src: skills/diagnosis/SKILL.md) |
| `feature-prototype` | Feature mode after the feature plan is approved: cut the smallest runnable container of an existing app for the prototype build (src: skills/feature-prototype/SKILL.md) |
| `mvp-prototype` | MVP mode (`/superlite:mvp`) after the `.mvp.md` plan: make the one-file clickable prototype, check it, read the user's feedback (src: skills/mvp-prototype/SKILL.md) |
| `research` | The user wants a topic researched with sources, a report at a chosen language level, or conflicting evidence compared (src: skills/research/SKILL.md) |
| `proof` | About to claim work is complete, fixed or passing, before committing or creating PRs (src: skills/proof/SKILL.md) |
| `shipping` | Implementation complete and tests pass; decide how to integrate the work (src: skills/shipping/SKILL.md) |
| `merged` | The user says a pull request was merged: clean up its session, branch and local main (src: skills/merged/SKILL.md) |
| `upgrade-scan` | Review project changes and suggest adoption steps after a superlite upgrade (src: skills/upgrade-scan/SKILL.md) |
| `writing-specs` | Only when `agent_specs` is on or the user asks for a spec: what done looks like and why, before plan or implementation (src: skills/writing-specs/SKILL.md) |

- There are 14 skill directories under `skills/` (src: skills/).
- `docs/skills-and-agents.md` lists 12 of them (no `merged`, no `upgrade-scan`) (src: docs/skills-and-agents.md).

## Agents (page: reference/agents)

| Agent | Model | Effort | Tools | Role |
|---|---|---|---|---|
| `implementer` | sonnet | medium | Read, Edit, Write, Bash, Grep, Glob | Builds one plan task; primed with the shared prime (READY), then sent its task by message; stays idle after DONE for reuse (src: agents/implementer.md) |
| `implementer-haiku` | haiku | high | Read, Edit, Write, Bash, Grep, Glob | Builds one `Kind: mechanical` task as written; mini prime first; stops rather than guesses (src: agents/implementer-haiku.md) |
| `package-reviewer` | sonnet | medium | Read, Edit, Write, Bash, Grep, Glob | Reviews one checkpointed task (selective) or one package (batched) against the spec slice and incident hotspots, then fixes its own findings with regression tests (src: agents/package-reviewer.md) |
| `final-reviewer` | opus | high | Read, Grep, Glob | Fresh one-turn reviewer; reads only `.superlite/review-packet.md`; verdict plus tiered findings; never fixes (src: agents/final-reviewer.md) |
| `researcher` | sonnet | medium | Read, Write, Grep, Glob, Bash, WebSearch, WebFetch | Gathers evidence for one research sub-question into the ledger; never writes the report (src: agents/researcher.md) |
| `fixer-sonnet` | sonnet | medium | Read, Edit, Write, Bash, Grep, Glob | Tier 1: known cause, fix confined to one module, regression test, then checkpoint (src: agents/fixer-sonnet.md) |
| `fixer-sonnet-high` | sonnet | high | Read, Edit, Write, Bash, Grep, Glob | Tier 2: known cause spanning modules or a seam, or a known probe class (src: agents/fixer-sonnet-high.md) |
| `fixer-opus` | opus | high | Read, Edit, Write, Bash, Grep, Glob | Tier 3: unknown cause (reproduce, then fix) or a lower tier failed twice (src: agents/fixer-opus.md) |

- 8 agent files exist (src: agents/).
- No agent has the `Agent` tool: "No agent spawns agents" (src: docs/gates.md).
- Where a harness has no plugin agents, each agent runs as the harness's generic subagent with `agents/<name>.md` as its instructions (src: README.md).
- "Lower tier failed twice" and the haiku "stops rather than guesses" wording come from the summary (src: docs/skills-and-agents.md).

## Hooks (page: reference/hooks)

| Hook script | Claude Code event | Matcher | Job |
|---|---|---|---|
| `session-start` | SessionStart | `startup\|clear\|compact` | Injects `using-superlite`; first session per installed version also embeds `assets/intro.txt` (src: hooks/hooks.json) |
| `pre-tool` | PreToolUse | `Edit\|Write\|NotebookEdit\|Bash\|PowerShell\|Monitor\|Agent` | Runs kill guard, edit guard, test guard in order; first denial wins, a rewrite ends the run, bad input passes (src: hooks/hooks.json) |
| `prompt` | UserPromptSubmit | none | Combines reply recording, compact nudge, cost line into one JSON object (src: hooks/hooks.json) |
| `capture-agent-launch` | PostToolUse | `Agent` | During a run appends a `launch` record (id, type, model, description) to `.superlite/metrics.jsonl` (src: hooks/hooks.json) |
| `capture-agent-stop` | SubagentStop | none | During a run appends a `stop` record (id, type, transcript path, last message); time is the gap between the two records (src: hooks/hooks.json) |
| `capture-host-usage` | Stop | none | Appends a `host_usage` record when the payload includes usage; writes nothing without token or cost fields (src: hooks/hooks.json) |

- `hooks/hooks.json` registers one process per event (src: docs/hooks.md).
- Helper scripts, not registered hooks: `_kill`, `_edits`, `_rewrite`, `_reply`, `_nudge`, `_preamble` (src: hooks/).
- Run hooks are active only while a run marker exists: `.superlite/stable.json`, `prime.md` or `pool.json` (written by the first lint), or `orchestrator.json` or `feature.json` once a session has started. They fail open on bad input (src: docs/hooks.md).
- Kill guard (`_kill`): always on for Bash and PowerShell; blocks `taskkill /IM`, `pkill`, `killall`, `Stop-Process -Name`; kill by PID is allowed; `pkill -f` naming one project server (program with a port) or a path below the project root passes (src: docs/hooks.md).
- Edit guard (`_edits`): guards the coordinator only (subagents always pass). Always denies the model writing `.superlite/orchestrator.json`, `.superlite/feature.json`, `.superlite/human-replies.jsonl` (src: docs/hooks.md).
- Edit guard during a run: the coordinator may write only `docs/plans/**`, `SPEC*.md`, `docs/**/*.md` and `.superlite/**`, except the sandbox `.superlite/sandboxes/**` (src: docs/hooks.md).
- Edit guard in human mode before approval: denies `superlite:*` and `general-purpose` agents and `superlite-cli dispatch`, `assign`, `wave0`, `build_prime`, `checkpoint`, `finish`; file writes pass (src: docs/hooks.md).
- Same denial holds in feature mode while the prototype try-out waits for the human, until `superlite-cli proto tryout --approve` (src: docs/hooks.md).
- Test guard (`_rewrite`): during a run rewrites the coordinator's raw test commands into background `superlite-cli run_tests --full`, or `--fast <files>` when paths are named; logs a `rule-rewrite` incident (src: docs/hooks.md).
- Subagents may not run a bare full suite (exit 2); they use `--fast` (src: docs/hooks.md).
- A known runner inside an `until`/`while` loop or with stdout to `/dev/null` is denied (exit 2, `rule-block`), pointing to `run_tests --wait`; `until curl ...` and `until [ -f x ]` pass (src: docs/hooks.md).
- Reply recording (`_reply`, run by `prompt`): while a human run is unapproved, appends time, approves flag and first 160 characters to `.superlite/human-replies.jsonl`. It records only; it never blocks, and the flag does not decide (src: docs/hooks.md).
- Compact nudge (`_nudge`): when a session is due (about 35-40 min) adds an `additionalContext` nudge; silent with `SUPERLITE_NO_AUTOCOMPACT=1` (src: docs/hooks.md).
- Cost line: one-line `superlite cost: ...` only after the host reported usage; opt out with `SUPERLITE_NO_COST_REPORT=1` (src: docs/hooks.md).
- Cursor uses `hooks/hooks-cursor.json`: `sessionStart`, `beforeSubmitPrompt`, `preToolUse`, `subagentStart`, `subagentStop`, and `capture-host-usage` on `stop` and `afterAgentResponse` (src: docs/hooks.md).

## Gates (page: reference/gates)

| Gate | What it enforces | How |
|---|---|---|
| The coordinator cannot write code | Every change goes to a fix-only or implementer agent, by tier | `pre-tool` hook (src: docs/gates.md) |
| You approve before anything is orchestrated | In human mode nothing is linted, dispatched or sent to an agent until a typed reply approves the review plan | `pre-tool`, `prompt`, `superlite-cli orchestrator approve --review` (src: docs/gates.md) |
| No unsafe kills | No `pkill` or `taskkill /IM`; only PIDs the run started | `pre-tool` hook (src: docs/gates.md) |
| Tests never block the coordinator | Raw `pytest`, `npm test`, `go test` rewritten into background `superlite-cli run_tests` | `pre-tool` hook (src: docs/gates.md) |
| No hidden waits on tests | Test run in `until`/`while` or output to `/dev/null` denied; use `run_tests --wait` | `pre-tool` hook, hang probe (src: docs/gates.md) |
| A plan must lint before work starts | Stable tasks dispatch at once, others wait with a reason; size band, frozen interfaces | `lint --stable` (src: docs/gates.md) |
| Same plan, same agents | Task features pick the tier from `routing.json`; Haiku only for mechanical tasks off the critical path, never fixes | `routing.py` (src: docs/gates.md) |
| Agents stay in their tier | A task goes only to an idle agent of its own tier | `dispatch` (src: docs/gates.md) |
| Cost caps are enforced | Warns at 70% of the cap; over the cap stops optional work | `superlite-cli budget` (src: docs/gates.md) |
| Every run is measured | Tokens, cost and time captured and feed next predictions | capture hooks, `history.jsonl` (src: docs/gates.md) |
| No agent spawns agents | Subagents get explicit tool lists with no `Agent` | agent definitions (src: docs/gates.md) |

- Opening line: "Instructions drift. Scripts and hooks do not." (src: docs/gates.md)
- Approval counts for any clear approval (approve, yes, ok, go ahead, looks good, lgtm, proceed and similar); an explicit refusal, a real question, or a bare mention does not (src: docs/hooks.md).
- The approval gate needs a human reply newer than the review plan (src: docs/hooks.md).

## CLI tools (page: reference/cli)

- Run any tool as `bash <skill-dir>/scripts/superlite-cli <tool> ...` with `<skill-dir>` = `skills/swarm` (src: docs/tools.md).
- The launcher runs a `.sh` tool under bash and a `.py` tool under the first working Python; exits 3 when there is no Python; caches the interpreter; `SUPERLITE_PYTHON` skips the probe (src: docs/tools.md).
- There are no `.sh` tools today: `skills/swarm/scripts/` holds only `.py` files plus the `superlite-cli` launcher (src: skills/swarm/scripts/).
- Probes are `skills/swarm/probes/*.py`; the probe pack is `skills/swarm/probes/pack/` (src: docs/tools.md).
- `_pyrun.py` is a shared helper, not a tool (src: docs/tools.md).
- Tools needing each other import in process instead of spawning `superlite-cli` (src: docs/tools.md).
- A checkpoint is a hidden save of one task under `refs/superlite/*`; superlite never commits during a run (src: docs/tools.md).
- Full file list below (every non-underscore `.py` in scripts/ and probes/). "Doc" says whether docs/tools.md has a row named exactly that. Tools with no doc row are mostly helper modules; call names equal file names. `probe_run` is the documented probe tool; the other `probe_*` files are its parts (src: docs/tools.md).

| Tool (file stem) | Doc | What it does (module docstring) |
|---|---|---|
| `probe_common` | no (helper module) | Shared constants and helpers of the probe runner (see probe_run.py). Stdlib only. (src: skills/swarm/probes/probe_common.py) |
| `probe_pool` | no (helper module) | The parent side of the probe runner: worker processes, the pool, and one probe run (size steps). (src: skills/swarm/probes/probe_pool.py) |
| `probe_regress` | no (helper module) | Regression tests written from probe failures, and shrinking a failing size. (src: skills/swarm/probes/probe_regress.py) |
| `probe_run` | yes | Parallel probe runner (no agents). Stdlib only, Python >= 3.12. (src: skills/swarm/probes/probe_run.py) |
| `probe_store` | no (helper module) | Incident log, file selection and the pass cache of the probe runner. (src: skills/swarm/probes/probe_store.py) |
| `probe_worker` | no (helper module) | The worker side of the probe runner: a persistent subprocess that imports the adapter and runs probes. (src: skills/swarm/probes/probe_worker.py) |
| `agent_specs` | yes | Opt-in pre-implementation specs for feature (and later) flows. (src: skills/swarm/scripts/agent_specs.py) |
| `base` | no (helper module) | Shared base: git, json files, locks, processes, flags. Stdlib only. (src: skills/swarm/scripts/base.py) |
| `budget` | yes | One line of run-time control: elapsed, cost so far, projected finish and cost against target and cap. (src: skills/swarm/scripts/budget.py) |
| `build_prime` | yes | Build the shared agent prime: byte-identical for the same inputs. (src: skills/swarm/scripts/build_prime.py) |
| `checkpoint` | yes | Checkpoint one or more tasks: record only each task's Owns files, block anything else. (src: skills/swarm/scripts/checkpoint.py) |
| `checkpoint_import` | no (helper module) | Import-check driver for checkpointed Python modules. Stdlib only. (src: skills/swarm/scripts/checkpoint_import.py) |
| `claude_create` | no (helper module) | Claude Code creates the session worktree, superlite adopts it. Stdlib only. (src: skills/swarm/scripts/claude_create.py) |
| `compact_check` | no (helper module) | Compact check for the prompt hook: token nudge from the live transcript, else the old timer. (src: skills/swarm/scripts/compact_check.py) |
| `compact_due` | no (helper module) | Session compact due check and host-neutral nudge text. (src: skills/swarm/scripts/compact_due.py) |
| `compact_host` | no (helper module) | Per-host compact command lookup. (src: skills/swarm/scripts/compact_host.py) |
| `compact_log` | no (helper module) | Nudge ledger for token-based compaction. (src: skills/swarm/scripts/compact_log.py) |
| `context_tokens` | no (helper module) | Live context size and compact boundaries from a Claude Code transcript (JSONL). (src: skills/swarm/scripts/context_tokens.py) |
| `cost` | yes | Run cost: the live host spend line, the per-agent cost table, and the host-truth overlay. (src: skills/swarm/scripts/cost.py) |
| `dispatch` | yes | Ready tasks and who runs each, in one call. (src: skills/swarm/scripts/dispatch.py) |
| `exclusive` | no (helper module) | Suspend peer process plugins for the length of a superlite run. (src: skills/swarm/scripts/exclusive.py) |
| `finish` | yes | Print the closing summary of a run. (src: skills/swarm/scripts/finish.py) |
| `gitignore` | no (helper module) | Dependency, build and cache paths per language, from config/gitignore.json (data, not code). (src: skills/swarm/scripts/gitignore.py) |
| `graph` | yes | Mermaid flowchart of a plan's %% taskgraph block, for the chat to draw. (src: skills/swarm/scripts/graph.py) |
| `graphify_gate` | yes | Decide whether Graphify should be used for this repo. Prints one JSON object. (src: skills/swarm/scripts/graphify_gate.py) |
| `history` | yes | Persistent run history: one aggregate row per task per run, and stats derived by feature band. (src: skills/swarm/scripts/history.py) |
| `host_harness` | no (helper module) | Host harness facts: are we running under Claude Code, and the line that tells the agent to enter a worktree. (src: skills/swarm/scripts/host_harness.py) |
| `host_models` | yes | Resolve a host profile (e.g. Cursor) once per run into `.superlite/host-models.json`. (src: skills/swarm/scripts/host_models.py) |
| `ident` | no (helper module) | Path identity: whole-path comparison, never string prefixes. Stdlib only. (src: skills/swarm/scripts/ident.py) |
| `incident` | yes | Append-only incident log: add, hotspots, summary. Stdlib only. (src: skills/swarm/scripts/incident.py) |
| `journal` | no (helper module) | Undo journal: run steps, and on failure undo the finished ones newest first. Stdlib only. (src: skills/swarm/scripts/journal.py) |
| `metrics` | yes | Per-task records from a run's `.superlite/metrics.jsonl` (written by the capture hooks). (src: skills/swarm/scripts/metrics.py) |
| `mvp_check` | yes | MVP mode: build, stamp and check the prototype page and its feedback file (skills/mvp-prototype). (src: skills/swarm/scripts/mvp_check.py) |
| `next_tasks` | yes | Print the ready tasks, longest predicted Est. time first, within the cap. (src: skills/swarm/scripts/next_tasks.py) |
| `orchestrator` | yes | Who orchestrates: a human (approves the plan before it runs) or an AI (does everything itself). (src: skills/swarm/scripts/orchestrator.py) |
| `plan_lint` | no (helper module) | Lint for plan_tools: placeholders, Owns, Consumes/Produces, diagrams, size caps, CHAIN, Kind, spec coverage, (src: skills/swarm/scripts/plan_lint.py) |
| `plan_schedule` | no (helper module) | Task graph and schedule for plan_tools: parse the `%% taskgraph` block, simulate the ready queue, report the (src: skills/swarm/scripts/plan_schedule.py) |
| `plan_stubs` | no (helper module) | Interface signatures and stubs for plan_tools: parse a `name(args) -> ret` Produces line, resolve its module, (src: skills/swarm/scripts/plan_stubs.py) |
| `plan_tools` | yes | plan_tools: mechanical tooling for superlite plans. (src: skills/swarm/scripts/plan_tools.py) |
| `planfile` | no (helper module) | The one plan reader: tasks, their fields, the task graph, and which edges a consumer must wait on. (src: skills/swarm/scripts/planfile.py) |
| `predict` | yes | predict.py: the stored predictor for task time, context and cost. (src: skills/swarm/scripts/predict.py) |
| `project_config` | no (helper module) | Load `superlite.json` at the root of the project being built. (src: skills/swarm/scripts/project_config.py) |
| `proto` | yes | The feature flow's prototype: a sandbox folder in this checkout, the human's try-out, then discard. (src: skills/swarm/scripts/proto.py) |
| `proto_discard` | no (helper module) | Discard the open prototype: save its report, archive the build's run files, remove the sandbox, mark discarded. (src: skills/swarm/scripts/proto_discard.py) |
| `proto_lint` | no (helper module) | Stay-inside lint: a `.proto.md` plan may only write inside the open prototype sandbox. Stdlib only. (src: skills/swarm/scripts/proto_lint.py) |
| `pt_review` | yes | Review-side plan tools: adapter, packet, contract. (src: skills/swarm/scripts/pt_review.py) |
| `report` | yes | End-of-build report: actual metrics next to the projected ones. (src: skills/swarm/scripts/report.py) |
| `research` | yes | Level-free research evidence ledger. (src: skills/swarm/scripts/research.py) |
| `research_render` | no (helper module) | Render a level-free research ledger at four audience levels, and check parity. (src: skills/swarm/scripts/research_render.py) |
| `review_adapter` | no (helper module) | Probe adapter and the live-server half of the seam check. (src: skills/swarm/scripts/review_adapter.py) |
| `review_contract` | no (helper module) | Seam checks that read the plan against the real code: Produces signatures and the data contract. (src: skills/swarm/scripts/review_contract.py) |
| `review_packet` | no (helper module) | Review packet: one size-capped file the reviewer reads in a single turn. (src: skills/swarm/scripts/review_packet.py) |
| `routing` | yes | Deterministic tier routing (D36): a task's tier comes from its plan features and config/routing.json. (src: skills/swarm/scripts/routing.py) |
| `run_archive` | no (helper module) | Run archive: choose a run's files, make its id, write run.json and move the files into the hub's runs folder. Stdlib only. (src: skills/swarm/scripts/run_archive.py) |
| `run_tests` | yes | Run the project's test command (testcmd: .superlite/test-cmd.json, else detection; pytest for Python), (src: skills/swarm/scripts/run_tests.py) |
| `run_widget` | yes | Run cards: the forecast right before a build and the metrics right after, each one self-contained HTML fragment. (src: skills/swarm/scripts/run_widget.py) |
| `sandbox` | no (helper module) | Prototype sandboxes: where they live, what is in them, whether a path is inside one. Stdlib only. (src: skills/swarm/scripts/sandbox.py) |
| `session` | yes | Worktree-and-branch sessions: ensure, create, close, use, split, discard, status, sync. (src: skills/swarm/scripts/session.py) |
| `session_archive` | no (helper module) | Archive manifest for closed sessions: <hub>/.superlite/session-store/<slug>/session.json. (src: skills/swarm/scripts/session_archive.py) |
| `session_close` | no (helper module) | Session close: archive the session (WIP on a private ref, run files, manifest), then drop its row. Stdlib only. (src: skills/swarm/scripts/session_close.py) |
| `session_ensure` | no (helper module) | Session start and end: ensure, create, close, untracked. Stdlib only. (src: skills/swarm/scripts/session_ensure.py) |
| `session_hud` | no (helper module) | One-line session HUD for hooks: slug, branch, worktree, status, stash. (src: skills/swarm/scripts/session_hud.py) |
| `session_reconcile` | no (helper module) | Reconcile sessions.json with git: worktree inventory, drift report, registry-only repair. Stdlib only. (src: skills/swarm/scripts/session_reconcile.py) |
| `session_registration` | no (helper module) | Rollback-safe session registration for existing linked and in-place checkouts. (src: skills/swarm/scripts/session_registration.py) |
| `session_registry` | no (helper module) | Session registry and the git/run-state helpers every session command shares. Stdlib only. (src: skills/swarm/scripts/session_registry.py) |
| `session_sandboxes` | no (helper module) | Session row list of prototype sandboxes: `row["sandboxes"]` holds one entry per topic. (src: skills/swarm/scripts/session_sandboxes.py) |
| `session_switch` | no (helper module) | Session switching: use, discard, split, status. Stdlib only. (src: skills/swarm/scripts/session_switch.py) |
| `session_sync` | no (helper module) | Fast-forward a session branch to local main: session_sync.py --slug S [--untracked stash/keep] [--later] [--root DIR] (src: skills/swarm/scripts/session_sync.py) |
| `session_wip` | no (helper module) | Session WIP on private refs: `refs/superlite/wip/<slug>` holds a snapshot of the working tree and the index. (src: skills/swarm/scripts/session_wip.py) |
| `state` | no (helper module) | The only module that spells the run-state folder. Stdlib only. (src: skills/swarm/scripts/state.py) |
| `suggest_flow` | yes | Suggest a superlite flow from the request text and the project state. Deterministic: same input, same line. (src: skills/swarm/scripts/suggest_flow.py) |
| `tail` | no (helper module) | Live feed of a superlite run: agent starts and finishes (metrics.jsonl) and ledger lines (progress.md), as they land. (src: skills/swarm/scripts/tail.py) |
| `task` | yes | Print one task's block from the CURRENT plan plus the Produces lines of every task it Consumes. (src: skills/swarm/scripts/task.py) |
| `testcmd` | yes | Resolve the project's test command, language-neutrally. (src: skills/swarm/scripts/testcmd.py) |
| `testprobe` | yes | Hung test run probe. Library for run_tests.py; `testprobe.py` alone prints the CPU method and the config. (src: skills/swarm/scripts/testprobe.py) |
| `tree_id` | no (helper module) | Working-tree fingerprint: sha256 over HEAD, the diff against HEAD, and each untracked not-ignored file's path and bytes. (src: skills/swarm/scripts/tree_id.py) |
| `wave0` | yes | Build the Wave 0 (skeleton) brief from the plan alone; the coordinator authors nothing. (src: skills/swarm/scripts/wave0.py) |
| `worktree_home` | no (helper module) | Where superlite cuts worktrees: `<hub>/.claude/worktrees/<name>` (D10). Stdlib only. (src: skills/swarm/scripts/worktree_home.py) |

Documented tool rows, key facts:

- `plan_tools`: plan lint, task graph, stubs, schedule (prints only), and `adapter`, `packet`, `contract` review subcommands (src: docs/tools.md).
- `lint --stable` saves the forecast once and prints a `widget:` line before its last line `DISPATCH NOW:` (src: docs/tools.md).
- `dispatch`: `ready`, `assign --task Tn`, or both; batches ready tasks of one tier per message; `--batch 1` turns batching off (src: docs/tools.md).
- `run_tests`: `--background`, `--wait`, `--affected [--base REF] [--files F...]`, `--full --reuse`, `--fast`; writes `.superlite/test-summary.json` (src: docs/tools.md).
- `run_tests --wait` exits 3 with the SUSPECT HANG line when it sees the sidecar (`--patient` keeps waiting); a timed-out wait exits 2 with `TIMEOUT` (src: docs/tools.md).
- Hang probe reports and never kills by default; it samples every 12 s only after a run passes 3x its expected time (src: docs/tools.md).
- `orchestrator`: `set human|ai`, `approve --review <plan>`, `status` (src: docs/tools.md).
- `session`: `ensure`, `close`, `untracked`, `create`, `use`, `split`, `discard`, `status`, `sync` (src: docs/tools.md).
- `proto`: `start`, `tryout` (`--approve`), `status`, `discard`; state in `feature.json` (src: docs/tools.md).
- `research`: `init`, `source`, `claim`, `contradiction`, `lint`, `render --level`, `downloads`, `download --yes`, `list` (src: docs/tools.md).
- `suggest_flow --text "<request>"`: prints the suggested flow among regular, mvp, feature, debug, small, review, research, resume, status (src: docs/tools.md).
- `agent_specs`: `on`/`off`/`status`; `open on|off`; `prefer on|off` (src: docs/tools.md).
- `history` stores rows in `history.jsonl` under `$SUPERLITE_HOME`, else `$XDG_DATA_HOME/superlite` (src: docs/tools.md).
- `tail`: live feed of agent starts/finishes and ledger lines; `superlite-cli tail --dir .superlite` (src: README.md).
- Run cards: `plan_tools lint --stable` writes `.superlite/run-widget-before.html`; `finish --plan` writes `.superlite/run-widget-after.html` (src: docs/tools.md).

## Config (page: reference/config)

Config files live in `skills/swarm/config/` (src: docs/config.md).

| File | Holds |
|---|---|
| `models.json` | Prices (USD per million tokens) and output speed per model (src: docs/config.md) |
| `predictor.json` | Time and context coefficients and size band for `predict`; refit with `superlite-cli predict --refit` (src: docs/config.md) |
| `gitignore.json` | Ignore entries per language plus common; Wave 0 appends missing ones to `.gitignore` before any agent runs (src: docs/config.md) |
| `testprobe.json` | Hung-test probe thresholds (src: docs/config.md) |
| `routing.json` | Task features to tier (haiku or sonnet), tier agents and limits, stats gate (src: docs/config.md) |
| `hosts/cursor.json` | Per-IDE job to model-slug candidates (src: docs/config.md) |
| `compact.json` | Exists in the folder; not described in docs/config.md (src: skills/swarm/config/compact.json) |

- Model prices, USD per million tokens (input / output): opus 4.00 / 20.00; sonnet 2.00 / 10.00; haiku 0.10 / 0.50 (src: skills/swarm/config/models.json).
- Cache read per million: opus 0.20, sonnet 0.10, haiku 0.01 (src: skills/swarm/config/models.json).
- Output speed (tokens per second): opus 45, sonnet 80, haiku 150 (src: skills/swarm/config/models.json).
- A critical-path task never gets haiku; fixed in code (src: docs/config.md).
- `routing.json` `critical_path_solo` (true) keeps critical-path tasks alone; `dispatch --batch 1` forces one task per agent (src: docs/config.md).
- Testprobe keys and defaults: `enabled`, `multiplier` 3, `fallback_expected_s` 60, `min_start_s` 30, `sample_interval_s` 12, `min_flat_s` 30, `cpu_idle_pct` 2, `cpu_busy_pct` 20, `auto_kill_blocked` false, `auto_kill_after_s` 300, `hard_timeout_s` 0 (off), `max_tree_listed` 8 (src: docs/tools.md).
- Env overrides: `SUPERLITE_TESTPROBE_CONFIG=<path>`, `SUPERLITE_TESTPROBE=0`, `SUPERLITE_TESTPROBE_BACKEND=psutil|ctypes|proc|ps|none`, `SUPERLITE_TEST_EXPECT_S` (src: docs/tools.md).
- Other env: `SUPERLITE_HOME`, `SUPERLITE_PYTHON`, `SUPERLITE_HOST`, `SUPERLITE_AGENT_SPECS`, `SUPERLITE_NO_AUTOCOMPACT=1`, `SUPERLITE_NO_COST_REPORT=1` (src: docs/tools.md).
- Project overlay: a committed `superlite.json` at the project root; a missing file changes nothing; `version` must be `1` (src: docs/config.md).
- `superlite.json` keys: `cap` (dispatch cap; explicit `--cap` wins; default 10), `gitignore`, `hosts`, `testprobe`, `models` (numeric prices on existing models) (src: docs/config.md).
- Unknown keys, including `skills`, exit 2 and name the file. It does not replace `routing.json` (src: docs/config.md).
- Hosts: plans may set `**Execution:** host=cursor, parallel=multitask, swarm_by=tier` so `dispatch` prints a `SWARM` block (src: docs/config.md).
- `host_models resolve` writes `.superlite/host-models.json` once per run (src: docs/config.md).

## Language (page: reference/language)

- The tools are Python, but the project being built can be in any language (src: docs/language.md).
- `run_tests` uses the project's test command: `.superlite/test-cmd.json`, else detection from marker files (src: docs/language.md).
- Detection covers npm, go, cargo, dotnet, Python (src: docs/tools.md).
- The probe stage (`probe_run`) is Python only (src: docs/language.md).
- `plan_tools stubs` writes Python stubs for Python; other languages get one `stub:` line per Produces signature and a `contract:` line, no `.py` file (src: docs/config.md).
- `plan_tools adapter` writes `.superlite/probes/adapter.py` for Python; other languages get `seam.ts`, `seam.js`, `seam.go`, `seam.rs`, `seam.cs`, `seam.java`, `seam.rb` or `seam.txt` (src: docs/config.md).
- Requirement: Python 3.12 or newer, git, bash (Git Bash on Windows); `uv` optional (src: README.md).

## Harnesses and install (page: reference/harnesses)

- Claude Code install: `/plugin marketplace add iha2/superlite` then `/plugin install superlite@superlite-dev` (src: README.md).
- One-session local load: `claude --plugin-dir <path-to-superlite>` (src: README.md).
- Optional agent map: `/plugin install claude-agent-map@superlite-dev` (src: README.md).
- Verify any install: open a fresh session and send `Let's make a react todo list`; a working install starts `inventing` before any code (src: README.md).
- If you use more than one harness, install separately for each (src: README.md).

| Harness | Install command (as in README) |
|---|---|
| Claude Code | `/plugin marketplace add iha2/superlite`, `/plugin install superlite@superlite-dev` (src: README.md) |
| Antigravity | `agy plugin install https://github.com/iha2/superlite` (src: README.md) |
| Codex App | `codex plugin marketplace add iha2/superlite`, then add from the Plugins sidebar; set `[features] multi_agent = true` in `~/.codex/config.toml`; marked Unverified (src: README.md) |
| Codex CLI | `codex plugin marketplace add iha2/superlite`, then `/plugins`; marked Unverified (src: README.md) |
| Cursor | `/add-plugin superlite` in Agent chat, or the plugin marketplace, or a local plugin (reads `.cursor-plugin/plugin.json`) (src: README.md) |
| Devin CLI | `devin plugins install iha2/superlite` (src: README.md) |
| Factory Droid | `droid plugin marketplace add https://github.com/iha2/superlite`, `droid plugin install superlite@superlite-dev` (src: README.md) |
| Gemini CLI | `gemini extensions install https://github.com/iha2/superlite` (src: README.md) |
| GitHub Copilot CLI | `copilot plugin marketplace add iha2/superlite`, `copilot plugin install superlite@superlite-dev` (src: README.md) |
| Grok Build CLI | `grok plugin marketplace add iha2/superlite`, `grok plugin install superlite@superlite-dev --trust`; marked Unverified (src: README.md) |
| Kimi Code | `/plugins install https://github.com/iha2/superlite`, then `/new` (src: README.md) |
| OpenCode | Tell OpenCode to fetch and follow `.opencode/INSTALL.md` from the repo's raw GitHub URL (src: README.md) |
| Pi | `pi install git:github.com/iha2/superlite` (src: README.md) |
| Qwen Code | `qwen extensions install iha2/superlite` (src: README.md) |
| Hermes Agent | `hermes plugins install iha2/superlite --enable`; restart active sessions; no post-compaction hook (src: README.md) |

- Tool-mapping files in `skills/using-superlite/references/`: antigravity, claude-code, codex, cursor, gemini, hermes, muse, pi (src: skills/using-superlite/references/).
- Codex App, Codex CLI and Grok are labeled "hasn't been tested yet" in README (src: README.md).

## Benchmark (page: results/benchmark)

Benchmarked: superpowers 6.4.1 vs superlite 0.1.0 (current then 1.6.0); date 2026-10-16, corrected 2026-10-17; task: build `sitegen` from `bench/task/SPEC.md` (9 modules, stdlib Python); 1 rep per arm (src: docs/benchmark-report.md).

| Metric | superpowers 6.4.1 | superlite 0.1.0 |
|---|---|---|
| Wall-clock | 34.4 min | 8.8 min, 3.9x faster (src: docs/benchmark-report.md) |
| Cost (API-equivalent) | $8.15 | $3.28, 60% cheaper (src: docs/benchmark-report.md) |
| Opus / Sonnet / Haiku cost | $6.43 / $1.72 / none | $1.53 / $1.75 / $0.002 (src: docs/benchmark-report.md) |
| Hidden acceptance tests | 89 / 89 | 89 / 89 (src: docs/benchmark-report.md) |
| Own tests written | 298 | 208 (src: docs/benchmark-report.md) |
| Stops-for-human | 0 | 0 (src: docs/benchmark-report.md) |
| Coordinator turns | 68 | 46 (src: docs/benchmark-report.md) |
| Coordinator context per request (median / max) | 108k / 154k | 70k / 90k (src: docs/benchmark-report.md) |
| Subagent dispatches | 21, strictly serial | 10, up to 5 concurrent; serial chain 21 to 3 (src: docs/benchmark-report.md) |
| Agent-busy wall time | 25.1 min | 4.8 min (src: docs/benchmark-report.md) |

- Correction: the first version summed every `result` event, inflating costs 3x (superpowers) and 9x (superlite); figures above are corrected (src: docs/benchmark-report.md).
- Parallel waves drive the speed; superpowers' review-after-every-task loop was about 12 minutes (src: docs/benchmark-report.md).
- Planning was not the bottleneck: about one minute each (src: docs/benchmark-report.md).
- Superpowers' 5 Opus dispatches took 15.8 minutes, 58% of agent time (src: docs/benchmark-report.md).
- Superlite's Opus read 2.3M cached tokens vs 8.7M (src: docs/benchmark-report.md).
- Caveats: n=1 per arm, timings could move +/-30%; the arms overlapped in time and shared rate limits; costs are API-equivalent; the hidden suite checks SPEC conformance only (src: docs/benchmark-report.md).
- Next steps: keep Opus final review, share caches across siblings, add a harder task, run 3 reps per arm sequentially (src: docs/benchmark-report.md).

## Open questions

- docs/tools.md has no rows for several tool files (e.g. `gitignore`, `ident`, `journal`, `session_*`, `compact_*`); the table marks them as helper modules by docstring only (src: skills/swarm/scripts/).
