---
description: Open a watch pane on a factory run's live events, beside this session.
argument-hint: "[run]"
---

The prompt hook answers `/superlite:watch` first. If it reaches Claude, run `superlite-cli cloud watch $ARGUMENTS` and print its output only. Change nothing else.

A `<factory>:<ref>` address (for example `warp:1a2b3c4d`) names a run on that factory; a bare ref uses the active factory. For a non-cloud factory run `superlite-cli factory watch $ARGUMENTS` instead.
