# Feature plan: end-of-run metrics widget

## Goal

When a superlite build finishes, show its metrics as a small inline card in the chat, so the person who ran it sees time, cost, quality and what was decided at a glance instead of reading the text summary. For anyone running superlite in a harness with an inline widget tool (Claude desktop's `show_widget`). Other harnesses keep the text summary unchanged.

## What changes

- **New tool `sl run_widget`** (`skills/swarm/scripts/run_widget.py`): reads `.superlite/report.json` and the progress ledger and prints one self-contained HTML fragment. With no `report.json` it prints nothing and exits 0.
- **`sl finish --plan PLAN` output**: after the report summary it writes `.superlite/run-widget.html` and prints one line:
  `widget: .superlite/run-widget.html (show it with an inline widget tool if you have one, e.g. show_widget; else skip)`.
  Without `--plan` or without metrics, no widget and no line.
- **`skills/swarm/SKILL.md`** "Freeze, rulings, finish": one short sentence, "If finish prints `widget:`, show that file verbatim." (stays under the 12,500 cap).
- **Docs**: a row in `docs/tools.md`, the tool name in the `sl` header comment.

What the card shows (all four groups the user picked):

1. **Time and cost headline**: wall-clock minutes and agent cost USD, projected -> actual with the delta; total cost (agents + rework + coordinator); agent count.
2. **Tasks and tests**: tasks reported, first-time pass `x/n`, rework dispatches, tests passed/failed, open incidents. Feature flow: prototype and integration minutes as one extra line.
3. **Cost by model**: one horizontal bar per model from `model_mix` (tasks, USD), plus the coordinator share when known.
4. **Rulings and commit state**: ruling count, `[scope]` changes count, and "Committed" vs "Nothing committed (N checkpoints)".

Card layout (example values; n/a where a value is missing):

```text
+--------------------------------------------------------------------------+
| superlite run  ·  feature: run-widget  ·  human orchestrator             |
|                                                                          |
| +----------------+ +----------------+ +----------------+ +-------------+ |
| | Wall time      | | Agent cost     | | Total cost     | | Agents      | |
| | 14.2 min       | | $1.84          | | $2.31          | | 6           | |
| | proj 12.0  +2.2| | proj 2.10 -0.26| | coord $0.47    | | proj 5  +1  | |
| +----------------+ +----------------+ +----------------+ +-------------+ |
|                                                                          |
| Tasks and tests                                                          |
|   tasks 8   first-time pass 6/8   rework 2                               |
|   tests 142 passed, 0 failed      open incidents 1                       |
|   prototype 3.1 min  ·  integration 11.1 min        (feature flow only)  |
|                                                                          |
| Cost by model                                                            |
|   sonnet       ##################..........  $1.20   5 tasks            |
|   haiku        ####......................    $0.22   3 tasks            |
|   coordinator  ###########...............    $0.47                      |
|                                                                          |
| Rulings 4  ·  scope changes 1  ·  Nothing committed (8 checkpoints)      |
+--------------------------------------------------------------------------+
```

Rendering rules: HTML fragment only (no `<html>`/`<body>`), CSS variables only (`--text-*`, `--surface-1`, `--border`), no scripts, no external loads, every number rounded, every value HTML-escaped, a visually hidden `<h2 class="sr-only">` summary first, n/a for missing values.

## Acceptance checks

- Run `sl run_widget --dir <fixture>/.superlite` on a finished-run fixture: it prints an HTML fragment containing the wall time, the total USD, the test counts, one bar per model and the ruling count; no `<script>`, no `<html>`.
- Run it on a `.superlite` with no `report.json`: no output, exit code 0.
- Run `sl finish --plan <plan>` on the fixture: `.superlite/run-widget.html` exists and the output ends with the `widget:` line; without `--plan` there is no `widget:` line.
- A ruling containing `<b>` appears escaped (`&lt;b&gt;`), not as markup.
- Pass the file to `show_widget` in Claude desktop: the card renders in light and dark mode with readable text and no horizontal scroll.
- Existing tests still pass, `skills/swarm/SKILL.md` stays at or under 12,500 characters.

## Code map

- **Entry points.** `skills/swarm/scripts/sl <tool>` runs `<tool>.py` with a Python 3.10+ found on the machine. The coordinator runs `sl finish --plan $PLAN` at the end of every orchestrated build (regular, MVP, feature, review), per `skills/swarm/SKILL.md` "Freeze, rulings, finish". Small and debug runs do not call finish and are out of scope.
- **Data sources (read only).** `report.py:write_report` writes `.superlite/report.json` (keys `headline`, `cost_split`, `model_mix`, `quality`, `feature`, `tasks`, `coordinator_cost_by_model`), called from `finish.py:history_section` when `--plan` is given and `metrics.jsonl` exists. `finish.py:read_ledger` returns rulings, checkpoints and the last tests line; `finish.scope_rulings` picks `[scope]` rulings; `autocommit.is_on()` tells commit vs checkpoint mode.
- **Touched files.** New `skills/swarm/scripts/run_widget.py`; `finish.py` (`main`: write the file and print the line after `summary`); `skills/swarm/SKILL.md` (one sentence); `docs/tools.md`; `sl` header comment; new `tests/scripts/test_run_widget.py`.
- **Interfaces that must not change.** `report.json` schema and `report.summary` text; `finish.summary(...)` signature and its text (tests parse it); `sl` dispatch; the `finish` output before the new last line.
- **Conventions.** Stdlib only; every line carries an `# O(...)` complexity comment and the file header has a "Complexity notation" block; deterministic output; fail open (exit 0); LF endings; tests are pytest under `tests/scripts/`, helpers `run`, `SCRIPTS` from `test_plan_tools`, run fixtures from `test_metrics_history.setup_run` and `test_routing.mech_plan`. Version bumps only via `scripts/bump-version.sh` (not part of this feature).
- **UI component set.** None in the repo; the host widget system supplies CSS variables and base styles, so the card is one-off markup on those tokens.

## Prototype scope

Keep: `run_widget.py` reading a real `report.json` shape and a ledger. Stub: a hand-written fixture `.superlite/` (report.json, progress.md, checkpoints.json) instead of a real build; no `finish.py` change. Level L1. Tried by rendering the output with `show_widget` here and checking light and dark. About 3 tasks: renderer, fixture, test.

## Integration outline

1. Port `run_widget.py` (renderer plus `main`), tests against a run fixture built with `setup_run` + `report.write_report`.
2. `finish.py`: after the summary, when `report.json` was written this run, write `run-widget.html` and print the `widget:` line; test both with and without `--plan`.
3. Wording and docs: the SKILL.md sentence (cap test), `docs/tools.md`, `sl` comment.
4. Seam check: `sl finish --plan` end to end on the fixture, the file renders, existing `test_report.py`, `test_orchestrator.py`, `test_mvp_check.py` pass.
