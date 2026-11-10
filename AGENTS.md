# Superlite - Contributor Guidelines

Instructions for any agent (or human) changing this repository.

## What this repo is

A lean build-lifecycle plugin: 10 skills in `skills/`, 7 agents in `agents/`, and one manifest or extension per supported harness. Keep it lean; process is the cost superlite exists to remove.

## Rules

- While a superlite run is in progress, superpowers (and anything with the same lifecycle skills) is suspended. `superlite-cli finish` releases it. See the README note.
- `skills/using-superlite/SKILL.md` is injected into every session. Keep it at most 2,000 characters.
- Every harness integration must load `using-superlite` at session start. Acceptance test: open a clean session and send `Let's make a react todo list`; `inventing` must trigger before any code is written. Manually copied skills or per-session opt-in do not count.
- Per-harness tool mappings live in `skills/using-superlite/references/<harness>-tools.md`. Each says how to run `agents/*.md` where plugin agents do not exist.
- Versions are declared in `.version-bump.json`. Change them only with `scripts/bump-version.sh <version>`; run `scripts/bump-version.sh --audit` to catch missed files.
- Keep LF line endings. `hooks/session-start` uses `printf`, never a heredoc.
- Do not touch `bench/` results or `docs/plans/` when working on a harness integration.
- **One feature, one worktree.** Never develop two features in the same checkout. Before writing code for a feature (including feature-mode integration), cut a dedicated branch and git worktree with superlite (`superlite-cli session create --slug <topic> --worktree`; it lands in `.claude/worktrees/`), enter it (under Claude Code see "Worktrees under Claude Code"), and keep other features' trees untouched. Prototype worktrees (`.claude/worktrees/<topic>-proto`) stay separate from the integration worktree. The flow that cut a worktree removes it when the flow finishes; a keep with uncommitted work stays. Trivial one-line fixes on the current branch are exempt.
- **No Cursor agent notes on commits.** Do not add `Co-authored-by: Cursor …` (or similar agent trailers). After clone, run `bash scripts/install-git-hooks.sh` once so `core.hooksPath=.githooks` and the `commit-msg` hook strips them automatically.

## Worktrees under Claude Code

Project instructions authorise `EnterWorktree` (`name=` and `path=`) and `ExitWorktree` here. Follow the line superlite prints:

- `session: create EnterWorktree name=<n> base=<ref>` (session ensure / create --worktree) or `proto: create EnterWorktree name=<topic>-proto base=<ref>` (proto start): call `EnterWorktree name=<n>`, then rerun the same command inside the new worktree (`proto start --topic <T> --adopt` for the prototype). Superlite adopts it.
- `ASK branch-name slug=<s>: ...`: ask the user once: keep Claude's `worktree-<slug>`, use `superlite/sessions/<slug>`, or their own pattern with `{slug}`. Rerun with `--branch-name worktree|superlite|PATTERN`; it is saved and changed later with `superlite-cli session branch-name`.
- `session: enter EnterWorktree path=<abs>`: superlite cut the worktree (hub not on the needed base, merge checkout, or another harness's fallback); call `EnterWorktree path=<abs>`. `session: note hub on <cur>, not <base>; superlite cut the worktree` explains why.

Worktrees live under `<hub>/.claude/worktrees/`. The repo's `.claude/settings.json` sets `"worktree": {"baseRef": "head"}` so `EnterWorktree name=` cuts from the hub's HEAD; it applies from the next Claude Code session start.

Cleanup: a Claude-created worktree ends with `ExitWorktree` `remove` (or `keep`) or the desktop app; `session close` and `proto discard` only archive and unregister it. A worktree superlite cut is removed by `session close` / `proto discard`; `ExitWorktree` only leaves it.

### Other harnesses (fallback)

No `EnterWorktree`: `git worktree add -b superlite/<topic> <path> <base>`, `cd <path>`, and at the end `cd` back and `git worktree remove <path>`.

## Harness files

| Harness | Files |
|---|---|
| Claude Code, Factory Droid, Copilot CLI, Qwen Code | `.claude-plugin/`, `hooks/hooks.json` |
| Codex | `.codex-plugin/plugin.json`, `.agents/plugins/marketplace.json` |
| Cursor | `.cursor-plugin/plugin.json`, `hooks/hooks-cursor.json` |
| Devin | `.devin-plugin/plugin.json` |
| Gemini CLI | `gemini-extension.json`, `GEMINI.md` |
| Kimi Code | `.kimi-plugin/plugin.json`, `docs/README.kimi.md` |
| Muse | `.muse-plugin/` |
| Hermes Agent | `.hermes-plugin/` |
| OpenCode | `.opencode/`, `index.js`, `package.json` |
| Pi | `.pi/extensions/superlite.ts`, `package.json` |
