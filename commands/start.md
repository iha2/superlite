---
description: Start a superlite session. superlite suggests the mode (regular, MVP, feature, debug, small, review) and asks who orchestrates.
argument-hint: "[regular|mvp|feature|debug|small|review] [what you want]"
---

Start a superlite session. Arguments: $ARGUMENTS

1. If the first word of the arguments is `regular`, `mvp`, `feature`, `debug`, `small` or `review`, that is the mode and the rest is the request: skip the suggestion. Skip it too when the user typed another `/superlite:` command or named a mode in words. Otherwise the mode is unset.
2. If there is no request text yet, ask for it in one line.
3. Mode unset: run `sl suggest_flow --text "<the request>"`. It prints one line, `suggest: <flow> - <reason> | also fits: <flows>`, from the request and the project state. Confirm it, or override it with a one-line reason when the request clearly says otherwise.
4. Ask what is still unset, in ONE AskUserQuestion (up to 2 questions):
   - Mode, if unset: the suggested flow first, labelled `(Recommended)` with its reason, then the "also fits" flows, then the rest that fit the project state. Regular (size the request, then brainstorm, plan, build, review), MVP (smallest working version first), Feature (a change to existing code, proved on a prototype you try first), Debug (find the cause, fix with a regression test), Small (a few files, inline), Review (review and harden a branch).
   - For Resume, Status, Sessions or Finish: route directly to the corresponding command, preserving the selected session and orchestrator; do not run `sl session start` or `sl orchestrator set`.
   - For a new build only, ask the orchestrator choice unless the user already said: Human (you approve a plain-language review plan before anything is built) or AI (hands off). Then run `sl session start --host-session <native-chat-id>` when the harness exposes a conversation/session id (else plain `sl session start`). Export the printed `SUPERLITE_SESSION=…` (and keep `SUPERLITE_HOST_SESSION` if set), then run `sl orchestrator set human|ai`. The new run is isolated under `.superlite/sessions/<id>/`; `sl finish` deletes it, a same-host new start replaces it, and `/superlite:stop` preserves it for resume.
5. Load the skill for the mode and follow it:
   - Regular: print `assets/flows/regular.txt` the same way (one ```text block, verbatim, never redrawn), then `superlite:using-superlite` sizing and `superlite:inventing`.
   - MVP, Feature, Debug (each prints its own diagram from `assets/flows/`), Small, Review: the same as `/superlite:mvp`, `/superlite:feature`, `/superlite:debug`, `/superlite:small`, `/superlite:review`.
   - Resume or Status (a run in progress): the same as `/superlite:resume` or `/superlite:status`.

Menu by the "Project state" line in the session context (show only the options that fit):
- run in progress: Resume, Status, Sessions (list/switch/discard), Start something new, Finish.
- existing code: Feature, Debug, Small, Review, Regular, MVP (a new app beside it).
- empty folder: MVP (plan, clickable prototype to assess, then build), Regular, Small.
Skip the menu for a clear one-line request you can size as trivial, or when the user already typed a `/superlite:` command.
