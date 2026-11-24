---
description: Attach this session to a factory run; plain lines then go to the run as nudges or answers until you detach.
argument-hint: "<run>"
---

The prompt hook answers `/superlite:attach` first. If it reaches Claude, run `superlite-cli cloud attach $ARGUMENTS` and print its output only. Change nothing else.

A `<factory>:<ref>` address (for example `warp:1a2b3c4d`) names a run on that factory; a bare ref uses the active factory. For a non-cloud factory run `superlite-cli factory attach $ARGUMENTS` instead.
