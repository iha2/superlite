---
description: Hand a run to a teammate by email; they take over its ownership. Not every factory supports it.
argument-hint: "<run> <email>"
---

The prompt hook answers `/superlite:give` first. If it reaches Claude, run `superlite-cli cloud give $ARGUMENTS` and print its output only. Change nothing else.

A `<factory>:<ref>` address (for example `warp:1a2b3c4d`) names a run on that factory; a bare ref uses the active factory. For a non-cloud factory run `superlite-cli factory give $ARGUMENTS` instead.
