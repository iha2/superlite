# Cursor Tool Mapping

Skills speak in Claude Code's tool names. On Cursor they resolve as follows. Trust your live tool list over this table when they disagree.

| Skill says | Cursor |
|---|---|
| Load a skill | Read `skills/<name>/SKILL.md` (or the plugin skill UI) |
| `Agent` (dispatch an agent) | `Task` with `subagent_type` (see below) |
| `model`, `effort` on `Agent` | Optional `model` on `Task` only from `.superlite/host-models.json` for that job; otherwise omit (child inherits the session). Do not invent slugs. |
| `SendMessage` to an agent | `Task` with `resume` set to that agent id |
| `AskUserQuestion` | Plain text in one message (up to 4 questions); Cursor has no AskUserQuestion tool |
| Plan or todo list | Cursor todos / plan UI if present; else a markdown checklist |
| Open a plan or spec for the user (`open_in_harness`) | `open_resource` on the workspace path; else path + five-line summary in chat |

## When the plan is on Cursor

If the plan header has `**Execution:** host=cursor, parallel=multitask, swarm_by=tier` (or `SUPERLITE_HOST=cursor`), run `bash "$SL" host_models resolve --plan $PLAN` once at lint/start. `sl dispatch` then prints the usual lines **plus** a `SWARM` block grouped by tier.

### Pool vs Multitask

Multitask is only the parallel **transport** when several agents must run at once. Batching and the pool stay as today.

- **Batching unchanged:** one dispatch line can still give one agent several tasks (`T1,T2,T3`). That is one `Task` / one `resume`, not one Multitask slot per task.
- **Multitask when spawning multiple agents:** if this turn has several dispatch lines (several `fresh` / `agent=` / `poll` winners), fire them as parallel background `Task`s (`run_in_background: true`) in one coordinator turn.
- `agent=<id>` → **`resume` that idle agent** with the batch brief (not a new spawn).
- `fresh` → one new background `Task`; that agent joins `.superlite/pool.json`.
- Extra idle agents of the same tier stay idle until `assign` picks them; do not wake every idle agent for one batch.
- Never invent agents beyond what `dispatch` printed. The `SWARM` block is a view over those lines, not a second scheduler.

## Running superlite's agents

Cursor exposes plugin-style types when the superlite plugin is installed (`implementer`, `implementer` lane, `package-reviewer`, `final-reviewer`, `fixer-sonnet`, `fixer-sonnet-high`, `fixer-opus`). Prefer those `subagent_type` values. If a type is missing, paste the body of `agents/<name>.md` (minus frontmatter) into a `generalPurpose` `Task` prompt and keep the tier in mind.

| Agent | Job (host profile) | Typical Cursor slug (from host-models) |
|---|---|---|
| implementer-haiku | mechanical | first mechanical candidate |
| implementer, package-reviewer, fixer-sonnet | build | first build candidate |
| fixer-sonnet-high | build-hard | first build-hard candidate |
| final-reviewer, fixer-opus | judge | first judge candidate |

Leaf agents do not spawn children. Only the coordinator fans out.

## Parallel and resume

- One message: all `Task` calls for the wave (background when Multitask / multiple agents).
- After DONE, keep the agent idle in the pool; next matching line uses `resume`.
- If `Task` / Multitask is unavailable, use the skills' inline mode — do not invent tool calls.
