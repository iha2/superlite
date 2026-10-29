---
description: Show the superlite run state: mode, orchestrator, plan approval, task progress, budget.
argument-hint: ""
---

Show the current superlite state in under 15 lines. Run `sl session list` (tell the user which sessions are still open), `sl orchestrator status` (mode and whether the plan is approved), `sl metrics` (task progress and cost so far; defaults to the active session dir) and, if a plan exists, `sl budget --plan <plan>`. Report: open sessions (or none), orchestrator, approved or not, tasks done/running/waiting, cost and time so far, and the next step. When any session is still open, point at `/superlite:sessions` to switch or discard. Change nothing.
