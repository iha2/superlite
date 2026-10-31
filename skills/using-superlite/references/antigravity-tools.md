# Antigravity CLI (`agy`) Tool Mapping

Skills speak in Claude Code's tool names. On the Antigravity CLI (`agy`) they resolve as follows.

| Skill says | Antigravity CLI equivalent |
|---|---|
| `Skill` (load a skill) | the native skill mechanism, or `view_file` on `skills/<name>/SKILL.md` |
| `Agent` (dispatch an agent) | `invoke_subagent` with a built-in `TypeName`: `self` for full-capability work, `research` for read-only (see below) |
| `model`, `effort` on `Agent` | not settable per call; the subagent inherits the session model |
| `SendMessage` to an agent | no resume tool: dispatch a new subagent with the earlier report and the new findings in its prompt |
| `AskUserQuestion` | ask in plain text, batching up to 4 questions in one message |
| Plan or todo list | a **task artifact** (see [Task tracking](#task-tracking)). **Not** `manage_task`, which manages background processes. |
| Open a plan or spec for the user (`open_in_harness`) | no open tool: path + five-line summary in chat |

## Running superlite's agents

Antigravity has no plugin agents, so the `superlite:*` agents (`implementer`, `implementer-haiku`, `package-reviewer`, `final-reviewer`, `fixer-sonnet`, `fixer-sonnet-high`, `fixer-opus`) do not exist. Call `invoke_subagent` and put the full text of `agents/<name>.md` (from this plugin's root, minus the frontmatter) followed by the brief into the prompt. Tier from the `model` and `effort` in that file's frontmatter:

| Agent | TypeName | Tier named in agents/<name>.md |
|---|---|---|
| implementer | `self` | sonnet, medium |
| package-reviewer | `self` (it fixes its own findings) | sonnet, medium |
| implementer-haiku | `self` | haiku, high |
| fixer-sonnet | `self` | sonnet, medium |
| fixer-sonnet-high | `self` | sonnet, high |
| fixer-opus | `self` | opus, high |
| final-reviewer | `research` (read-only, no tools needed) | opus, high |
| Explore | `research` | haiku or sonnet |

If you cannot choose a model per call, keep the tier in mind and do not let the final-reviewer write files. Run tests with `bash <skill-dir>/scripts/sl run_tests`, not an agent. For several independent agents in a wave, issue the `invoke_subagent` calls in one response.

## Task tracking

Antigravity has **no todo tool** (`manage_task` manages background processes: `list`/`kill`/`status`/`send_input`). When a skill says to keep a plan or todo list, maintain a **task artifact**: a markdown checklist saved with `write_to_file` (`IsArtifact: true`, `ArtifactMetadata.ArtifactType: "task"`), edited with `replace_file_content` / `multi_replace_file_content` as you go.

At the start of a multi-step task, create the artifact listing every step (one per wave). Mark each done (`- [x]`) as you finish it and update it when the plan changes. Re-read it before starting each step once the conversation gets long.
