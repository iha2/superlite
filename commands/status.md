---
description: Show the superlite run state: mode, orchestrator, plan approval, task progress, budget.
argument-hint: ""
---

Show the current superlite state in under 15 lines. Run `superlite-cli orchestrator status` (mode and whether the plan is approved), `superlite-cli metrics --dir .superlite` (task progress and cost so far) and, if a plan exists, `superlite-cli budget --plan <plan>` and `superlite-cli graph --plan <plan>`. Report: orchestrator, approved or not, tasks done/running/waiting, cost and time so far, and the next step; then, when a plan exists, the task graph in a ```mermaid block below the status lines, without its `# hash:` line. Change nothing.
