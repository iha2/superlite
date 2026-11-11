---
description: Make a small change inline, with a reviewer, without the full planning flow.
argument-hint: "[the change]"
---

Small change: $ARGUMENTS
When invoked independently (not from `/superlite:start`), before any edit run `superlite-cli session ensure --slug <topic>` (`<topic>`: a short slug of the change, a-z, 0-9, `-`). On `SESSION worktree=<path>` that differs from the cwd, under Claude Code on `session: create EnterWorktree name=<n>` call `EnterWorktree name=<n>` and rerun the same command inside it, on `session: enter EnterWorktree path=<p>` call `EnterWorktree path=<p>`, on `ASK branch-name` ask the user once and rerun with `--branch-name worktree|superlite|PATTERN`; else `cd` there; on `REDIRECT worktree=…` do the same for that worktree.
If ensure prints an `ASK` line (`ASK untracked=...` or `ASK move ...`, exit 4, nothing changed), ask exactly what the `ask:` line after it says, then do exactly what the chosen answer's `on ...:` line says (on a refusal, stop; nothing changed). If the hook printed `superlite sync:`, ask the same way; sync is fast-forward only.
Do it inline in a few tool calls and say so; no plan document. Run the focused tests, then dispatch one `superlite:package-reviewer` on the diff before claiming done. After a successful review run `superlite-cli finish` in the session checkout: it marks the session finished and leaves uncommitted work in place. If it turns out to span more than a few files or several chains, say so and switch to `/superlite:feature`.
