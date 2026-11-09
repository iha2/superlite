---
description: Start a superlite session. superlite suggests the mode (regular, MVP, feature, debug, small, review, research) and asks who orchestrates.
argument-hint: "[regular|mvp|feature|debug|small|review|research] [what you want]"
---

Start a superlite session. Arguments: $ARGUMENTS

1. If the first word of the arguments is `regular`, `mvp`, `feature`, `debug`, `small`, `review` or `research`, that is the mode and the rest is the request: skip the suggestion. Skip it too when the user typed another `/superlite:` command or named a mode in words. Otherwise the mode is unset.
2. If there is no request text yet, ask for it in one line.
3. Mode unset: run `superlite-cli suggest_flow --text "<the request>"`. It prints one line, `suggest: <flow> - <reason> | also fits: <flows>`, from the request and the project state. Confirm it, or override it with a one-line reason when the request clearly says otherwise.
4. Ask what is still unset, in ONE AskUserQuestion (up to 2 questions):
   - Mode, if unset: the suggested flow first, labelled `(Recommended)` with its reason, then the "also fits" flows, then the rest that fit the project state. Regular (size the request, then brainstorm, plan, build, review), MVP (smallest working version first), Feature (a change to existing code, proved on a prototype you try first), Debug (find the cause, fix with a regression test), Small (a few files, inline), Review (review and harden a branch), Research (parallel sourced research, report at your level).
   - Orchestrator, always unless the user already said: Human (you approve a plain-language review plan before anything is built) or AI (hands off).
   - Once the mode and request are known, and BEFORE `superlite-cli orchestrator set`, run `superlite-cli session ensure --slug <topic>` (`<topic>`: a short slug of the request, a-z, 0-9, `-`; reuse it for plan file names). For Review, first resolve the review target the way `commands/review.md` says (before anything changes the checkout) and run `superlite-cli session ensure --slug <topic> --branch <target>` instead.
   - On a `SESSION worktree=<path>` line, move the agent root to `<path>` when it differs from the current checkout. On `REDIRECT worktree=…`, open that worktree instead.
   - If ensure prints an `ASK` line (`ASK untracked=...` or `ASK move ...`, exit 4, nothing changed), ask exactly what the `ask:` line after it says, then do exactly what the chosen answer's `on ...:` line says (on a refusal, stop; nothing changed). If the hook printed `superlite sync:`, ask the same way; sync is fast-forward only.
   - Then run `superlite-cli orchestrator set human|ai`.
5. Load the skill for the mode and follow it:
   - Regular: print `assets/flows/regular.txt` the same way (one ```text block, verbatim, never redrawn), then `superlite:using-superlite` sizing and `superlite:inventing`.
   - MVP, Feature, Debug (each prints its own diagram from `assets/flows/`), Small, Review: the same as `/superlite:mvp`, `/superlite:feature`, `/superlite:debug`, `/superlite:small`, `/superlite:review`, `/superlite:research` (loads `superlite:research`).
   - Resume or Status (a run in progress): the same as `/superlite:resume` or `/superlite:status`.

Menu by the "Project state" line in the session context (show only the options that fit):
- run in progress: Resume, Status, Start something new, Finish.
- existing code: Feature, Debug, Small, Review, Research, Regular, MVP (a new app beside it).
- empty folder: MVP (plan, clickable prototype to assess, then build), Regular, Small, Research.
Skip the menu for a clear one-line request you can size as trivial, or when the user already typed a `/superlite:` command.

**Start something new (run in progress).** Do not invent on top of another session’s live `.superlite/`. `superlite-cli session ensure` handles it: it separates the new run into its own worktree while the open session's run is open.

- `superlite-cli session split` and `superlite-cli session use` stay as manual tools. See `docs/sessions.md`.
- If `session use` reports a stash conflict: resolve, `git add`, then `superlite-cli session use --slug <slug> --continue`.
- If the user refuses any isolation: offer Resume or Finish only.

A **session HUD** line (`superlite session: …`) is injected each turn when a registry exists.
