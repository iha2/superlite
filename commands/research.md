---
description: Research a topic with parallel agents, a sourced ledger and a report at your level.
argument-hint: "[the topic or question]"
---

Superlite research mode. Topic: $ARGUMENTS

First, read `${CLAUDE_PLUGIN_ROOT}/assets/flows/research.txt` (if the variable is not expanded, it is `assets/flows/research.txt` in the superlite plugin directory) and print it verbatim in one ```text block, before anything else. It is stored, so never redraw or edit it. Skip it only when it was already printed this turn.

If the topic is empty, ask for it in one line (the question, and the language level: Director, VP, Researcher or Simple).
Once the request is known, and BEFORE the first other `superlite-cli` call, run `superlite-cli session ensure --slug <topic>` (`<topic>`: a short slug of the request, a-z, 0-9, `-`; reuse it for plan file names). On a `SESSION worktree=<path>` line, move the agent root to `<path>` when it differs from the current checkout; on `REDIRECT worktree=…`, open that worktree instead.
If ensure prints an `ASK` line (`ASK untracked=...` or `ASK move ...`, exit 4, nothing changed), ask exactly what the `ask:` line after it says, then do exactly what the chosen answer's `on ...:` line says (on a refusal, stop; nothing changed). If the hook printed `superlite sync:`, ask the same way; sync is fast-forward only.
Then load `superlite:research` and follow it: plan, critical questions, approval (human mode), parallel researcher agents writing to the ledger, contradictions, lint, then the report at the chosen level. Download files only after an explicit yes.
