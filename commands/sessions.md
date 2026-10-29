---
description: List, switch, or discard superlite sessions in this worktree.
argument-hint: "[list|use <id>|discard <id>]"
---

Manage per-worktree superlite sessions. Arguments: $ARGUMENTS

Sessions isolate concurrent runs under `.superlite/sessions/<id>/`. Finish (and discard) delete a session tree; stop keeps it for resume.

1. Parse the first word of the arguments as the action when it is `list`, `use`, or `discard`. Otherwise the action is `list`.
2. Always run `sl session list` first and show its output (active marked with `*`, plus created/host/stopped/orch).
3. Action:
   - `list` (default): stop after the list. If more than one session exists, offer in one short question whether to `use` or `discard` one (skip the question in AI/headless runs).
   - `use <id>`: run `sl session use --id <id>`, then export the printed `SUPERLITE_SESSION=…` for this process. Report the new active id in one line. Missing id → ask which id from the list.
   - `discard <id>`: confirm only when the id is the active session or has a `stopped.md` the user may still resume; then run `sl session discard --id <id>`. Missing id → ask which id. Discarding the last session returns to the legacy flat layout.
4. Change nothing else (no plan edits, no finish). For the live run's progress use `/superlite:status`; to continue a stopped session, `use` it then `/superlite:resume`.
