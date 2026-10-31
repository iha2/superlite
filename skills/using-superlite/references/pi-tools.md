# Pi Tool Mapping

Skills speak in Claude Code's tool names. On Pi they resolve as follows.

| Skill says | Pi equivalent |
|---|---|
| `Skill` (load a skill) | Pi's native skills: `read` the `SKILL.md`, or a human runs `/skill:name` |
| `Agent` (dispatch an agent) | an installed subagent tool such as `subagent` from `pi-subagents`, if available |
| `model`, `effort` on `Agent` | pass them if the subagent tool accepts them; otherwise keep the tier in mind |
| `SendMessage` to an agent | the subagent tool's resume workflow; otherwise dispatch again with the earlier report in the prompt |
| `AskUserQuestion` | ask in plain text, batching up to 4 questions in one message |
| Plan or todo list | an installed todo/task tool if available, otherwise the plan file or a repo-local `TODO.md` |
| Open a plan or spec for the user (`open_in_harness`) | no open tool: path + five-line summary in chat |

Pi's built-in coding tools are lowercase: `read`, `write`, `edit`, `bash`, plus optional `grep`, `find` and `ls`.

## Running superlite's agents

Pi core does not ship a standard subagent tool and has no plugin agents, so the `superlite:*` agents (`implementer`, `implementer-haiku`, `package-reviewer`, `final-reviewer`, `fixer-sonnet`, `fixer-sonnet-high`, `fixer-opus`) do not exist. The `pi-subagents` package is a strong optional companion: it provides a `subagent` tool with single-agent, chain, parallel, async, forked-context and resume/status workflows. With it, pass the full text of `agents/<name>.md` (from this plugin's root, minus the frontmatter) followed by the brief as the prompt, on the tier named in that file (implementer, package-reviewer, fixer-sonnet: sonnet-class, medium; implementer-haiku: haiku-class, high; fixer-sonnet-high: sonnet-class, high; final-reviewer and fixer-opus: opus-class, high). Run a wave's independent agents in parallel.

If no subagent tool is installed, do not fabricate `Agent` calls: use the skills' inline mode (implement directly, run the suite yourself) or explain that the optional subagent capability is missing.

## Task lists

Pi core does not ship a standard task-list tool. If a todo/task extension is installed, use its documented tool. Otherwise use plan-file checklists in Markdown or a repo-local `TODO.md`.
