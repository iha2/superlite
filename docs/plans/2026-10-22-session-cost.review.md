# Superlite session cost plan: compact, graph, scoped tests, agent run cost

**Goal:** cut session cost and make spend visible in every superlite mode and language by (1) compacting the coordinator about every 35-40 minutes, (2) showing the task graph as an ASCII diagram, (3) having agents run only the tests for the code they changed, and (4) tracking per-agent / run totals (USD, tokens, wall clock) live and at the end — preferring each IDE’s analytics API for the final total, with `models.json` rate refresh when possible.

**Status:** draft for review, 2026-10-22. Nothing built yet. This hub is the single plan doc (no separate feature.md).

## Context

Four changes for every superlite mode (regular, MVP, feature, debug, small) and every language:

1. **Compact** — coordinator context is re-sent every turn at Opus rates (median 70-141k tokens). Compact about every 35-40 minutes.
2. **Graph** — show the plan’s task graph as a stored-style ASCII diagram (parallel / wait / shared agent).
3. **Scoped tests** — agents test only what they changed; one full suite run in the background at the end.
4. **Agent / run cost** — live + end table for USD, tokens, wall clock; host analytics for the final total when available; else estimate from transcripts × `models.json`.

Findings that shape the plan:

- A plugin hook can only add text; it cannot compact. A **mod** can (`$.session.compact`). Nudge is the backup when no mod exists or the mod did not fire.
- After compaction, `session-start` re-injects the skill intro but not run-state pointers; disk state survives (`.superlite/progress.md`, `pool.json`, …).
- Graph inputs already exist (`parse_taskgraph`, `schedule_report`, `dispatch.make_batches`, …); no ASCII renderer yet.
- Agents say “never full suite”, but `--fast` needs test paths, `rewrite-test-runs` forces `--full`, and finish/verify text still send agents to the full suite.
- Cost today: hooks + transcripts × hand-edited `models.json`; `metrics.reconcile` vs Claude stream-json only; agent-map shows tokens/time but not USD; no `models` refresh; no Cursor/other host adapters.

---

## Part 1 - compact (~35-40 min)

**Decision:** shared policy on disk; auto-compact **mod where the host can**; **nudge always on** if no mod or mod did not fire.

- **Policy:** due from `.superlite/metrics.jsonl` + `.superlite/last-compact` (~38 min default); active run; no mid-flight subagent; flush `Ruling:` to `progress.md` first. Opt out: `SUPERLITE_NO_AUTOCOMPACT=1`. Shared `sl compact due`.
- **Mod (Claude Code):** `mods/superlite-compact/` → `$.session.compact({instructions})`; stamp `last-compact`.
- **Nudge:** `hooks/compact-nudge`; silent if `last-compact` fresh; host-neutral wording.
- **Recovery:** `session-start` on compact reloads skill + progress/pool/dispatch. Pi already re-bootstraps on `session_compact`. Hermes: no post-compact hook.

## Part 2 - ASCII task graph

- `sl graph` → `.superlite/graph.txt` (cached by plan/`stable.json` hash); lanes, critical path `*`, batches, legend; ≤79 wide.
- Show after `lint --stable`; on demand via status/approve. Skip debug/small. Not in human review mermaid plans.

## Part 3 - scoped tests (any language)

- `testcmd` `related` + Owns + name convention; `--fast` accepts source or test paths; fail loud if none.
- `rewrite-test-runs`: scoped raw → `--fast`; deny bare full suite for subagents during a run.
- Reword finish/verify/debug/small/SKILL to focused tests; one background `--full` at the end.

## Part 4 - agent / run cost (USD, tokens, wall clock)

- **Live:** per-agent wall, tokens, est USD + run totals (`sl status`; agent-map USD on Claude Code). Label `est` until host reconcile.
- **End table:** agent, model, wall-min, tokens, USD; totals row in `sl report` / finish → `.superlite/cost-summary.json` with `source: host|est`.
- **Host truth:** Claude → stream-json / session usage / Usage-Cost Admin API when keyed; Cursor/others → their analytics when available; else estimate. Missing keys never fail the build. Claude Admin APIs need org keys and can lag — individuals use stream-json / session usage first. Host adapters only where a real API exists; otherwise permanent `est` for that host.
- **Rates:** `sl models refresh` when a price source is reachable; manual edit remains.

---

## Order and constraints

1. Part 3 → 2. Part 2 → 3. Part 1 → 4. Part 4.  
Version tags per part (e.g. 1.7.0 …); push only when you say so. Focused tests only; no co-author trailer; `coord-split` stays out.

## Verification

- **P3:** scratch repos (Py/TS/Go); one file → only its tests; raw scoped runner → `--fast`.
- **P2:** `sl graph` on fixture/bench plans; `test_graph.py`.
- **P1:** due-check unit tests; nudge vs fresh stamp; Claude mod 1-min window; nudge if mod disabled.
- **P4:** fixture live + end table; host-labelled total vs est; `models refresh` fixture.

## Related

- Spec: [[2026-10-19-superlite-cost-baseline]], [[2026-10-19-coordinator-model-decision]]
- Plan: [[2026-10-19-superlite-cost-parity-plan]], [[2026-10-19-coordinator-health-plan]]
- Project: [[Superlite]]

Hub: [[Plans index]]
