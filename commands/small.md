---
description: Make a small change inline, with a reviewer, without the full planning flow.
argument-hint: "[the change]"
---

Small change: $ARGUMENTS
Before any edit, run `sl session ensure --slug <topic>` (`<topic>`: a short slug of the change, a-z, 0-9, `-`). On `SESSION worktree=<path>` move the agent root there when it differs; on `REDIRECT worktree=…` open that worktree instead.
If ensure prints `ASK untracked=<N> slug=<s>: <files>` (exit 4, nothing changed), ask in ONE AskUserQuestion: "Stash with <s>" / "Keep in the folder" / "Always stash (this repo)" / "Always keep (this repo)"; for an "Always" choice run `sl session untracked stash|keep` first, then rerun with `--untracked stash|keep`.
Do it inline in a few tool calls and say so; no plan document. Run the focused tests, then dispatch one `superlite:package-reviewer` on the diff before claiming done. If it turns out to span more than a few files or several chains, say so and switch to `/superlite:feature`.
