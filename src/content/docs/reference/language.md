---
title: Language support
description: Which parts of superlite depend on your project's language.
sidebar:
  order: 7
---

The tools are Python, but the project you build can be in any language.

| Part | Python project | Other language |
|---|---|---|
| Test command | `.superlite/test-cmd.json`, else detection | Same; detection covers npm, go, cargo, dotnet |
| `probe_run` | Runs | Python only |
| `plan_tools stubs` | Writes Python stubs | One `stub:` line per Produces signature and a `contract:` line, no `.py` file |
| `plan_tools adapter` | Writes `.superlite/probes/adapter.py` | Writes `seam.ts`, `seam.js`, `seam.go`, `seam.rs`, `seam.cs`, `seam.java`, `seam.rb` or `seam.txt` |

superlite needs Python 3.12 or newer, git and bash (Git Bash on Windows). `uv` is optional.
