# Gemini CLI Tool Mapping

Skills speak in Claude Code's tool names. On Gemini CLI they resolve as follows.

| Skill says | Gemini CLI equivalent |
|---|---|
| Read a file | `read_file` (`read_many_files` for several) |
| Create a new file | `write_file` |
| Edit a file | `replace` |
| Run a shell command | `run_shell_command` |
| Search file contents | `grep_search` |
| Find files by name | `glob` |
| List files and subdirectories | `list_directory` |
| Fetch a URL | `web_fetch` |
| Search the web | `google_web_search` |
| `Skill` (load a skill) | `activate_skill` |
| `Agent` (dispatch an agent) | `invoke_agent` with `agent_name` and `prompt` (see below) |
| `model`, `effort` on `Agent` | not settable per call; the subagent inherits the session model unless its definition sets one |
| `SendMessage` to an agent | no resume tool: `invoke_agent` again with the earlier report and the new findings in the prompt |
| `AskUserQuestion` | `ask_user` (text, single-select or multi-select; up to 4 questions) |
| Plan or todo list | `write_todos` (statuses: pending, in_progress, completed, cancelled, blocked); one item per wave |
| Multiple parallel dispatches | multiple `invoke_agent` calls in the same response |
| Open a plan or spec for the user (`open_in_harness`) | no open tool: path + five-line summary in chat |

## Instructions file

When a skill mentions "your instructions file", on Gemini CLI this is **`GEMINI.md`**, loaded hierarchically: global at `~/.gemini/GEMINI.md`, then project-level files in workspace directories and their ancestors.

## Personal skills directory

User-level skills live at **`~/.gemini/skills/`**, with **`~/.agents/skills/`** as a cross-runtime alias. Each skill is a subdirectory containing a `SKILL.md` with `name` and `description` frontmatter.

## Running superlite's agents

Gemini CLI has no plugin agents, so the `superlite:*` agents (`implementer`, `implementer-haiku`, `package-reviewer`, `final-reviewer`, `fixer-sonnet`, `fixer-sonnet-high`, `fixer-opus`) do not exist. Dispatch Gemini's built-in `generalist` (also reachable as `@generalist <prompt>`):

| Skill dispatch form | Gemini CLI equivalent |
|---|---|
| Any `superlite:*` agent | `invoke_agent` with `agent_name: "generalist"`; the prompt is the full text of `agents/<name>.md` (from this plugin's root, minus the frontmatter) followed by the brief |
| `Explore` | `invoke_agent` with `agent_name: "codebase_investigator"` |
| `general-purpose` | `invoke_agent` with `agent_name: "generalist"` |

Tier from the `model` and `effort` named in `agents/<name>.md` (implementer, package-reviewer, fixer-sonnet: sonnet-class, medium; implementer-haiku: haiku-class, high; fixer-sonnet-high: sonnet-class, high; final-reviewer and fixer-opus: opus-class, high). If Gemini lets you pick a model for the subagent, use the matching tier; otherwise it inherits the session model. The final-reviewer reads only its review packet and uses no tools: say so in the prompt. Run tests with `bash <skill-dir>/scripts/sl run_tests`.

### Parallel dispatch

Issue all independent `invoke_agent` calls of a wave in one response (or several `@generalist` invocations in one prompt). Keep dependent tasks sequential.

## Additional Gemini CLI tools

| Tool | Purpose |
|---|---|
| `save_memory` (legacy) | Persist facts across sessions when `experimental.memoryV2 = false` |
| `get_internal_docs` | Look up Gemini CLI's bundled documentation |
| `enter_plan_mode` / `exit_plan_mode` | Switch into and out of read-only plan mode |
| `update_topic` | Update the conversation's topic metadata |
| `complete_task` | Signal that a Gemini subagent has completed and return its result to the parent |
| `tracker_create_task`, `tracker_update_task`, `tracker_get_task`, `tracker_list_tasks`, `tracker_add_dependency`, `tracker_visualize` | Rich task tracker with dependencies |
| `read_mcp_resource`, `list_mcp_resources` | MCP resource access |
