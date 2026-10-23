# Feature: Cursor host profile + Multitask swarms by tier

## Goal

Coordinator authors and Cursor users of superlite. When a run is on Cursor, the task graph and `dispatch` know that parallel work is **Multitask** (background `Task`s), and each fan-out is a **swarm of one tier** (one job, one resolved model slug for the run). Cache and idle reuse stay intact: no mid-run model shopping, no mixed-tier swarms.

Who it is for: people running superlite inside Cursor (Multitask / Agents Window), without changing Claude Code’s existing Agent path.

## What changes

**Commands / outputs (coordinator-facing)**

- Optional plan header: `**Execution:** host=cursor, parallel=multitask, swarm_by=tier` (absent → today’s behavior on every harness).
- At first lint / start of a Cursor run: write `.superlite/host-models.json` — resolved once per run: each job (`mechanical`, `build`, `build-hard`, `judge`) → one Cursor model slug from `config/hosts/cursor.json` candidates.
- `sl dispatch` (when execution host is cursor): after the existing per-batch lines, print a `SWARM` block the coordinator can follow — one swarm per tier that has ready work, listing task ids, agent type, resolved slug, and `mode=multitask` (background `Task`).
- New harness map: `skills/using-superlite/references/cursor-tools.md` — Skill/Agent → Cursor `Task`, Multitask `run_in_background`, resume, never invent tools; leaf agents do not spawn children.
- `using-superlite` platform list names `cursor`.

**What the user sees differently**

- On Cursor: one coordinator turn can start several background agents at once, grouped by tier (e.g. all ready mechanical tasks as one swarm, all ready build tasks as another).
- Session model stays user-chosen; the map only binds **subagent** slugs per job for the run (or inherits parent when the profile says omit).
- Claude Code and other harnesses unchanged when the Execution header is absent.

**Pool vs Multitask (must be in `cursor-tools.md`)**

Multitask is only the parallel **transport** when several agents must run at once. Batching and the pool stay as today (`dispatch` / `assign` / `.superlite/pool.json`).

- **Batching unchanged:** one dispatch line can still give one agent several tasks (`T1,T2,T3` … up to `batch` limits). That is one agent, one brief/resume — not one Multitask slot per task.
- **Multitask when spawning multiple agents:** if this turn has several lines that need a new or resumed agent (e.g. three `fresh` / `agent=` lines across a wave), fire them as parallel background `Task`s in one coordinator turn. That is what Multitask is for.
- `agent=<id>` → **`resume` that idle agent** with the batch brief (not a new spawn).
- `fresh` → one new background `Task` (`run_in_background: true`); that agent joins the pool.
- Extra idle agents of the same tier stay idle until `assign` picks them for a later line; do not wake every idle agent for one batch.
- Never invent agents beyond what `dispatch` printed. Swarm grouping by tier is a view over those lines, not a second scheduler.
- Same rules as Claude Code’s background `Agent` + `SendMessage`; Cursor names are `Task` + `resume`.

**Not in this feature**

- Cursor session Auto picking models per task.
- Cursor Plan UI “Build in Parallel” driving the build (bypasses lint/gates).
- Live host USD analytics (session-cost plan); estimates stay on `models.json` / `est`.
- Changing the SDD rule that the coordinator never overrides agent `model` on Claude Code plugin agents.

## Acceptance checks

A human can try these after the prototype (and again after integration):

1. With a tiny fixture plan that has `**Execution:** host=cursor, parallel=multitask, swarm_by=tier` and two ready sonnet tasks + one ready haiku (off critical path), `sl dispatch` prints existing lines **and** a `SWARM` section grouping by `tier=haiku` and `tier=sonnet`, each with `mode=multitask` and a resolved slug (or `inherit`).
2. Without the Execution header, `sl dispatch` output matches today’s shape (no `SWARM` block); Claude-oriented tests still pass.
3. `sl host resolve` (or equivalent invoked at lint) writes `.superlite/host-models.json` once; a second call does not change slugs mid-run unless `--force`.
4. Opening `skills/using-superlite/references/cursor-tools.md` shows: map Skill → how to load; Agent → `Task` + `subagent_type`; Multitask → `run_in_background: true` when this turn needs **multiple agents**; batching still puts several tasks on one agent; `agent=` resumes one idle with that batch; do not pass call-site `model` unless the host profile resolved a slug and the Cursor tools map allows it from that file.
5. `using-superlite` SKILL.md platform line lists `cursor` and stays under the character cap.
6. Unit tests cover: header parse default/absent; swarm grouping by tier; host resolve picks first candidate; dispatch without header unchanged.

## Code map

**How a run is stood up today**

- `lint --stable` → `.superlite/stable.json` (`tiers`, `critical_path`, …).
- `sl dispatch` = `next_tasks` + same-tier `make_batches` + `assign` → lines like `Tn[,Tm] fresh tier=sonnet agent=superlite:implementer`.
- Coordinator follows SDD + harness `*-tools.md` to spawn agents; pool in `.superlite/pool.json`.

**Entry points**

| Area | Path |
|---|---|
| Dispatch / batch / assign | `skills/subagent-driven-development/scripts/dispatch.py`, `assign.py`, `next_tasks.py`, `routing.py` |
| Plan header parse | `skills/subagent-driven-development/scripts/plan_tools.py` (`Plan.header_target`, `header_ignore`) |
| Config | `skills/subagent-driven-development/config/routing.json`, `models.json` |
| Harness maps | `skills/using-superlite/references/*.md` (**no** `cursor-tools.md` yet) |
| Cursor plugin | `.cursor-plugin/plugin.json`, `hooks/hooks-cursor.json` (session-start only) |
| Agents | `agents/*.md` (`model` / `effort` frontmatter) |
| SDD contract | `skills/subagent-driven-development/SKILL.md` — never pass `model` on Claude plugin path |
| Tests | `tests/scripts/test_routing.py`, `test_batching.py`, `test_spawn_free.py`, `test_stable_split.py` |

**Touched files (expected)**

- New: `config/hosts/cursor.json`, `scripts/host_models.py` (or similar), `references/cursor-tools.md`, tests for header + swarm + resolve.
- Edit: `dispatch.py` (optional SWARM block), `plan_tools.py` (`header_execution`), `using-superlite/SKILL.md` (platform list), docs for writing-plans Execution header, maybe `routing` docs in `docs/config.md` / `docs/tools.md`.
- Prototype may stub Cursor Task UI and prove CLI + config + map text only.

**Interfaces that must not change**

- Existing `sl dispatch` line grammar (`agent=` / `poll` / `fresh` / `wave0` + `tier=`).
- `stable.json` keys; pool agent fields; same-tier assign; critical path never haiku.
- SDD “never pass `model`” on Claude Code plugin agents.
- Other harness `*-tools.md` behavior when Execution header absent.
- Do not touch `bench/` results for this harness feature.

**Conventions / toolchain**

- Python tools via `bash …/scripts/sl <tool>`; pytest under `tests/scripts`.
- LF line endings; keep `using-superlite` under its character cap.
- New config under `skills/.../config/` does not require `.version-bump.json` until a release bump.

## Prototype scope

**Keep**

- Plan header parse for `**Execution:**`.
- `hosts/cursor.json` job → candidate slugs.
- Resolve → `.superlite/host-models.json`.
- `dispatch` SWARM block for cursor execution.
- Draft `cursor-tools.md` and a one-page “how the coordinator fans out” section.
- Unit tests on fixtures (no live Cursor Multitask required).

**Stub**

- Real Cursor `Task` launches (document the calls; do not require Multitask Mode in CI).
- Live model allowlist probing (candidates are static in `cursor.json`; resolve = first listed).
- Cost/analytics host adapter.

**Level:** L1 — prove the schedule and map; acceptance is CLI + docs + tests.

**Prototype plan size:** ≤ 8 tasks in the sandbox worktree.

## Integration outline

1. Land header parse + host config + resolve + dispatch SWARM on the real branch.
2. Add `cursor-tools.md` and register `cursor` in `using-superlite`.
3. Document Execution header in writing-plans / config docs.
4. Keep Claude Code path byte-stable when header absent; extend dispatch tests.
5. Seam check: dispatch lines still parseable by existing coordinator instructions; SWARM is additive.
6. Port prototype acceptance tests; full `tests/scripts` green; final review on the packet.
