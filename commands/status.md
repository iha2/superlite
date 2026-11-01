---
description: Show the superlite run state: mode, orchestrator, plan approval, task progress, budget.
argument-hint: ""
---

Show the current superlite state in under 15 lines. Run `superlite-cli orchestrator status` (mode and whether the plan is approved), `superlite-cli metrics --dir .superlite` (task progress and cost so far) and, if a plan exists, `superlite-cli budget --plan <plan>`. Report: orchestrator, approved or not, tasks done/running/waiting, cost and time so far, and the next step. Change nothing.
