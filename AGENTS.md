# Superlite - Contributor Guidelines

Instructions for any agent (or human) changing this repository.

## What this repo is

A lean build-lifecycle plugin: 10 skills in `skills/`, 7 agents in `agents/`, and one manifest or extension per supported harness. Keep it lean; process is the cost superlite exists to remove.

## Rules

- Do not enable superlite alongside the plugin it was derived from (see the README note); they share skill names.
- `skills/using-superlite/SKILL.md` is injected into every session. Keep it under 1,500 characters.
- Every harness integration must load `using-superlite` at session start. Acceptance test: open a clean session and send `Let's make a react todo list`; `brainstorming` must trigger before any code is written. Manually copied skills or per-session opt-in do not count.
- Per-harness tool mappings live in `skills/using-superlite/references/<harness>-tools.md`. Each says how to run `agents/*.md` where plugin agents do not exist.
- Versions are declared in `.version-bump.json`. Change them only with `scripts/bump-version.sh <version>`; run `scripts/bump-version.sh --audit` to catch missed files.
- Keep LF line endings. `hooks/session-start` uses `printf`, never a heredoc.
- Do not touch `bench/` results or `docs/plans/` when working on a harness integration.
- **One feature, one worktree.** Never develop two features in the same checkout. Before writing code for a feature (including feature-mode integration), cut a dedicated branch and git worktree — e.g. `git worktree add -b superlite/<topic> .superlite/worktrees/<topic> <base>` — move the agent root there, and keep other features' trees untouched. Prototype sandboxes (`.superlite/sandboxes/<topic>-proto`) stay separate from the integration worktree. The flow that cut a worktree removes it when the flow finishes; a keep with uncommitted work stays. Trivial one-line fixes on the current branch are exempt.
- **No Cursor agent notes on commits.** Do not add `Co-authored-by: Cursor …` (or similar agent trailers). After clone, run `bash scripts/install-git-hooks.sh` once so `core.hooksPath=.githooks` and the `commit-msg` hook strips them automatically.

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
