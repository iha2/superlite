# Hooks

`hooks/hooks.json` registers these seven. The run hooks are active only while a run marker exists (`.superlite/stable.json`, `prime.md` or `pool.json`, written by the first lint). They fail open on bad input.

- `session-start`: injects `using-superlite` at startup, clear and compact.
- `block-unsafe-kill`: always on for Bash and PowerShell; blocks killing processes by name or pattern (`taskkill /IM`, `pkill`, `killall`, `Stop-Process -Name`). Kill by PID is allowed.
- `block-coordinator-edits`: guards the coordinator (calls without `agent_id`); subagents always pass. In every state it denies the model writing `.superlite/orchestrator.json` and `.superlite/human-replies.jsonl`. While a human-orchestrator run is unapproved it denies orchestration: `superlite:*` and `general-purpose` agents and `sl dispatch`, `assign`, `wave0`, `build_prime`, `commit_task` and `finish`; file writes pass, so small inline work needs only the human's yes in chat. During a run it blocks the coordinator from writing project code; it may write `docs/plans/**`, `SPEC*.md`, `docs/**/*.md` and `.superlite/**`.
- `rewrite-test-runs`: during a run, rewrites the coordinator's raw test commands (pytest, npm test, go test, cargo test, dotnet test, ...) into a background `sl run_tests --full`, and logs a `rule-rewrite` incident.
- `record-human-reply`: on every prompt the human submits while a human-orchestrator run is unapproved, appends one line to `.superlite/human-replies.jsonl` (time, whether it approves, the first 160 characters). `sl orchestrator approve` needs an approving reply newer than the review plan. Only this hook writes the file; it never blocks.
- `capture-agent-launch`: during a run, appends a `launch` record (agent id, type, model, description) to `.superlite/metrics.jsonl` after each `Agent` call.
- `capture-agent-stop`: during a run, appends a `stop` record (agent id, type, transcript path, last message) to `.superlite/metrics.jsonl` when a subagent stops.
