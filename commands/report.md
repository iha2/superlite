---
description: List stored build reports, show one build's report summary, or the history across runs.
argument-hint: "[plan name] [full]"
---

Arguments: $ARGUMENTS. Every build keeps its report next to its plan: `<plan>.report.md` and `.report.json` (the latest is also `.superlite/report.md`).
- No argument: run `superlite-cli report --list` (newest first: plan, date, time and cost delta), then `superlite-cli history read --n 5` and a short table: date, size band, minutes, cost, review findings, probe failures.
- `<plan name>`: run `superlite-cli report --show <plan name>` and print the summary.
- `<plan name> full`: run `superlite-cli report --show <plan name> --full` and print it.
- Reports on the build just finished: `superlite-cli report --plan <plan>`. Change nothing else.
