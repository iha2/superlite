---
description: Open a watch pane on a factory run's live events, beside this session.
argument-hint: "[run]"
---

The prompt hook answers `/superlite:watch` first and hands you the command to run; it opens nothing itself. Open a new terminal, tab or pane for the user, beside this session, that runs `superlite-cli cloud watch $ARGUMENTS`, using whatever this harness offers. Never run it inline in this session: it does not exit and would block you. If you cannot open one, show the user the exact command to run. Change nothing else.

A `<factory>:<ref>` address (for example `warp:1a2b3c4d`) names a run on that factory; a bare ref uses the active factory. For a non-cloud factory the command is `superlite-cli factory watch $ARGUMENTS` instead.

Guard: `hooks/pre-tool` denies a foreground `cloud watch` or `factory watch` Bash call (run it with `run_in_background` only), and the CLI itself prints this handoff instead of streaming when an agent harness runs it without a terminal; `--follow` (or `SUPERLITE_WATCH_INLINE=1`) streams anyway.
