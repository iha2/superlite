# Superlite for Kimi Code

Complete guide for using Superlite with [Kimi Code](https://github.com/MoonshotAI/kimi-code).

## Installation

Open the plugin manager:

```text
/plugins
```

Install directly from this repository:

```text
/plugins install https://github.com/iha2/superlite
```

To validate unreleased changes, pin the branch explicitly:

```text
/plugins install https://github.com/iha2/superlite/tree/dev
```

Kimi Code applies plugin changes to new sessions. After installing, updating, enabling, disabling, or reloading a plugin, start a fresh session with `/new`.

## How It Works

The Kimi plugin manifest lives at `.kimi-plugin/plugin.json`. It does three things:

1. Points Kimi Code at the existing `skills/` directory.
2. Loads `using-superlite` at session start through `sessionStart.skill`.
3. Provides Kimi-specific tool mapping through `skillInstructions`.

There are no copied skills, symlinks, hooks, or extra runtime dependencies.

## Tool Mapping

Skills use Claude Code's tool names. On Kimi Code these resolve to:

- `AskUserQuestion` -> Kimi Code's `AskUserQuestion`
- plan or todo list -> `TodoList`
- `Agent` dispatch -> Kimi's `Agent` with `subagent_type: "coder"`, pasting the text of `agents/<name>.md` followed by the brief into `prompt` (Kimi has no plugin agents and no per-call `model` or `effort`)
- `Agent` with `Explore` -> `Agent` with `subagent_type: "explore"`
- `SendMessage` -> resume the same agent if supported, otherwise dispatch a new `coder` with the earlier report
- `Skill` -> Kimi Code's native `Skill` tool
- read, write, edit a file -> `Read`, `Write`, `Edit`
- run a shell command -> `Bash`
- search contents / find files -> `Grep` / `Glob`
- fetch a URL / search the web -> `FetchURL` / `WebSearch`

## Updating

Use Kimi Code's plugin manager (`/plugins`), select Superlite and update it. Start a fresh session with `/new` afterwards.

## Troubleshooting

- Plugin not loading: run `/plugins info superlite`, check diagnostics, make sure it is enabled, and start a fresh session with `/new`.
- Old release installed: Kimi Code installs the latest GitHub release for a bare repository URL when one exists; install the branch explicitly to test unreleased changes.
- Skills not triggering: confirm the plugin is enabled, start a fresh session, and try `Let's make a react todo list`. A working install loads `inventing` before writing code.
