---
description: List your live runs on the active factory, or every run with `all`. Read-only.
argument-hint: "[all]"
---

The prompt hook answers `/superlite:runs` first. If it reaches Claude, run `superlite-cli cloud runs $ARGUMENTS` and print its output only. Change nothing else.

`runs` lists the active factory. For a non-cloud factory run `superlite-cli factory runs` (add `--all` for every run) instead.
