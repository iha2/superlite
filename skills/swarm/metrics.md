# Metrics, history and budget

Reference for `sl metrics`, `sl history`, `sl budget` (D37). Language-neutral; stdlib Python only.

## Capture
Two hooks append to `.superlite/metrics.jsonl` (epoch ms in `t`):
`{"ev":"launch","t","agent_id","type","model","desc"}` and `{"ev":"stop","t","agent_id","type","transcript","last"}`.
`transcript` is the subagent's own JSONL (`agent_transcript_path`).

## `sl metrics [--plan P] [--json] [--task Tn]`
Joins launch and stop by `agent_id`, reads each transcript once (usage per assistant message, deduplicated by message
id; tool calls; timestamps), prices tokens from `config/models.json`, and maps agents to tasks through
`.superlite/pool.json` or the `Tn` in the launch description. An agent reused for several tasks is split at the user
messages that name a `Tn`. Fields match `bench/mechanical.py`: task, kind, model, agent, agent_type, dispatches,
start_s, end_s, duration_s, tokens, cost_usd, tool_calls, status, complete, rework_dispatches, rework_s, rework_cost,
kind_miss_incidents, fix_notes, first_time_pass, done_s, start_lag_s, slack_s, on_critical_path.
Works offline after the run; well under a second.

## `sl history write|read|derive|import`
Append-only `~/.superlite/history.jsonl` (override with `SUPERLITE_HOME`), one aggregate row per task per run (schema in
the header of `history.py`), a per-project copy under `.superlite/`, and `stats.json` by band
(`kind/loc band/tests`) and by tier: n, first-time pass rate, mean and p90 duration, mean cost.
`import` converts `bench/results/*.json` score files (idempotent). `finish --plan $PLAN` writes the run's rows and
refreshes stats. Rows hold no code, prompts or file contents.

## Feed forward (deterministic: same plan plus same history gives the same result)
- `predict.load_coef()` refits from history when at least 30 usable rows exist (`SUPERLITE_HISTORY_MIN`), else the
  shipped `config/predictor.json`; the size band's `high_min` follows the refit's p90. An explicit `--coef` is never
  replaced.
- `routing.json` `stats_gate`: a tier|band with at least `min_n` samples and a first-time pass rate below
  `min_first_time_pass` stays on Sonnet. `lint --stable` prints `stats: n=..` (and which tasks the gate moved) plus the
  predicted time and cost, marked history-backed or shipped.

## `sl budget [--plan P] [--changed] [--json]`
One line: elapsed, cost so far, projected finish and cost against `**Target-min:**` and `**Budget-usd:**`, `[stats n=..]`.
`WARN` at 70% of the cap; `OVER CAP`: no optional work (extra review rounds, fix rounds beyond the first), finish what
is in flight; `LATE: critical path T1 -> T5` when the projection overruns the target. `lint` and `dispatch` print it
only when it changed (`.superlite/budget.state`) and only when `metrics.jsonl` exists.
Soft controls only. Harder controls proposed, not built: a PreToolUse hook that refuses new `Agent` calls over the cap;
auto-downgrading optional reviewers to the haiku tier; stopping the run at 100%.

## Coordinator, accuracy and reconcile
- The coordinator is its own line: `metrics.py --json --all` returns `{coordinator, tasks}` (turns, tokens, cost by model, median and
  peak context, duration). Its transcript is a `session_transcript` / `transcript_path` key on any metrics line, else derived from an
  agent transcript path (`<dir>/<session>/subagents/agent-X.jsonl` -> `<dir>/<session>.jsonl`). `sl budget` totals coordinator + agents
  (`coordinator $2.10 | agents $3.05 | total $5.15 of $8 cap`) and hints when the coordinator is 40%+ of the cost with 30+ turns at 60k+
  median context.
- Transcripts store an early snapshot of `output_tokens` per streamed message (one message id spans several entries), so output is
  topped up from text, thinking and tool-input length at 3.5 chars per token; thinking tokens are not stored and stay uncounted.
- `metrics.py --reconcile STREAM_LOG` compares coordinator + agents with the stream-json log's last `total_cost_usd`
  (diff, percent, which side is higher). On the z2 benchmark-style run (agents killed at the $3 budget): 2.78 vs 3.09 USD, -9.9%
  (metrics lower; before the output top-up it was -23%).
