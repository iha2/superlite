# Run archive: one ledger per run, history in `.superlite/runs/`

## Goal

Every superlite run starts with an empty ledger, and every past run stays readable. Today `.superlite/progress.md` and its siblings keep growing across runs that share a checkout, so `resume`, the scheduler and `finish` read checkpoints, dispatches and rulings from earlier runs (task ids like `T1` repeat across plans). This is for anyone running more than one flow in a checkout, and for `predict.py`, which learns from past runs.

## What changes

CLI (target kind: cli):

- `superlite-cli finish` ends by moving the run's ledger files from `.superlite/` into `.superlite/runs/<YYYYMMDD-HHMMSS>-<slug>/` and prints `run: archived to .superlite/runs/<id>`. The summary, report and after card are produced first, from the live files, exactly as today.
- The start of a run (the first `superlite-cli` call of a flow that finds leftover run files from an earlier run) archives them the same way, with `-unfinished` in the folder name, and prints `run: archived leftovers to …`. So a run that never reached `finish` still leaves the next one clean.
- `stop` and an interrupted run leave the live files alone: `resume` still works.
- The swarm skill no longer asks the agent to copy `progress.md` and `incidents.jsonl` next to the plan by hand.
- Archives are one per repo: every checkout and worktree, including prototypes, writes to the main checkout's `.superlite/runs/`. Session switch, close and discard never move or delete it. All archives are kept.
- History: each archived run gets a `run.json` (run id, slug, checkout, plan, archive time, finished or unfinished). History rows carry the run id as `run` plus a new `phase` (`proto` or `build`), so a row points at its archive and a run is never recorded twice. Rows no longer pick up earlier runs' agents. `predict.py` counts double-recorded rows once and refits only on non-prototype rows.
- The feature flow's integration build records history and writes its report (`finish --plan`).

## Acceptance checks

- Run a small flow to `finish`; `.superlite/` no longer holds `progress.md`, `incidents.jsonl`, `metrics.jsonl`; `.superlite/runs/<id>/` holds them plus `run.json`; `finish` printed the same summary as before plus the `run: archived` line.
- Start a second flow in the same checkout; its `progress.md` starts empty and `finish` reports only its own rulings and checkpoints.
- Kill a run mid-way (no `finish`), then `/superlite:resume`: the ledger is still there and resume continues.
- Kill a run mid-way, then start a new flow instead: the old files land in `.superlite/runs/<id>-unfinished/` and the new run starts empty.
- `finish` twice on the same run records history once.
- Finish a run inside a session worktree, then close and discard that session: its archive is still in the main checkout's `.superlite/runs/`.
- Session switch/close still parks and restores run files; `runs/` stays put.
- A history file with a run recorded twice (old behaviour) gives the same predict coefficients as with it once; prototype rows do not change the refit.
- A prototype's report still shows in the feature report after `proto discard`.
- The full existing test suite passes.

## Code map

Scripts are in `skills/swarm/scripts/` (stdlib Python, run through the bash launcher `superlite-cli <tool>`, which runs `<tool>.py`; no registry). Tests are `tests/scripts/test_*.py` (pytest via `uv run pytest`; in-process tests import modules via `sys.path`, while `scenario.py` drives the launcher in a temp repo; `conftest.py` points `SUPERLITE_HOME` at tmp).

- **Run folder.** `state.run_dir` gives `<checkout>/.superlite`, one per worktree. `state.hub_root` gives the main checkout, which holds `sessions.json` and `session-store/`.
- **Session moves.** `session_registry.py:37` `STATIONARY` lists what sessions never move. `run_entries`/`move_run_state`/`park_run_state`/`restore_run_state`/`clear_run_state` (:358-405) move or wipe everything else, so a new `runs/` must be added to `STATIONARY` or sessions would carry it and discard would delete it.
- **finish** (`finish.py:239` `main`): summary, then `history_section` (`history.record_run` + `report.write_report`, only with `--plan`), the after card, `clean_checkpoints`, `session.mark_run("finished")`, `exclusive.release` (reads `peers.json`), and the `widget:` line pointing at `.superlite/run-widget-after.html`.
- **Run start.** Every new flow runs `session ensure` (`session_ensure.py:137`) before anything else; `/superlite:resume` does not. The paths that matter:
  - "already open here" (:175): the same session, now starting a new flow.
  - `start_in_host` (:360-396): today it moves leftovers of an unregistered run aside and back.
  - parking a finished active session.
- **History.** Rows are written by `history.py:168` `run_rows`/`record_run`, to the global `$SUPERLITE_HOME/history.jsonl` and to `<run folder>/history.jsonl` + `stats.json`. The run id is `now()`, not linked to anything. There is no dedupe (only `import` dedupes, on run+task). Rows are built from `metrics.jsonl`, which only ever grows, so earlier runs' agents leak into new rows. Readers: `predict.py:77` `load_coef` (refits once ≥30 usable rows, no filtering, duplicates count), `history.project_stats` (routing/budget), and the plan_lint label.
- **Prototype report.** `proto.py:119` `proto_report` reads the prototype worktree's `.superlite/report.json`, which finish will now archive. `proto.py:147` `originating_run` tells whether a checkout is a prototype.
- **Research ledgers** (`research.py:30`, `.superlite/research/<slug>/`) are durable and keyed by slug; `research list` finds them live or parked.
- **Per-checkout config, not run state:** `test-cmd.json`, `agent-specs.json`, `host-models.json`.
- **Interfaces that must not change:** `finish` CLI flags and summary text (new lines are appended only); `session_registry` helper signatures; history row fields (new fields are added only); `predict.load_coef(path)`; `report.write_report`; `proto_report(st)`.
- **Docs to update:**
  - `skills/swarm/SKILL.md:99`: drop the manual archive step.
  - `commands/feature.md:20`: integration `finish --plan`.
  - `docs/sessions.md`: `runs/` stays in the hub.
  - `README.md:67,84` and `docs/tools.md`: describe `runs/`.

## Decisions

- Decided: `runs/` is one per repo at `<hub>/.superlite/runs/`, added to `STATIONARY`; every checkout and the prototype worktree archive there.
- Decided: keep all archives; no pruning.
- Decided: archive at the end of `finish` and at the start of a run (`session ensure`) when the checkout holds run files; never in `stop`/`resume`.
- Decided: fix history: the archive id is the row's `run`; `record_run` skips (run, task) already present; new `phase` field (`proto` when `originating_run` finds this checkout, else `build`); `predict` and stats dedupe on content (date, task, duration_s, tokens) so old double-recorded rows count once, and the refit and its 30-row threshold use only non-proto rows.
- Decided: integration `finish` gets `--plan`.
- Ruling: archive everything in the run folder except `STATIONARY`, `runs/`, `research/` and the per-checkout config files (because a clean start needs every per-run file gone, and those few files outlive runs).
- Ruling: no project filter in predict (because rows from other projects still improve the global fit; a project field without a reader would be unused).
- Ruling: `proto_report` falls back to the newest archive whose `run.json` checkout is the prototype worktree (because finish archives `report.json` before `proto discard` reads it).
- Ruling: a new flow started in a session whose run never finished archives that run as `-unfinished`; `/superlite:resume` is how to continue a run (because the user asked for an archive before every run; nothing is deleted).
- Assumption: archive id `<YYYYMMDD-HHMMSS>-<slug>`, the slug from this checkout's session row, else `run`; on a clash add `-2`.

## Prototype scope

Level L1, in the `run-archive-proto` worktree:
- **Keep:** the real `finish.py`, `session_ensure.py` and `session_registry.py`.
- **Build:** a new `run_archive.py` (pick the files, make the id, write `run.json`, move into the hub's `runs/`), called from `finish` and `ensure`, plus a `scripts/try_run_archive.sh` demo that runs two fake runs and an interrupted one in a temp repo and lists `runs/`.
- **Stub:** history, predict and proto_report stay unchanged.
- **Tests:** written against the acceptance checks for archiving.

## Integration outline

Tasks in the integration worktree, ported from the prototype rather than merged:
1. `run_archive.py` + `STATIONARY`.
2. `finish` wiring: run id passed to history, widget path rewritten to the archive.
3. `ensure` wiring: replace the leftovers' move-aside.
4. History: `run`, dedupe, `phase`.
5. `predict` + `project_stats` dedupe and proto filter.
6. `proto_report` fallback.
7. Docs and commands.
8. Seam test: a full finish → ensure → finish cycle that checks history rows, `runs/` contents and session park/restore.
