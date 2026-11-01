# E13 brownfield: column widths for the CSV export

A feature task on an existing codebase that is big enough to trigger the Graphify gate (plan section 7:
at least 50 source files or at least 10k LOC). Run it with `BENCH_TASK=brownfield`; the harness copies `start/`
into the run dir and commits it before the arm starts (`seed_run_dir`).

## Contents

- `SPEC.md`: the feature (new `minisheet.csvio.parse_widths`, `widths=` on `to_csv_text`/`save_csv`, new package
  `sheetsite` with the `csv_widths` front matter key, `bridge.py` and a CLI). It is a feature request, not a rebuild.
- `start/`: the combined reference (sitegen + minisheet, with its own tests and `pyproject.toml`) plus a vendored
  copy of `rich` under `start/vendor/rich/` (unmodified package source and its `LICENSE`).
- `hidden/`: the 177 combo tests (`suite_minisheet`, `suite_sitegen`) as the regression check, plus
  `suite_feature/test_column_widths.py` with 24 feature tests. 201 tests in all.
- `reference_patch/column-widths.patch`: a reference implementation as a `git apply` patch (changes
  `src/minisheet/csvio.py` and `pyproject.toml`, adds `src/sheetsite/`).

## Vendored library

`rich` 15.0.0, MIT licensed (Copyright (c) 2020 Will McGugan), pure Python, fetched from PyPI with
`uv run --no-project --with rich` and copied from the installed package (`rich/` plus
`rich-15.0.0.dist-info/licenses/LICENSE`). It is not imported by anything; the SPEC forbids touching or using it.
Its own dependencies (`markdown-it-py`, `pygments`) are not vendored.

## Size (what the gate sees: tracked files, newline count)

| | Files | LOC |
|---|---|---|
| `start/` | 134 | 42,673 |
| seeded run repo (`start/` + `SPEC.md`) | 135 | 42,755 |
| of which `vendor/rich` | 102 (100 `.py`) | 38,534 |
| of which everything else (sitegen, minisheet, tests, `SPEC.md`, config) | 33 | 4,221 |

Both thresholds are crossed (50 files, 10k LOC), so the gate decides on `graphify` availability and install consent.
`superlite-cli graphify_gate --root <seeded run repo>`: `{"use": true, "reason": "graphify installed", ...}` with graphify on PATH;
with graphify off PATH and no `SUPERLITE_ALLOW_INSTALL`: `use` false, `needs_install` true.

## Validation

Using `bench/harness.py` `seed_run_dir` and `hidden_score` with `BENCH_TASK=brownfield`:

- `start/` without the patch: 177 / 201 pass; all 24 feature tests fail, the 177 regression tests pass.
- `start/` with `reference_patch/column-widths.patch` applied (`git apply` in the run dir): 201 / 201 pass.

## Notes

- Arms run with and without `SUPERLITE_ALLOW_INSTALL=graphify` (plan section 7); the task itself does not change between them.
- `bench/prompt.md` says "Build the project described in SPEC.md". For this task the SPEC says the repository already
  exists and asks for a feature; if that wording clashes in a run, use a task-specific prompt.
- The feature is deliberately cross-cutting (`minisheet.csvio`, `sitegen.frontmatter`, a new package, packaging config)
  so that finding the right files needs real navigation of a repo where most of the lines are vendored.
