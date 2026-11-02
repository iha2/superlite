# Feature plan: run cards before and after a build

## Goal

The end-of-run card (`run-widget.feature.md`, built on this branch) shows what a build cost and how it went. Its twin, shown right before the build starts, shows what the build is forecast to cost and how long it should take, so the person running superlite sees the bet before agents spend money, and can compare the two cards afterwards. For anyone running superlite in a harness with an inline widget tool (Claude desktop's `show_widget`). Other harnesses skip the line.

## What changes

- **`superlite-cli run_widget --before --plan PLAN [--dir .superlite]`**: prints the before card from `.superlite/projection.json` (the fixed forecast), the stable and fix task lists, and the plan's `**Budget-usd:**` cap. No `projection.json`: prints nothing, exit 0. Without `--before` it prints the after card, as today.
- **`plan_tools lint --stable`**: on the lint that first saves the forecast (the first stable lint, before any dispatch; in human mode after approval), it writes `.superlite/run-widget-before.html` and prints `widget: .superlite/run-widget-before.html (show it with an inline widget tool if you have one, e.g. show_widget; else skip)` just before its last line, so the output still ends with `DISPATCH NOW: ...`. Later lints keep the forecast and print no widget line.
- **The after card's file** is renamed `run-widget-after.html` so the pair reads as one set; `finish --plan` is otherwise unchanged.
- **`skills/swarm/SKILL.md`**: the finish sentence becomes one sentence covering both: "When lint or finish prints `widget:`, show that file verbatim with your inline widget tool (e.g. `show_widget`)."
- **Docs**: `docs/tools.md`: the `run_widget` row; "End-of-run card" becomes "Run cards" with one diagram covering both moments.

Before card, in the after card's frame and look:

```text
+--------------------------------------------------------------------------+
| superlite run forecast  ·  run-widget  ·  human orchestrator             |
| [Wall time 12.0 min | Agent cost $2.10, 70% of cap | Budget cap $3.00 | Agents 5, peak 4] |
| Tasks and critical path                                                  |
|   tasks 8   task-minutes 41.5   peak 4 at once                           |
|   critical path T1 > T3 > T5 > T7   11.2 min                             |
|   stable 7   fix first 1                                                 |
| Projected cost by model                                                  |
|   sonnet  $1.56 4 tasks | haiku $0.22 4 tasks | other $0.32 (when total > sum of tasks) |
+--------------------------------------------------------------------------+
```

The forecast's headline cost can exceed the sum of per-task costs; then one muted "other" bar covers the gap, so the bars always add up to the headline.

## Acceptance checks

- `run_widget --before --plan <plan> --dir <fixture>/.superlite` on a linted fixture prints a fragment with projected wall time, agent cost, cap and percentage, critical path, and one bar per model whose amounts add up to the headline; no `<script>`, no `<html>`.
- No `projection.json`: no output, exit 0. No `**Budget-usd:**`: cap n/a, cost tile says "no Budget-usd cap".
- First `lint --stable` on a fresh fixture writes `run-widget-before.html`, prints `widget:`, still ends with `DISPATCH NOW:`. A second lint prints no `widget:` line.
- `finish --plan` writes `run-widget-after.html` and ends with its `widget:` line.
- A task id or plan name containing `<b>` is escaped.
- Both cards render with `show_widget` in light and dark, readable, no horizontal scroll, one set.
- Existing tests pass; SKILL.md at or under 12,500 characters.

## Code map

- **Entry points.** `superlite-cli <tool>` runs `<tool>.py`. Coordinator runs `plan_tools lint --plan $PLAN --stable` after the machine plan (SKILL.md "Fixed order") and `finish --plan $PLAN` at the end. Human mode: `lint --stable` refuses before `orchestrator approve`.
- **Forecast.** `plan_lint.py:cmd_lint_stable` calls `_save_projection` after `_print_report` when `stable and not frozen`; `report.save_projection` writes `projection.json` once and returns its path, else None. Keys: `wall_min`, `cost_usd`, `agents`, `peak_width`, `sum_task_min`, `critical_path`, `critical_path_min`, `tasks{id: {pred_min, model, cost_usd}}`, `saved`, `plan_sha`.
- **Cap.** `budget.header_budget(plan)`.
- **Renderer.** `run_widget.py` (after card, `write`/`build`); `finish.widget_line` prints the line.
- **Touched files.** `run_widget.py` (shared frame; `render_before`, `build_before`; `widget_line` moved here), `plan_lint.py` (save the forecast before the report; print the line before `DISPATCH NOW`), `finish.py` (use `run_widget.widget_line`, new file name), `skills/swarm/SKILL.md`, `docs/tools.md`, `tests/scripts/test_run_widget.py`.
- **Must not change.** `lint --stable` lines and order, last line `DISPATCH NOW:` (SKILL.md and 10 tests: test_stable_split, test_wave0, test_metrics_history); `stable.json`, `lint-log.jsonl`, `projection.json` schemas; save-once forecast; `finish` output before its last line; `report.json`.
- **Conventions.** Stdlib; guard clauses first; nesting <= 3; complexity notes on def lines only; no `.superlite` string outside state.py; "checkpoint" never "commit"; fail open; behavior tests.
- **UI component set.** None; host CSS variables, one shared frame.

## Prototype scope

Keep: `render_before` on the shared frame, fed a real `projection.json` from `lint --stable` on a `mech_plan` fixture. Stub: no plan_lint/finish change, no rename. L1. Tried with show_widget, light and dark. About 2 tasks.

## Integration outline

1. `run_widget.py`: shared frame, before card, "other" bar, `widget_line`, `write(before=...)`, both file names; tests on a linted fixture.
2. `plan_lint.py`: save before the report; print the line before `DISPATCH NOW`; tests for first lint, second lint, last line.
3. `finish.py`: `run_widget.widget_line`, after file name; finish tests updated.
4. SKILL.md sentence (cap), `docs/tools.md` "Run cards" with one diagram.
5. Seam: lint then finish on one fixture gives both files; affected suites pass; full suite in background.
