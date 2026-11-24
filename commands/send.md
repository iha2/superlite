---
description: Send a run to the active factory (superlite-cloud or an optional plugin factory such as warp) and print its name, status and link.
argument-hint: "<text> | --plan FILE [--superlite] [--title T] [--repo R] [--base B]"
---

Run `superlite-cli factory send $ARGUMENTS` and print its output only. Change nothing else.

`--plan FILE` sends the file as the prompt. `--superlite` prefixes the superlite preamble; it needs superlite switched on for the factory (`superlite-cli factory use <factory> --superlite`), else the command stops with exit 2.
