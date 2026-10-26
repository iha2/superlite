---
description: First-run check: toolchain, test command, model tiers, and conflicts with other plugins.
argument-hint: ""
---

Check and report, changing nothing without a yes:
- the project language and test command (`sl testcmd`, `test-cmd.json` or detection) and that it runs;
- Python 3.10+ for the `sl` tools;
- the model tiers in `config/models.json` and `config/routing.json`;
- whether a superlite run has superpowers suspended (`.superlite/peers.json`); it stays suspended until `sl finish`, then superpowers can be used again;
- whether `.superlite/` is gitignored.
Give a pass or fix line for each.
