---
description: Resume an interrupted superlite build from the last checkpointed, tested wave.
argument-hint: "[plan path]"
---

Resume a superlite build. Arguments: $ARGUMENTS
1. Find the plan (the argument, else the newest in docs/plans) and read `.superlite/` state (`- checkpoint Tn` lines in `progress.md`; older ledgers say `- commit Tn`) to see which tasks are checkpointed and tested.
2. Load `superlite:swarm` and continue from the ready queue. Never redo a checkpointed, passing task; re-run only tasks that were running or untested.
3. When `open_in_harness` is on (`sl agent_specs status`), open the human-facing document associated with the resumed build (`superlite:drafting`, After writing): use the `**Review plan:**` path when the execution plan has that header, or the execution plan itself only if it is a combined design+plan. Never open standalone machine, `.proto.md` or integration plans; skip opening if no human-facing document exists. Report in two lines what was already done and what will run now. Respect the orchestrator choice and the human approval gate.
