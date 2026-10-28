---
description: Stop dispatching new superlite tasks cleanly and report where the build stands.
argument-hint: ""
---

Stop the build cleanly. Dispatch no new tasks. Let running agents finish (do not kill processes). Checkpoint nothing half-done; leave unfinished work untouched. Write a short note to the active session's `stopped.md` (`sl session path` / `.superlite/sessions/<id>/stopped.md`, or legacy `.superlite/stopped.md`) with: tasks checkpointed, tasks running or unfinished, and the command to continue (`/superlite:resume`). Report the same in under 10 lines. Do **not** run `sl session end` here — the session stays so resume can continue; `sl finish` (or a later `sl session start` for the same host) is what deletes it.
