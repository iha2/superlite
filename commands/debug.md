---
description: Find the root cause of a bug first, then fix it with a regression test.
argument-hint: "[the bug or failing test]"
---

Superlite debug mode. Problem: $ARGUMENTS

First, read `${CLAUDE_PLUGIN_ROOT}/assets/flows/debug.txt` (if the variable is not expanded, it is `assets/flows/debug.txt` in the superlite plugin directory) and print it verbatim in one ```text block, before anything else. It is stored, so never redraw or edit it. Skip it only when it was already printed this turn.

If the problem is empty, ask for it in one line (what fails, how to reproduce).
Once the request is known, and BEFORE the first other `sl` call, run `sl session ensure --slug <topic>` (`<topic>`: a short slug of the request, a-z, 0-9, `-`; reuse it for plan file names). On a `SESSION worktree=<path>` line, move the agent root to `<path>` when it differs from the current checkout; on `REDIRECT worktree=…`, open that worktree instead.
If ensure (or `use`/`close`) prints `ASK untracked=<N> slug=<s>: <files>` and exits 4, nothing changed. Ask in ONE AskUserQuestion: "Stash with <s>" / "Keep in the folder" / "Always stash (this repo)" / "Always keep (this repo)".
For an "Always" choice run `sl session untracked stash|keep` first; then rerun the same command with `--untracked stash|keep`.
Then load `superlite:diagnosis` and follow it: reproduce, find the root cause, write the failing regression test, fix, verify. Fixes go to fix-only agents by tier. Use the project's own test command. Do not widen the change beyond the cause.
