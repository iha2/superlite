---
description: Find the root cause of a bug first, then fix it with a regression test.
argument-hint: "[the bug or failing test]"
---

Superlite debug mode. Problem: $ARGUMENTS

First, read `${CLAUDE_PLUGIN_ROOT}/assets/flows/debug.txt` (if the variable is not expanded, it is `assets/flows/debug.txt` in the superlite plugin directory) and print it verbatim in one ```text block, before anything else. It is stored, so never redraw or edit it. Skip it only when it was already printed this turn.

If the problem is empty, ask for it in one line (what fails, how to reproduce). Then load `superlite:systematic-debugging` and follow it: reproduce, find the root cause, write the failing regression test, fix, verify. Fixes go to fix-only agents by tier. Use the project's own test command. Do not widen the change beyond the cause.
