# Feature: Compact agents at a token count (main session, subagents, every harness)

Revised 2026-11-02 after web research (sources at the end). Where research contradicted the first draft, the research wins.

## Goal

Long contexts are re-read on every turn, so they are the biggest avoidable cost. Today superlite only nudges the **main session** to compact on a **30-minute timer** (`compact_due.py`); it never looks at tokens. For anyone running a long build (human or AI orchestrator), this feature compacts by **token count**, uses each host's own compact, and checks that the compact really happened.

## What research changed

| First draft | Research found | Plan now |
|---|---|---|
| Subagents cannot compact; restart them with a handoff note | Claude Code subagents **auto-compact** themselves, and a per-agent `autoCompactWindow` (100k-1M, v2.1.296+) lowers the trigger | On Claude Code set `autoCompactWindow` in `agents/*.md`; handoff note is only the fallback for hosts without agent compaction |
| PostToolUse hook reads an agent's tokens and injects "write handoff" mid-task | `PostToolUse` has **no token usage**; no documented way for a hook to trigger `/compact` | Mid-task agent hook dropped. Mid-task compaction comes from the host (`autoCompactWindow`) |
| Thresholds as % of window (60% / 80%) | Cost follows **absolute tokens**, and many models now have 1M windows, so 60% = 600k would defeat the goal. Vendors use absolute counts (Anthropic compaction default 150k, min 50k; context editing 100k; Aider/OpenAI absolute); only Gemini uses a fraction (0.5 default) | Absolute tokens: `compact_tokens` default **150k** main, **100k** agents; optional percent override |
| Detect compact by guessing from transcript or context drop | Claude Code has `PreCompact`/`PostCompact` hooks, `SessionStart` matcher `compact`, and `compact_boundary` / `isCompactSummary` transcript lines with `preTokens` | Use those as the primary confirmation |
| Cursor `/summarize`, Codex `/compact`, ... from memory | Many rows are unconfirmed (see table) | Each row carries a confidence flag; unconfirmed hosts get nudge + fallback |

## What the user will see or do differently

- **Main session (Claude Code, Cursor first):** when the live context (last main-transcript message: input + cache read + cache write) reaches `compact_tokens`, the next prompt carries the nudge naming the count and **the host's compact command**: `Context is 162k (limit 150k): run /compact now, then re-read .superlite progress`. The 30-minute timer stays as a fallback.
- **Human mode:** task updates reach the main chat without a user prompt, so the same check also runs on `SubagentStop` (payload has `transcript_path`) and the nudge is queued for the next turn. Compact points are **between waves**, after the checkpoint is on disk; plan approval lives in `superlite-cli orchestrator`, not in the chat, so a compact cannot lose it. Per-task updates stay one line.
- **Subagents on Claude Code:** `agents/*.md` frontmatter gets `autoCompactWindow: 100000`, so each agent compacts itself at 100k. (`CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` also applies to subagents but can only lower the trigger.) After DONE, an agent over its limit is not reused; the existing tier reuse limits (`max_context_k`) are compared with **measured** tokens from `agent_records`, not the estimate.
- **Other hosts:** per the table: native auto-compact settings are checked and (with the user's yes) set; commands are named in the nudge; hosts without a documented compact use the fallback (handoff note, fresh session re-primed from it).
- **If no compact follows, the trigger fires again** on every prompt, firmer each time (`Compact still pending (2nd request, 170k)`), until one is confirmed. After 3 unanswered requests: `compact_missed` is logged and shown once in the HUD; re-triggering continues. The nudge is skipped when the plan has one task left or the build is at its final review (a compact near the end only costs a cache rewrite), and capped at 2 compacts per task to prevent loops (my own safeguard; no vendor standard exists).
- **Host auto-compact as safety net (verify on, lower the trigger):** most hosts already ship with auto-compact on; the gap is that the default trigger is too late to save cost (Claude Code: the model limit). `superlite-cli compact status` lists each harness with its auto-compact state and trigger; `superlite-cli compact enable [--host H]` lowers the trigger to the superlite threshold and turns auto-compact on where it is off (Codex unset, anything the user disabled), only after the user's explicit yes, one host at a time (it edits the user's own settings; never from a hook or at install). `/superlite:setup` runs the status check. Superlite's thresholds sit **below** the host's own trigger (Claude Code default is the model limit, about 967k on 1M models), so the planned compact comes first. For Claude Code the enable sets `autoCompactWindow` (or `CLAUDE_CODE_AUTO_COMPACT_WINDOW`, which wins over settings).
- **Honest limits:** a hook cannot run `/compact` (not documented on any host). It can only nudge, block (`PreCompact` exit 2) or react. The model or user runs the command.
- **Handoff note content** (fallback and summary hint): current task, decisions, open bugs, files touched. Keep verbatim: project instructions (re-injected, not summarized), current task, last turns. Old tool output is dropped first (Anthropic: lightest, safest form).

## Host table (`skills/using-superlite/config/compact.json`; each row has `confidence`)

| Host | Manual | Auto-compact setting | Agents compact? | Hook can trigger? | Confidence |
|---|---|---|---|---|---|
| Claude Code | `/compact` | `autoCompactEnabled`, `autoCompactWindow`, env `CLAUDE_CODE_AUTO_COMPACT_WINDOW`, `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`; default = model limit | **Yes**, `autoCompactWindow` per agent | No (`PreCompact`/`PostCompact` can block/observe) | docs |
| Cursor | `/summarize` | unknown | unknown | unknown | forum only |
| Codex | `/compact` | `model_auto_compact_token_limit`, `model_post_turn_compact_threshold_percent` | unknown | `PreCompact`/`PostCompact` events exist, details unknown | config docs; default unknown |
| Gemini CLI | `/compress` | `model.compressionThreshold` (0.5; some versions 0.7) | unknown | no | docs, version-dependent |
| OpenCode | `/compact` | `compaction.auto` (true) | unknown | `experimental.session.compacting` customizes only | docs |
| Pi | `/compact` | `compaction.enabled`, `reserveTokens`, `keepRecentTokens` | unknown | `session_before_compact` cancel/supply summary; trigger unknown | docs |
| Kimi | `/compact` | `loop_control.compaction_trigger_ratio` (0.85) | unknown | unknown | docs |
| Hermes | `/compress` | `compression.threshold` (0.50) | unknown | context-engine plugin `should_compress()` | docs |
| Devin, Antigravity, Muse | unknown | unknown | unknown | unknown | none: fallback only |

The prototype re-checks every non-"docs" row against the host's current docs; none ships on memory. Cursor `/summarize` stays "forum only" until confirmed.

## Acceptance checks

1. Main transcript last message 160k, `compact_tokens` 150k: `compact_due.py <.superlite>` exits 0 and prints a line with "compact", the count and the host command, even when `last-compact` is fresh.
2. Same at 90k with a fresh `last-compact`: exits 1.
3. After a compact (transcript has a `compact_boundary` / small last message) the nudge stops without touching `last-compact`.
4. `hooks/prompt` emits the nudge in all three output shapes; `SUPERLITE_NO_AUTOCOMPACT=1` silences it; a missing or unreadable transcript falls back to the timer and never errors.
5. `superlite.json` `compact_tokens` is accepted by `project_config`; an invalid value is rejected clearly; env `SUPERLITE_COMPACT_TOKENS` overrides.
6. Nudge then compact: run the hook over 160k (nudge + `compact_nudge` logged); append a `compact_boundary` line with `preTokens` and a small message; run again: `compact_done` logged (before/after) and no nudge. `PostCompact`/`SessionStart compact` hooks log it directly where the host provides them.
7. Ignored nudge: each over-threshold prompt re-triggers with an incremented count; after 3, `compact_missed` once and one HUD line; confirmed compact resets the count.
8. Human mode: 10 DONE notifications push the transcript over the threshold with no user prompt; the `SubagentStop` check logs a nudge; after the compact `superlite-cli orchestrator status` still shows approved and the next ready task is unchanged.
9. Skip rules: no nudge at the final review or with one task left; no more than 2 compacts per task.
10. Agents: every `agents/*.md` that is an implementer, reviewer or fixer carries `autoCompactWindow` (validated by a lint test); dispatch refuses reuse of an agent whose measured tokens exceed its tier limit and says why.
11. `compact status` lists each harness (on / off / unknown); `compact enable` changes nothing without an explicit yes, then flips exactly the named host's setting; a second run is a no-op.
12. The nudge text uses the detected host's command; an unknown host gets the fallback wording.
13. The report lists nudges, confirmed compacts, missed, tokens saved (sum of `preTokens` minus post size).
14. All existing tests pass (`tests/scripts/test_compact_due.py` unchanged in behavior).

## Code map

- Entry: `hooks/hooks.json` UserPromptSubmit -> `hooks/prompt` -> `hooks/_nudge` `compact_nudge()` -> `skills/swarm/scripts/compact_due.py` (CLI: arg = `.superlite` dir, exit 0 + text if due, else 1). Add `SubagentStop` check (existing `hooks/capture-agent-stop` already runs there) and `PostCompact` / `SessionStart compact` logging.
- Payload: `hooks/_preamble` regex `jstr`; `transcript_path` not read today (add; fail open).
- Token math: `metrics.py` `_count_usage` (116), `coordinator_record` (258), `session_transcript` (241). New `metrics.current_context(transcript)` = last non-sidechain assistant message's input + cache_read + cache_write; `metrics.last_compact_boundary(transcript)` finds `compact_boundary` / `isCompactSummary`.
- Config: `project_config.py` `KEYS` (13; unknown keys raise): add `compact_tokens`, `compact_hard_tokens` (optional ceiling), keep tier limits in `routing.json`. New `compact.json` (host table).
- Agents: `agents/*.md` frontmatter; `dispatch.py` `eligibility` (221), `reuse_limits` (211-230); `agent["context_k"]` setter not yet located (first prototype task).
- Tests: `tests/scripts/test_compact_due.py`, `test_hooks_cwd_first.py`, `test_human_gate.py`; fixtures `tests/scripts/fixtures/capture_real`; add a hook-emits-nudge test (none exists).
- Docs: `docs/hooks.md:12` (says ~35-40 min, stale vs 30), `skills/swarm/SKILL.md:48`, each `references/<harness>-tools.md` gets a "Compact" line.
- **Frozen:** `compact_due.is_due(sl_dir, now, window_s)` (extend with optional args), `nudge_line()` keeps "compact", CLI exit contract, `compact_nudge()` / `NUDGE`, `SUPERLITE_NO_AUTOCOMPACT`, hook fail-open and three output shapes, pool.json format, existing dispatch output lines.
- No UI. Toolchain: stdlib Python, pytest under `tests/scripts`, bash hooks (LF, `printf`).

## Open choices (my defaults)

- Thresholds: 150k main, 100k agents (Anthropic's compaction default and the `autoCompactWindow` minimum).
- Cursor first alongside Claude Code; other hosts get the table row, nudge text and docs line, with real triggers only where the research confirmed one.
- Compact enabling is always opt-in per host.

## Prototype scope (L1, about 5 tasks)

`metrics.current_context` + `last_compact_boundary`, token-aware `compact_due`, `transcript_path` in the hook, `compact_nudge`/`compact_done`/`compact_missed` logging, host table with the Claude Code and Cursor rows, fixture transcripts; checks 1-7, 12. Agent frontmatter, dispatch reuse, `compact status/enable` and the other hosts are ported at integration.

## Integration outline

1. Metrics helpers. 2. `compact_due` + config keys. 3. Hook wiring (prompt, SubagentStop, PostCompact). 4. Host table + `compact status/enable` + setup. 5. Agent frontmatter + dispatch measured reuse. 6. Docs and per-harness reference lines. Seam checks: hook over a real fixture transcript, dispatch with a real pool, status/enable on a temp settings file.

## Sources (fetched 2026-11-02)

code.claude.com/docs (model-config, env-vars, settings-reference, sub-agents, hooks); platform.claude.com/docs (compaction, context-editing, context-windows, memory-tool); anthropic.com/engineering/effective-context-engineering-for-ai-agents; geminicli.com/docs/cli/settings; opencode.ai/docs/config and /plugins; Pi compaction doc (badlogic/pi-mono); Kimi CLI config docs; Hermes context-compression docs; Cursor forum thread on `/summarize`; Codex config reference; Chroma "Context Rot" (research.trychroma.com/context-rot).
