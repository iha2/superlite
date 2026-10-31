# Codex Tool Mapping

Skills speak in Claude Code's tool names. On Codex (App and CLI) they resolve as follows. Trust your actual tool list over any table, including this one, when they disagree.

| Skill says | Codex |
|---|---|
| `Skill` (load a skill) | Codex's native skill loading, or read `skills/<name>/SKILL.md` |
| `Agent` (dispatch an agent) | `spawn_agent` (see below) |
| `model`, `effort` on `Agent` | `model` and `reasoning_effort` on `spawn_agent` |
| `SendMessage` to an agent | `followup_task` |
| Waiting for agents | `wait_agent` |
| `AskUserQuestion` | `request_user_input` when available, otherwise plain text with up to 4 questions in one message |
| Plan or todo list | `update_plan` (one step per wave) |
| Open a plan or spec for the user (`open_in_harness`) | no open tool: path + five-line summary in chat |

## Subagent dispatch requires multi-agent support

Add to `~/.codex/config.toml`:

```toml
[features]
multi_agent = true
```

Which tools you get depends on the multi-agent version your model preset selects (current presets run V2; older ones run V1).

## Running superlite's agents

Codex has no plugin agents, so the `superlite:*` agents (`implementer`, `implementer-haiku`, `package-reviewer`, `final-reviewer`, `fixer-sonnet`, `fixer-sonnet-high`, `fixer-opus`) do not exist. Call `spawn_agent` and put the full text of `agents/<name>.md` (from this plugin's root, minus the frontmatter) followed by the brief into its message. Set the tier from that file's frontmatter, mapped to models on your spawn allowlist:

| Agent | Tier named in agents/<name>.md | `reasoning_effort` |
|---|---|---|
| implementer | sonnet-class (mid-tier) | medium |
| package-reviewer | sonnet-class (mid-tier) | medium |
| implementer-haiku | haiku-class (cheapest) | high |
| fixer-sonnet | sonnet-class (mid-tier) | medium |
| fixer-sonnet-high | sonnet-class (mid-tier) | high |
| fixer-opus | opus-class (top tier) | high |
| final-reviewer | opus-class (top tier) | high |

The final-reviewer reads only its review packet and uses no tools; say so in the prompt. Run tests with `bash <skill-dir>/scripts/sl run_tests`. Role files under `~/.codex/agents/` (Codex 0.145+) attach via `agent_type`; use that only if you have set up roles from these files.

- **Spawning:** give children a clean context with `spawn_agent {fork_turns: "none"}`; the default `"all"` copies your whole transcript into the child.
- **Model and effort:** set `model` AND `reasoning_effort` on every `spawn_agent`. Setting `model` alone leaves the child's effort at that model's default. Never copy a model name from a skill or old session without checking it against your current spawn allowlist; V2 hard-errors on unsupported presets.
- **Fix rounds:** resume the implementer with `followup_task` instead of dispatching a fresh one.
- **Lifecycle:** V2 has no `close_agent`; finished children are evicted when slots are needed. On V1, close reviewers when their review returns and implementers when their package's review passes.

## Waiting on children

`wait_agent` is an event subscription, not a poll. While you have local work, do not wait. When genuinely idle, wait in bounded stretches (`timeout_ms` 300000-600000), then post one status line, run `list_agents`, and chase any child that finished without reporting. Never stack polls shorter than five minutes.

## Environment detection

Before finishing a branch, detect the environment with read-only git commands:

```bash
GIT_DIR=$(cd "$(git rev-parse --git-dir)" 2>/dev/null && pwd -P)
GIT_COMMON=$(cd "$(git rev-parse --git-common-dir)" 2>/dev/null && pwd -P)
BRANCH=$(git branch --show-current)
```

- `GIT_DIR != GIT_COMMON` means you are in a linked worktree.
- Empty `BRANCH` means detached HEAD (cannot branch, push or open a PR from the sandbox).

## Codex App finishing

When the sandbox blocks branch or push operations (detached HEAD in an externally managed worktree), commit all work and tell the user to use the App's native controls: **Create branch** (names the branch, then commit/push/PR via the App UI) or **Hand off to local** (moves the work to the local checkout). You can still run tests and output suggested branch names, commit messages and PR descriptions.
