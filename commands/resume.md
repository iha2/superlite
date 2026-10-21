---
description: Resume an interrupted superlite build from the last checkpointed, tested wave.
argument-hint: "[plan path]"
---

Resume a superlite build. Arguments: $ARGUMENTS
1. Find the plan (the argument, else the newest in docs/plans) and read `.superlite/` state (`- checkpoint Tn` lines in `progress.md`; older ledgers say `- commit Tn`) to see which tasks are checkpointed and tested.
2. Load `superlite:subagent-driven-development` and continue from the ready queue. Never redo a checkpointed, passing task; re-run only tasks that were running or untested.
3. Report in two lines what was already done and what will run now. Respect the orchestrator choice and the human approval gate.
