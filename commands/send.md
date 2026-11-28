---
description: Send a run to the active factory (superlite-cloud or an optional plugin factory such as warp) and print its name, status and link.
argument-hint: "<text> | --plan FILE [--superlite] [--orchestrator human|ai] [--title T] [--repo R] [--base B]"
---

Run `superlite-cli factory send $ARGUMENTS` and print its output only. Change nothing else.

`--plan FILE` sends the file as the prompt. `--superlite` prefixes the superlite preamble; it needs superlite switched on for the factory (`superlite-cli factory use <factory> --superlite`), else the command stops with exit 2.

`--orchestrator human|ai` picks who answers for this run and beats the factory's saved default (AI unless set with `superlite-cli factory use <factory> --orchestrator human`). A human run on superlite-cloud stops on a question (its last line is `Reply to answer`) and on the plan; answer from `watch` or `attach`.
