# Muse Tool Mapping

Skills speak in Claude Code's tool names. On Muse they resolve as follows.

| Skill says | Muse equivalent |
|---|---|
| Read a file | `read_file` |
| Read multiple files | `read_file` (call multiple times) or `search` |
| Create a new file | `write_file` |
| Edit a file | `edit_file` |
| Run a shell command | `bash` |
| Search file contents | `search` |
| Find files by name | `search` with `glob` |
| Fetch a URL | `web_fetch` |
| Search the web | `web_search` |
| `Skill` (load a skill) | `read_file` on `skills/<name>/SKILL.md` or the native skill tool |
| `Agent` (dispatch an agent) | `subagent_spawn` (see below) |
| `model`, `effort` on `Agent` | not settable per call; keep the tier in mind |
| `SendMessage` to an agent | no resume tool: `subagent_spawn` again with the earlier report and the new findings in the prompt |
| `AskUserQuestion` | `request_user_input` |
| Plan or todo list | `write_todos`, or a markdown task file via `write_file`/`edit_file` |
| Open a plan or spec for the user (`open_in_harness`) | no open tool: path + five-line summary in chat |

## Instructions file

When a skill mentions "your instructions file", on Muse this is **`CLAUDE.md`** or **`AGENTS.md`** in the project root, loaded hierarchically where configured.

## Skill invocation

Muse has native skill support via `muse skills`. To invoke a superlite skill, read its `SKILL.md` and follow it. The bootstrap (`using-superlite`) is injected automatically at `SessionStart` via the plugin hook; you are already following it, so do not re-load it.

## Running superlite's agents

Muse has no plugin agents, so the `superlite:*` agents (`implementer`, `implementer-haiku`, `package-reviewer`, `final-reviewer`, `fixer-sonnet`, `fixer-sonnet-high`, `fixer-opus`) do not exist. Use `subagent_spawn` with the full text of `agents/<name>.md` (from this plugin's root, minus the frontmatter) followed by the brief as the prompt. Tier from the `model` and `effort` in that file (implementer, package-reviewer, fixer-sonnet: sonnet-class, medium; implementer-haiku: haiku-class, high; fixer-sonnet-high: sonnet-class, high; final-reviewer and fixer-opus: opus-class, high). Say in the prompt that the final-reviewer uses no tools, and run tests with `bash <skill-dir>/scripts/sl run_tests`. Spawn all independent agents of a wave in one response. If no subagent tool is available, do the work inline rather than inventing tool calls.

## Task tracking

Use `write_todos` for checklist tracking, one todo per wave, marking in_progress/completed as you go. If `write_todos` is unavailable, maintain a markdown task file via `write_file`/`edit_file`.
