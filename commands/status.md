---
description: Show the superlite run state: mode, orchestrator, plan approval, task progress, budget.
argument-hint: ""
---

Show the current superlite state in under 15 lines. Run `sl session status` (active id), `sl orchestrator status` (mode and whether the plan is approved), `sl metrics` (task progress and cost so far; defaults to the active session dir) and, if a plan exists, `sl budget --plan <plan>`. Report: session, orchestrator, approved or not, tasks done/running/waiting, cost and time so far, and the next step. When more than one session is registered, mention `/superlite:sessions` to list/switch/discard. Change nothing.
