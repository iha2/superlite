---
description: Open the agent map pane for this run: main session and each subagent with tier, time, tokens, tasks and ledger. Read-only.
argument-hint: "[project dir]"
---

Open the agent map. Arguments: $ARGUMENTS

Call the `mcp__claude-agent-map__open_agent_map` tool with `dir` set to the given project folder (omit it for the working directory) and print its one-line answer. Change nothing else.
If that tool does not exist, the `claude-agent-map` mod is not loaded: say so in one line and give the install line `/plugin install claude-agent-map@superlite-dev` (terminal, after `/plugin marketplace add iha2/superlite`), or offer `/superlite:tail` instead.
