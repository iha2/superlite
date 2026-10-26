# Claude Code Tool Notes

Claude Code is the reference harness: superlite's skills speak its vocabulary, so no mapping is needed.

| Skill says | Claude Code |
|---|---|
| Load a skill | `Skill` tool (`superlite:<name>`) |
| Dispatch an agent | `Agent` with `subagent_type` (`superlite:implementer`, `superlite:implementer-haiku`, `superlite:package-reviewer`, `superlite:final-reviewer`, `superlite:fixer-sonnet`, `superlite:fixer-sonnet-high`, `superlite:fixer-opus`, `Explore`, `general-purpose`), `model` and `effort` |
| Continue an agent | `SendMessage` to that agent |
| Ask the user | `AskUserQuestion` (up to 4 questions per call) |
| Plan or todo list | the harness's plan/todo tools (one item per wave) |

Plugin agents exist here, so use `superlite:*` subagent types directly. `model` and `effort` on the call override the agent's frontmatter.

## Cheaper orchestration for subagent-driven development

The coordinator reads every dispatch result and report, and usually runs on the session's most capable model. Claude Code supports nested subagents (`CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH` adjusts the depth), so the whole loop can run one layer down.

When the user asks for it, or says the session model is too expensive for coordination, dispatch ONE orchestrator subagent on a mid-tier model with the plan path and the instruction to use `superlite:swarm` end to end. It dispatches its own implementers, reviewers and fixers; the progress file lives on disk, so nothing is lost. Its final message must carry the `Ruling:` lines verbatim, and you relay them, not summarize them.

Do this only for a whole plan. Nesting a single task's dispatch buys nothing and adds a seat.
