---
description: Approve the review plan so superlite can lint and build it.
argument-hint: "[path to the .review.md]"
---

Approve the human review plan. Arguments: $ARGUMENTS
Use the given path, else the newest `docs/plans/*.review.md`. When `open_in_harness` is on (`sl agent_specs status`), open the review plan for the user first (`superlite:drafting`, After writing). Show the user a five-line summary of what they are approving and ask only if the intent is unclear. Read their reply. If you understand it as approval, send approve: `sl orchestrator approve --review <path>`. Do not pass their sentence through and do not ask them to type the word approve. If you do not understand it as approval, ask what to change and end the turn. After approve succeeds, rewrite the plan in the agent format, lint it, and start the build.
