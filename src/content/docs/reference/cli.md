---
title: CLI tools
description: The tools you and the coordinator call through the launcher.
sidebar:
  order: 5
---

Run any tool as `bash skills/swarm/scripts/superlite-cli <tool> ...`.

| Tool | What it does |
|---|---|
| `plan_tools` | Plan lint, task graph, stubs, schedule, and the `adapter`, `packet`, `contract` review subcommands. |
| `graph` | Draws the plan's task graph as a mermaid flowchart. |
| `wave0` | Builds the skeleton brief for `implementer-haiku` from the plan. |
| `routing` | Maps each task's features to a tier using `routing.json`. |
| `predict` | Predicts time, context and cost per task; `--refit` fits coefficients. |
| `build_prime` | Builds the shared prime every agent reads first. |
| `task` | Prints task Tn plus the Produces lines of the tasks it consumes. |
| `dispatch` | `ready` tasks, `assign --task Tn`, or both; batches by tier. |
| `host_models` | Resolves a host profile once per run into `.superlite/host-models.json`. |
| `next_tasks` | Computes which tasks are ready. |
| `checkpoint` | Checks scope, then records tasks' owned files as hidden checkpoints. |
| `cost` | `line` for live spend, `table` for per-agent cost. |
| `agent_specs` | `on`, `off`, `status` for opt-in specs; `open` and `prefer` toggles. |
| `run_tests` | Runs the project's tests: `--background`, `--wait`, `--fast`, `--affected`, `--full --reuse`. |
| `testprobe` | The hung-test probe `run_tests` uses. |
| `testcmd` | Resolves the project's test command. |
| `pt_review` | `adapter`, `packet` and `contract` review steps. |
| `probe_run` | Parallel robustness probes with no agents (Python projects only). |
| `incident` | Records and queries the shared incident log. |
| `budget` | One line of elapsed time and cost against target and cap. |
| `metrics` | Per-task time, tokens and cost. |
| `history` | Run history: `write`, `read`, `derive`, `import`. |
| `graphify_gate` | Gates Graphify for search in non-trivial codebases. |
| `orchestrator` | `set human\|ai`, `approve --review <plan>`, `status`. |
| `report` | The end-of-build report, projected against actual. |
| `run_widget` | The before and after run cards as HTML fragments. |
| `suggest_flow` | `--text "<request>"`: one line naming the suggested flow. |
| `research` | Research ledger: `init`, `source`, `claim`, `contradiction`, `lint`, `render`, `list`. |
| `proto` | Feature prototype: `start`, `tryout`, `status`, `discard`. |
| `session` | `ensure`, `close`, `untracked`, `create`, `use`, `split`, `discard`, `status`, `sync`. |
| `mvp_check` | MVP mode: `build`, `stamp` and `check` the prototype page and feedback. |
| `finish` | Collects the finish summary; with `--plan` also writes the after card. |
| `tail` | Live feed of agent starts and finishes and ledger lines. |

The folder `skills/swarm/scripts/` also holds helper modules (session, compact and plan parts) that tools import. They are not called directly.

A checkpoint is a hidden save of one task under `refs/superlite/*`. superlite never commits during a run.
