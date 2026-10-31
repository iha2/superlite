# Hermes Agent Tool Mapping

Skills speak in Claude Code's tool names. On Hermes Agent they resolve as follows.

## Tools

| Skill says | Hermes tool |
|---|---|
| Read a file | `read_file` |
| Create a new file | `write_file` |
| Edit a file (targeted patch) | `patch` |
| Run a shell command | `terminal` |
| Search file contents | `search_files` |
| Find files by name | `terminal` with `find` |
| Fetch a URL / read a webpage | `web_extract(urls=[...])` |
| Search the web | `web_search(query=...)` |
| `Skill` (load a skill) | `skill_view("skill-name")` |
| `Agent` (dispatch an agent) | `delegate_task(goal=..., context=..., toolsets=[...], role="leaf")` |
| `model`, `effort` on `Agent` | not settable per call; the delegate uses the configured delegation model |
| `SendMessage` to an agent | no resume tool: call `delegate_task` again with the earlier report and the new findings in `context` |
| `AskUserQuestion` | ask in plain text, batching up to 4 questions in one message |
| Plan or todo list | `todo` tool (one item per wave) |
| Open a plan or spec for the user (`open_in_harness`) | no open tool: path + five-line summary in chat |

## Instructions file

When a skill mentions "your instructions file," on Hermes Agent this is **`AGENTS.md`** in the project directory, or **`SOUL.md`** globally at `~/.hermes/SOUL.md`.

## Invoking a skill

Hermes Agent has a `skills` toolset with `skill_view` and `skills_list` tools. To invoke a superlite skill, use:

```
skill_view("inventing")
skill_view("testing")
```

If `skill_view` cannot find a superlite skill (it may not appear in the catalog until the plugin fully registers it), fall back to reading the SKILL.md directly:

```
read_file(path="~/.hermes/plugins/superlite/skills/<skill-name>/SKILL.md")
```

## Running superlite's agents

Hermes has no plugin agents, so the `superlite:*` agents (`implementer`, `implementer-haiku`, `package-reviewer`, `final-reviewer`, `fixer-sonnet`, `fixer-sonnet-high`, `fixer-opus`) do not exist. Use `delegate_task` and put the full text of `agents/<name>.md` (from this plugin's root, minus the frontmatter) followed by the brief into `goal`/`context`:

```
delegate_task(goal="<agents/implementer.md text>\n\n<brief>", context="...", toolsets=["terminal", "file"], role="leaf")
```

Pick the tier from the `model` and `effort` named in the agent file (implementer, package-reviewer, fixer-sonnet: sonnet-class, medium; implementer-haiku: haiku-class, high; fixer-sonnet-high: sonnet-class, high; final-reviewer and fixer-opus: opus-class, high) if your delegation config lets you choose. Give the final-reviewer no tools. Run tests with `bash <skill-dir>/scripts/sl run_tests`. Send all independent delegations of a wave in one response. If `delegate_task` is unavailable, do the work inline (the skills' inline mode) rather than inventing tool calls.

## Task tracking

Use the `todo` tool for task tracking within a session. For multi-agent task boards, use the `hermes kanban` CLI if available.
