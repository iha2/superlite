# Feature: Optional pre-implementation specs (feature flow first)

## Goal

Give superlite users an **opt-in switch** so that, when on, agents write a short **implementation spec before coding**. Default is **off**. On first install / first session after the preference is introduced, the model asks once whether to turn it on and persists the answer globally. Users (or the LLM, following the user) can still decide if, how, and when specs are written for a given run; the switch only establishes the default expectation.

Who it is for: people running larger feature builds where agents go off for a while and the human wants a visible design artifact before code lands—starting in **feature mode** (prototype + integration), not every mode yet.

## Spec vs plan

A **spec** defines what “done” looks like and **why**. A **plan** chooses **how** to get there. One spec can outlive many plans: the plan may be rewritten, split, or replaced without rewriting the success criteria. Skills and agents treat the spec as the durable authority for acceptance; the plan (and task graph) are disposable tactics under it. If a plan or a task-local note drifts from the spec, fix the plan (or report `DONE_WITH_CONCERNS` with the delta)—do not silently override the spec.

## What changes

**Option (user-facing)**

- New toggle, modeled on autocommit:
  - Project: `.superlite/agent-specs.json` e.g. `{"agent_specs": true|false, "open_in_harness": true|false}`
  - Global default: `$SUPERLITE_HOME/preferences.json` (asked once at install/first session)
  - Env override: `SUPERLITE_AGENT_SPECS=1|0` (wins over files, like autocommit)
  - CLI: `sl agent_specs on|off|status` and `sl agent_specs open on|off` (open-in-harness sub-flag; default **on** when agent_specs is on)
- First-session ask (session-start, same marker pattern as intro): if preferences never recorded, instruct the model to ask once: “Turn on pre-impl specs by default?” → `sl agent_specs prefer on|off` (writes `~/.superlite/preferences.json`). Project file / env still override for a given repo.

**Default behavior when the switch is on (feature flow)**

- **Granularity (flexible):** the default sense of “a spec” is **feature-sized** — one durable design artifact for the feature (or for a clear sub-feature the coordinator splits), not a rigid one-file-per-commit rule. When agents fan out on discrete tasks, they may also write a **task-local** note under `.superlite/specs/<plan-topic>/…` when that helps (long-running implementer, ambiguous contract); those notes refine *how* for that task and must stay consistent with the feature spec. The LLM and user may redefine when/how for the run; document the default in the skill, not as a hard gate on every micro-task.
- **When:** before the implementer starts writing product code (after prime/READY, before TDD). No human approval gate on the task-local note. Spec is durable authority over the plan; on conflict → revise the plan or `DONE_WITH_CONCERNS` with the delta.
- **Open in harness:** when `open_in_harness` is on, after writing the feature (or task) spec, open it for the user (Cursor: `open_resource`; other harnesses: path in chat + harness-native open if documented). Can be turned off independently.
- **Style:** lean on existing flows design §9 (`superlite:writing-specs` shape: Mermaid first, tables, ≤150 prose words outside fences/tables) and Superpowers’ written-design sections (intent/why, goals/non-goals, constraints, decided vs left-to-builder) where they fit a feature-level doc. The feature **spec** is what done looks like; the `.feature.md` / machine plan are how—and may be replaced while the spec stays.

**Feature-flow wiring (this slice)**

- `commands/feature.md`, `docs/feature-flow.md`, `skills/feature-prototype/SKILL.md`, implementer agents, and SDD skill: when `sl agent_specs status` is on, expect the feature-level (and optional task-level) spec step; when off, today’s behavior unchanged.
- Prototype and integration both honor the switch.
- Out of scope for this slice: MVP/regular/debug modes, full `spec_check.py` scorer, bench evals (can follow later).

## Acceptance checks

1. Fresh preferences (no `preferences.json` key): session-start additional context includes a one-time instruction to ask about pre-impl specs; after `sl agent_specs prefer off`, the ask does not appear again; default effective status is **off**.
2. `sl agent_specs on` in a project → status shows on; `sl agent_specs off` → off; `SUPERLITE_AGENT_SPECS=0` overrides a project on to off.
3. `sl agent_specs open off` while agent_specs is on → skills say write the spec but do not require opening it in the harness.
4. With agent_specs on, a feature-flow implementer brief / agent instructions require writing a feature (or task) spec **before** product code, at the path convention, and report the path in the DONE message.
5. With agent_specs off, feature-flow and implementer instructions match today’s “no pre-impl spec” behavior (no new required files).
6. Unit tests cover: prefer once, project on/off, env override, open flag, session-start marker/instruction presence.
7. `docs/tools.md` / help mention `agent_specs`; `using-superlite` stays under its character cap (pointer only if needed).

## Code map

**Entry / stand-up**

- Session start: `hooks/session-start` (intro marker in `$SUPERLITE_HOME`); Cursor: `hooks/hooks-cursor.json`.
- Feature flow: `commands/feature.md`, `docs/feature-flow.md`, `scripts/proto.py`, `.superlite/feature.json`.
- Option pattern to copy: `skills/subagent-driven-development/scripts/autocommit.py` (`is_on` / `set_on` / CLI).
- Global home: `history.py` / session-start `SUPERLITE_HOME` (~/.superlite).

**Dispatch / agents (read status; soft then harden later)**

- `skills/subagent-driven-development/SKILL.md`, `agents/implementer.md`, `agents/implementer-haiku.md`.
- `scripts/dispatch.py`, `build_prime.py`, `task.py` — optional status line or brief hint when on.
- Prior design (not shipped): `docs/specs/2026-10-17-superlite-flows-design.md` §9; `docs/plans/2026-10-17-superlite-flows-implementation.md` T15 (`skills/writing-specs/`).

**Open in harness**

- No in-repo open API today except `sl mvp_check open` (browser). Cursor: document `open_resource` in `skills/using-superlite/references/cursor-tools.md`.

**Interfaces that must not change (FROZEN)**

- Autocommit / orchestrator JSON shapes and CLI verbs.
- Existing feature-plan shape and `sl proto *` / tryout approval gates.
- Plan lint `--spec` (product spec coverage) meaning unchanged; agent pre-impl specs are a separate artifact.
- `using-superlite` ≤ character budget.

**Conventions / toolchain**

- LF, `printf` in session-start (no heredoc).
- Tests under `tests/scripts/`; `uv run` / pytest as existing script tests.
- Version bumps only via `scripts/bump-version.sh` if a release is cut (not required for the feature itself).

## Prototype scope

**Keep:** `autocommit.py` as the template; `session-start`; feature command/docs stubs; a minimal `agent_specs.py` + `sl` wiring; session-start prefer ask; a short `writing-specs` skill stub or template; cursor-tools open note; unit tests.

**Stub:** full implementer hook enforcement / checkpoint gate; bench evals; MVP/regular mode wiring; Graphify; real harness open beyond documenting Cursor `open_resource`.

**Level:** L1 (scripts + markdown + pytest in a proto worktree).

**Run:** `uv run --with pytest python -m pytest tests/scripts/test_agent_specs.py tests/scripts/test_session_start.py -q` (adjust to files created).

## Integration outline

1. Port `agent_specs.py`, `sl` registration, preferences + session-start ask, tests.
2. Add `skills/writing-specs/SKILL.md` (+ thin template) aligned with flows §9 + feature-level default.
3. Wire feature.md / feature-flow.md / feature-prototype / SDD + implementer agents to consult `sl agent_specs status`.
4. Document open-in-harness in cursor-tools.md (and a one-liner in other harness maps if trivial).
5. Update `docs/tools.md` / help; keep using-superlite lean.
6. Seam: existing autocommit/orchestrator/session-start tests still green.

## Rulings (AI orchestrator)

- Ruling: orchestrator is AI for this feature (user asked).
- Ruling: default **off**; install/first-session asks once and stores global prefer; project/env override (because user chose off-by-default + install question).
- Ruling: default artifact is **feature-sized** spec; task-local notes optional when agents fan out — not a hard one-spec-per-commit rule (because user said flexible / “a spec ≈ a feature”).
- Ruling: **spec = done + why; plan = how; one spec can outlive many plans** (user clarification). Spec is durable authority; plans are replaceable tactics under it.
- Ruling: open-in-harness defaults **on** when agent_specs is on, separately toggleable (user’s stated default).
- Ruling: first slice is **feature flow only**; reuse flows §9 + Superpowers design sections for style, do not ship full scorer/evals yet (YAGNI for this slice).
- Ruling: plan detail `tests`.
- Ruling: Graphify not required for this plugin change (code map from Explore agents is enough; `graphify_gate` allowed=false for install).
