---
description: Find the root cause of a bug first, then fix it with a regression test.
argument-hint: "[the bug or failing test]"
---

Superlite debug mode. Problem: $ARGUMENTS

First, read `${CLAUDE_PLUGIN_ROOT}/assets/flows/debug.txt` (if the variable is not expanded, it is `assets/flows/debug.txt` in the superlite plugin directory) and print it verbatim in one ```text block, before anything else. It is stored, so never redraw or edit it. Skip it only when it was already printed this turn.

If the problem is empty, ask for it in one line (what fails, how to reproduce).
Once the request is known, and BEFORE the first other `superlite-cli` call, run `superlite-cli session ensure --slug <topic>` (`<topic>`: a short slug of the request, a-z, 0-9, `-`; reuse it for plan file names). On a `SESSION worktree=<path>` line that differs from the current checkout, under Claude Code on `session: create EnterWorktree name=<n>` call `EnterWorktree name=<n>` and rerun the same command inside it, on `session: enter EnterWorktree path=<p>` call `EnterWorktree path=<p>`, on `ASK branch-name` ask the user once and rerun with `--branch-name worktree|superlite|PATTERN`, elsewhere `cd` to `<path>`; on `REDIRECT worktree=…`, do the same for that worktree.
If ensure prints an `ASK` line (`ASK untracked=...` or `ASK move ...`, exit 4, nothing changed), ask exactly what the `ask:` line after it says, then do exactly what the chosen answer's `on ...:` line says (on a refusal, stop; nothing changed). If the hook printed `superlite sync:`, ask the same way; sync is fast-forward only.
Then load `superlite:diagnosis` and follow it: reproduce, find the root cause, run the `superlite:inventing` **Grill** (one round: correct behavior, what must not change, how far the fix may reach) when something material is open, write the failing regression test, fix, verify. Fixes go to fix-only agents by tier. Use the project's own test command. Do not widen the change beyond the cause. After verification passes, run `superlite-cli finish` in the session checkout: it marks the session finished and leaves uncommitted work in place.
