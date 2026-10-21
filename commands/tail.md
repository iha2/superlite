---
description: The live feed of this run in a terminal (agent starts and finishes, ledger lines), for where the agent map mod is not installed. Read-only.
argument-hint: "[project dir]"
---

Tail the run. Arguments: $ARGUMENTS

The feed is `bash "$SL" tail --dir <project>/.superlite` (`SL` = `<superlite skill dir>/subagent-driven-development/scripts/sl`; project = the given folder, else the working directory). It waits for the folder if the run has not started. A PowerShell tab often has no `bash` on its PATH: there run `python <the same scripts dir>\tail.py --dir <project>\.superlite` (absolute paths).
Where a terminal-tab tool exists (Claude Desktop's `run_in_terminal`), open it there titled "<project folder name> live", then bring the terminal panel into view on that tab (Claude Desktop's `show_pane` with `pane: "terminal"` and the tab id: a new tab does not show itself) and link the tab. Otherwise print the command for the user to run in their own terminal. Never run it in the foreground of your own shell: it does not exit. Change nothing else.
