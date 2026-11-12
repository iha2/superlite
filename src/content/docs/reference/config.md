---
title: Config
description: Config files in skills/swarm/config and the superlite.json overlay.
sidebar:
  order: 6
---

Config lives in `skills/swarm/config/`.

| File | Holds |
|---|---|
| `models.json` | Prices (USD per million tokens) and output speed per model. |
| `predictor.json` | Time and context coefficients for `predict`. |
| `gitignore.json` | Ignore entries per language; Wave 0 appends missing ones to `.gitignore`. |
| `testprobe.json` | Hung-test probe thresholds. |
| `routing.json` | Task features to tier (haiku or sonnet), tier limits, stats gate. |
| `hosts/cursor.json` | Per-IDE job to model-slug candidates. |

| Model | Input | Output | Cache read | Tokens per second |
|---|---|---|---|---|
| opus | 4.00 | 20.00 | 0.20 | 45 |
| sonnet | 2.00 | 10.00 | 0.10 | 80 |
| haiku | 0.10 | 0.50 | 0.01 | 150 |

Prices are USD per million tokens.

## Project overlay

A committed `superlite.json` at your project root overlays these defaults. A missing file changes nothing. `version` must be `1`.

| Key | Effect |
|---|---|
| `cap` | Dispatch cap. An explicit `--cap` wins. Default 10. |
| `gitignore` | Lines appended after the shipped list. |
| `hosts` | Replaces a job's candidate list. |
| `testprobe` | Threshold overrides. |
| `models` | Numeric prices on existing model names. |

Unknown keys exit 2 and name the file. The file does not replace `routing.json`, and a critical-path task never gets haiku.

## Environment

| Variable | Effect |
|---|---|
| `SUPERLITE_TESTPROBE=0` | Turns the hang probe off. |
| `SUPERLITE_TESTPROBE_CONFIG=<path>` | Uses another probe config. |
| `SUPERLITE_PYTHON` | Skips the interpreter probe. |
| `SUPERLITE_NO_AUTOCOMPACT=1` | Silences the compact nudge. |
| `SUPERLITE_NO_COST_REPORT=1` | Silences the cost line. |
