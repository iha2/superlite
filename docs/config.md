# Config

In `skills/subagent-driven-development/config/`:

- `models.json`: prices (USD per million tokens) and output speed per model. Edit it when they change.
- `predictor.json`: the time and context coefficients and the size band `predict` uses. Refit with `sl predict --refit`.
- `gitignore.json`: ignore entries per language plus common ones; Wave 0 appends the missing ones to `.gitignore` before any agent runs.
- `testprobe.json`: the hung-test probe's thresholds (see [Tools](tools.md)).
- `routing.json`: task features to tier (`haiku` or `sonnet`), tier agents and limits, and the stats gate. A critical-path task never gets haiku.
