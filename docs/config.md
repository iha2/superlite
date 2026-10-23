# Config

In `skills/subagent-driven-development/config/`:

- `models.json`: prices (USD per million tokens) and output speed per model. Edit it when they change.
- `predictor.json`: the time and context coefficients and the size band `predict` uses. Refit with `sl predict --refit`.
- `gitignore.json`: ignore entries per language plus common ones; Wave 0 appends the missing ones to `.gitignore` before any agent runs.
- `testprobe.json`: the hung-test probe's thresholds (see [Tools](tools.md)).
- `routing.json`: task features to tier (`haiku` or `sonnet`), tier agents and limits, and the stats gate. A critical-path task never gets haiku. `batch`: how `dispatch` groups ready tasks of one tier into one agent message: at most `max_tasks` (3), with summed weight at most `capacity` (3), weight by the task's `Complexity N` (up to 3: 1, so three share an agent; 4-5: 1.5, so two; 6 and up: 3, alone; unknown counts as 5), summed `Est. context` within the tier's `max_context_k`. Critical-path tasks run alone, so what they unblock never waits behind a batch. `max_tasks: 1` turns batching off.
- `hosts/<host>.json` (e.g. `hosts/cursor.json`): per-IDE job → model slug candidates. `sl host_models resolve` writes `.superlite/host-models.json` once per run. Plans may set `**Execution:** host=cursor, parallel=multitask, swarm_by=tier` so `dispatch` prints a `SWARM` block; see `skills/using-superlite/references/cursor-tools.md`.
