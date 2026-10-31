---
description: Make a small change inline, with a reviewer, without the full planning flow.
argument-hint: "[the change]"
---

Small change: $ARGUMENTS
Before any edit, run `sl session ensure --slug <topic>` (`<topic>`: a short slug of the change, a-z, 0-9, `-`). On `SESSION worktree=<path>` move the agent root there when it differs; on `REDIRECT worktree=…` open that worktree instead.
If ensure prints `ASK untracked=<N> slug=<s>: <files>` (exit 4, nothing changed), ask in ONE AskUserQuestion: "Stash with <s>" / "Keep in the folder" / "Always stash (this repo)" / "Always keep (this repo)"; for an "Always" choice run `sl session untracked stash|keep` first, then rerun with `--untracked stash|keep`.
If ensure prints `ASK move slug=<s>: ...` and exits 4, nothing changed. Ask: "Move it" / "Not now"; on "Move it" rerun ensure with `--move yes`.
If the hook printed `superlite sync:`, ask once: "Bring main in now" / "Later". "Now" runs `sl session sync --slug <s>`; "Later" runs it with `--later`. On `CONFLICT` (exit 5) dispatch a fixer agent to resolve the files and run the session's tests, then `sl session sync --slug <s> --continue`, or `--abort` if it cannot.
Do it inline in a few tool calls and say so; no plan document. Run the focused tests, then dispatch one `superlite:package-reviewer` on the diff before claiming done. If it turns out to span more than a few files or several chains, say so and switch to `/superlite:feature`.
