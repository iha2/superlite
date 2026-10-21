# Superlite 0.2 + 0.3: implementation plan

> **Erratum (2026-10-17, from the 0.2 session).** Smoke tests (0.2 design §9) invalidate parts of this document:
> - **A "shared wave preamble" with the task tail in the same brief does not share cache.** Siblings hit only the ~4.7k system-and-tools prefix. Use **prime-then-assign** instead: an identical first message (preamble + plan + spec sections + sandbox manifest path) answered with READY, then each task sent by SendMessage. Wherever this doc says the task tail follows the preamble in the brief, read it as the next message.
> - **A sibling hit-rate target of 80% is reachable only with prime-then-assign.** Measured: siblings 2..n read 100% of the prime.
> - **Agent files written mid-session are not loaded.** Agent definitions load at session start, so per-plan generated agents can't carry shared context.
> - **Forks run on the session model and in `-p` need `CLAUDE_CODE_FORK_SUBAGENT=1`.** No per-fork model or effort is documented. A Fable session therefore means Fable-priced forks.
> - **The implementer's `experimental.cacheTtl: 1h` is verified (E6):** a resume after about 7 min read 26.9k cached tokens.
>
> **Update (2026-10-17, main supalite session), after the minisheet run and an independent Opus review (architecture design §2.3c, §2.5, D9, E10):**
> - **Orchestration lean (D9, the user's decision).** Every flow leans toward an orchestrator dispatching parallel subagents. The workflow decides by its own complexity judgment, and builds inline only when it judges the task small and says so.
> - **A separate reviewer always runs**, inline or orchestrated.
> - **The 15 s Opus fork final review is too shallow.** It missed a recursion crash on deep formulas, an `inf` leak and HTTP 501s, which superpowers' review caught and fixed. Plan the final review as a fresh Opus reviewer at high effort with a robustness checklist (deep or long input, non-finite numbers, invalid encodings, unsupported methods, oversize input). That is E10, pending the user.
> - **The Orchestrator tier** is D1: the second most powerful model at max effort (Opus 5.5), Fable opt-in.


**Goal:** ship two things.
- **0.2 engine upgrades:** pipelined review, a top-tier fork for the final review, cache sharing, the `detail` plan switch, and a leaner coordinator.
- **0.3 flows:** MVP, Feature, Bugfix and Refactor, with sandboxes and the `.superlite/` folder.

Each is measured by benchmark before it becomes the default.

**Specs:**
- `C:\Work\Planning\Specs\2026-10-17-superlite-architecture-design.md` (0.2)
- `C:\Work\Planning\Specs\2026-10-17-superlite-flows-design.md` (0.3; its model-tier section supersedes the 0.2 spec's "Opus")

**Repo:** `C:\Users\CWogu\source\repos\superlite`, branch `superlite/0.2-0.3`. Never push without the user.
**Execution:** superlite's own SDD, with the orchestrator on the top-tier model, Sonnet workers, and Haiku runners.
**Plan detail:** `contracts`. The deliverables are mostly skill prose, and their tests are scenario evals, not unit tests.

## Global constraints

- **Skill files:** frontmatter has `name` and a `description` that starts "Use when…" (no workflow summary in it). Cross-references use `superlite:<name>`. One concept per skill.
- **Size caps:**
  - `using-superlite` ≤ 1,800 chars;
  - flow skills ≤ 6,000 chars each;
  - `sandboxing` ≤ 6,000;
  - `html-prototype` ≤ 4,000;
  - `subagent-driven-development` ≤ 11,000;
  - `writing-plans` ≤ 6,500;
  - agents ≤ 2,200 each.
- **Harness neutrality:** skills name tiers (orchestrator / worker / runner), never vendor model ids. Model ids live only in `skills/using-superlite/references/*-tools.md`.
- **Never in skills:** remote push, PR or publish without the human; real secrets in sandboxes; force-merge.
- **Artifacts folder:** `.superlite/` (committed), with `.superlite/sandboxes/` git-ignored. Gate-pending artifacts sit at the repo root, then `git mv` into `.superlite/` once approved.
- **Python** via `uv run --no-project python` (there's no system python). The shell is Git Bash.
- **Every task's evals** run through `bench/evals.py` (T1). A scenario is pass/fail on the stream log plus the files it produces.
- **Spec style** (flows spec §9.2) binds every spec-like artifact that superlite produces or contains: `ARCHITECTURE.md`, delta docs, refactor targets, task specs, and the skill bodies' own templates. The rules: a diagram first, tables over prose, a precise technical register, no filler, and code only where a diagram plus a contract is ambiguous.

## Waves

| Wave | Tasks (parallel) | Notes |
|---|---|---|
| 0 | T0 skeleton (coordinator, inline) | Branch, `.gitignore` entries, empty `bench/evals/scenarios/`, version 0.2.0-dev |
| 1 | T1 harness + evals · T2 router + tiers · T3 sandboxing · T4 html-prototype + prototyper agent · T15 writing-specs | No shared files |
| 2 | T6 mvp-flow · T7 feature-flow + bugfix-flow · T8 refactor-flow · T9 SDD 0.2 engine · T10 writing-plans 0.2 | Flows and SDD consume T3/T4/T15 by name only |
| 3 | T5 artifact + spec checkers · T11 brainstorming + finishing · T12 benchmark tasks | Evals that need T5 run in wave 4 |
| 4 | T13 README, docs, version · then T14 evals + experiment matrix + report (coordinator) | T13 documents T1–T12 and T15 |

---

### T0: Skeleton (coordinator, inline)

**Files:** `.gitignore` (add `.superlite/sandboxes/`, `bench/results/*.jsonl`); `bench/evals/scenarios/.gitkeep`; `.version-bump.json` (version 0.2.0-dev).
**Wave:** 0

### T1: Harness v2 and the scenario eval runner

**Files:** modify `bench/harness.py`; create `bench/logscan.py` and `bench/evals.py`; tests in `bench/tests/test_logscan.py` and `bench/tests/test_evals.py`.

**Interfaces (produces):**
- `logscan.timeline(events) -> list[Phase]`, with `Phase(name, start_s, end_s)` and phases detected from Skill calls, plan or spec Writes, Agent dispatches and commits.
- `logscan.dispatches(events) -> list[Dispatch]`, with `Dispatch(id, model, subagent_type, start_s, end_s, first_cache_read, first_cache_write, overlapped: bool)`.
- `harness.py run <arm> <rep> [--task <name>] [--variant <name>]`. `--variant` maps to `--plugin-dir bench/variants/<name>`, a copy of the plugin with one skill swapped in.
- `harness.py score` adds `timeline`, `dispatches`, `sibling_cache_hit_rate`, `orchestrator_cost_share` and `gates_hit`.
- Gate handling: when a result lacks the marker and the last assistant text asks for approval, resume with "Approved." and count it in `gates_hit`, not `resumes`.
- `evals.py run <scenario.json>` loads a scenario `{name, prompt, fixture_dir?, max_turns, budget_usd, assert: [...]}`. Assertion kinds:
  - `skill_invoked`
  - `skill_not_invoked`
  - `file_exists`
  - `file_absent`
  - `file_contains`
  - `stopped_for_gate`
  - `agent_dispatched{subagent_type, model}`
  - `max_dispatch_chain`
  - `mermaid_valid{path}` (uses T5)
  - `prototype_ok{path}` (uses T5)

  It prints `PASS`/`FAIL` per assertion and exits 1 on any FAIL. It reuses `clean_env()` and the run-dir-outside-repo rule.

**Tests:**
- `test_timeline_superlite_run1`: on a trimmed fixture copied from `superlite-1.jsonl`, the phases are `plan`, `wave-1`, `wave-2`, `final-review` and `finish`, in order.
- `test_dispatch_overlap`: the 5 wave-1 dispatches are `overlapped=True`, and `max_dispatch_chain == 3`.
- `test_cache_first_request`: wave-1 siblings have `first_cache_read == 0`, matching the measured 0.1 result.
- `test_score_last_result_per_session`: a log with 3 results sharing one session id has cost equal to the last result's (regression for the 2026-10-17 bug).
- `test_evals_assertions`: each assertion kind passes and fails on a synthetic log and a temp dir.

**Wave:** 1

### T2: Router and model tiers

**Files:** modify `skills/using-superlite/SKILL.md` and every `skills/using-superlite/references/*-tools.md` (add a "Model tiers" table); scenarios `bench/evals/scenarios/router-*.json`.

**Interfaces:**
- Consumes the skill names `superlite:mvp-flow`, `superlite:feature-flow`, `superlite:bugfix-flow`, `superlite:refactor-flow` and `superlite:sandboxing` (names only).
- Produces the router rules:
  - trivial → no skill;
  - no codebase → mvp;
  - existing code + new behaviour → feature;
  - wrong behaviour → bugfix;
  - structure only → refactor.
- Also: a one-line announcement, upgrade-only re-routing, the unattended rule, and the orchestrator-tier check ("if the session model isn't the top tier, say once how to switch, then continue").

**Tests (scenario evals, `max_turns` 3, budget $0.50):**
- `router-mvp`: "Build me a habit tracker web app" in an empty dir invokes `superlite:mvp-flow`.
- `router-feature`: "Add CSV export to the reports page" in a fixture repo invokes `superlite:feature-flow`.
- `router-bugfix`: "Totals are off by one on the invoices page" invokes `superlite:bugfix-flow`.
- `router-refactor`: "Split site.py into smaller modules, no behaviour change" invokes `superlite:refactor-flow`.
- `router-trivial`: "Rename the variable x to count in util.py" invokes no flow skill.
- A size check: `wc -c` is ≤ 1,800.

**Wave:** 1

### T3: `superlite:sandboxing`

**Files:** create `skills/sandboxing/SKILL.md` and `skills/sandboxing/templates/SANDBOX.md`; scenario `bench/evals/scenarios/sandbox-l1.json`.

**Interfaces:**
- Produces the levels L0–L3, with the decision table from flows spec §8.
- Produces the manifest at `.superlite/sandboxes/<topic>/SANDBOX.md`, with fields `level`, `branch`, `setup`, `focused_test`, `full_test`, `seed`, `ports`.
- Produces the merge-back procedure (green → merge into the main checkout's branch → full suite in main → teardown → progress line), and the teardown commands per level.
- Ecosystem recipes: uv/pytest, npm/vitest and dotnet.
- Consumes `superlite:test-runner`.

**Tests:**
- `sandbox-l1` (fixture: the sitegen reference repo): the prompt "Create a sandbox for adding pagination" produces a worktree under `.superlite/sandboxes/`, a `SANDBOX.md` whose `focused_test` runs green, `.superlite/sandboxes/` in `.gitignore`, and no changes in the main checkout.
- The template has every field listed above.
- A size check.

**Wave:** 1

### T4: `superlite:html-prototype` and `agents/prototyper.md`

**Files:** create `skills/html-prototype/SKILL.md` and `agents/prototyper.md` (worker tier, effort medium); scenario `bench/evals/scenarios/prototype-basic.json`.

**Interfaces:**
- Produces the prototype rules from flows spec §4.4:
  - a single file;
  - inline CSS and JS;
  - no network;
  - a `const MOCK` object with ≥ 5 records per list, plus one empty and one error state;
  - all sketched screens reachable;
  - create/edit/delete/filter/navigate in memory;
  - the banner text exactly "Prototype: mock data, changes are not saved.".
- Produces the review checklist the orchestrator runs against the ASCII sketch.
- Produces the archive step (`git mv` into `.superlite/` after the gate).
- Consumes the `ARCHITECTURE.md` "UI sketch" section format (from T6, names only).

**Tests:**
- `prototype-basic` (fixture: an `ARCHITECTURE.md` with 3 ASCII screens): `prototype.html` exists at the root, passes `prototype_ok` (T5), and contains `const MOCK` and the banner text.
- The agent frontmatter is valid.
- A size check.

**Wave:** 1

### T5: Artifact checkers

**Files:** create `bench/checks/mermaid_check.py`, `bench/checks/prototype_check.py`, `bench/checks/spec_check.py` and `bench/checks/package.json` (pins `@mermaid-js/mermaid-cli` and `playwright`); tests in `bench/tests/test_checks.py`.

**Interfaces:**
- `mermaid_check.py <md>` extracts every ```mermaid block and parses each one with `npx mmdc`. Exit 0 if all parse, 1 if any fails (the block index and the error are printed). Exit 3 if node is missing (the scorer treats that as "skipped").
- `prototype_check.py <html> [--screens N] [--script steps.json]` loads the page in headless Chromium and checks:
  - no console errors;
  - no network requests other than `file:`;
  - the banner is present;
  - optionally, the scripted clicks reach N distinct screens.

  Exit 0/1/3 as above.

- `spec_check.py <md> [--max-prose-words N] [--require mermaid,contract]` counts words outside code fences, Mermaid blocks and Markdown tables, and checks the required sections. Exit 0/1. It also feeds the scorer's `spec_prose_words` and `task_specs_per_dispatch` (T1 imports it).

**Tests:**
- `test_mermaid_valid_and_invalid` (fixtures: one good and one broken diagram).
- `test_spec_prose_count`: a fixture with 120 prose words plus fences and tables counts 120, and `--max-prose-words 100` exits 1.
- `test_prototype_ok`, `test_prototype_console_error` and `test_prototype_network_request` (3 tiny HTML fixtures).
- `test_missing_node_exits_3` (with PATH stripped).

**Wave:** 3

### T15: `superlite:writing-specs` and the task-spec template

**Files:** create `skills/writing-specs/SKILL.md` and `skills/writing-specs/templates/task-spec.md`; scenario `bench/evals/scenarios/task-spec.json`.

**Interfaces:**
- Produces the spec style table and the clarity bar (flows spec §9.2), stated as the rules every spec-producing skill cites.
- Produces the task-spec template with these sections, each omitted when it doesn't apply:
  - Component (Mermaid `classDiagram` or `flowchart`);
  - Interactions (`sequenceDiagram`);
  - States (`stateDiagram-v2`);
  - Contract (a table: signature | pre | post | invariants);
  - Edge cases (a table: input/condition | expected);
  - Local decisions (one line each).
- Prose ≤ 150 words outside fences and tables.
- Path rule: `.superlite/specs/<plan-topic>/T<N>-<slug>.md`, committed with the task's code.
- Precedence: plan > task spec. On a conflict the implementer reports `DONE_WITH_CONCERNS` with the delta.

**Tests:**
- `task-spec` (fixture: the sitegen reference plus a 1-task plan, unattended, budget $1.50):
  - the spec file exists at the path rule;
  - it has ≥ 1 Mermaid block, all `mermaid_valid`;
  - `spec_check` (T5) prose ≤ 150 words;
  - it has a contract table with ≥ 1 row;
  - the session never stops for a gate.
- A size check: the skill is ≤ 3,500 chars.

**Wave:** 1

### T6: `superlite:mvp-flow`

**Files:** create `skills/mvp-flow/SKILL.md` and `skills/mvp-flow/templates/ARCHITECTURE.md`; scenarios `bench/evals/scenarios/mvp-gate1.json` and `mvp-gate2.json`.

**Interfaces:**
- Consumes `superlite:writing-specs` (ARCHITECTURE.md follows its style), `superlite:html-prototype`, `superlite:writing-plans` and `superlite:subagent-driven-development`.
- Produces the flows spec §4 sequence:
  1. intent batch;
  2. `ARCHITECTURE.md` at the root, from the template sections: Goals/non-goals, System context (flowchart), Components (flowchart), Key flows (≤ 3 sequenceDiagrams), Data model (erDiagram), Architectural decisions (options + trade-offs + recommendation, which become ADR lines), and UI sketch (ASCII per screen + navigation);
  3. the Mermaid check before the gate;
  4. Gate 1, with decisions asked in one AskUserQuestion of ≤ 4 (extra low-stakes ones become rulings);
  5. the prototype, built by `superlite:prototyper`;
  6. Gate 2, with ≤ 2 change rounds;
  7. archive to `.superlite/`;
  8. plan (architecture = spec, `MOCK` = seed and fixtures);
  9. waves.
- Unattended: both gates pre-approved, decisions become rulings.

**Tests:**
- `mvp-gate1` (attended prompt, budget $3): "Build a small team expense tracker web app" stops for a gate. `ARCHITECTURE.md` exists at the root with ≥ 4 Mermaid blocks, all `mermaid_valid`, and ≥ 1 ```text wireframe. No `src/` code exists yet.
- `mvp-gate2` (resumed with "Approved"): `prototype.html` exists at the root and passes `prototype_ok`, and the session stops for gate 2.
- `mvp-unattended` (pre-approval prompt, budget $8): `.superlite/architecture.md` and `.superlite/prototype.html` are committed, with no root copies left, and a plan exists in `.superlite/plans/`.

**Wave:** 2

### T7: `superlite:feature-flow` and `superlite:bugfix-flow`

**Files:** create `skills/feature-flow/SKILL.md` and `skills/bugfix-flow/SKILL.md`; scenarios `bench/evals/scenarios/feature-*.json` and `bugfix-*.json`.

**Interfaces:**
- Consumes `superlite:writing-specs` (delta doc style), `superlite:sandboxing`, `superlite:html-prototype` (feature, UI-heavy only), `superlite:systematic-debugging`, `superlite:writing-plans` and `superlite:subagent-driven-development`.
- **Feature** produces:
  - Explore-first;
  - the delta doc at `.superlite/features/<date>-<topic>.md` (current/target Mermaid diagrams, changed interfaces, ASCII for changed screens, decisions batch);
  - one gate;
  - sandbox → waves → merge back → full suite in main → top-tier final review.
- **Bugfix** produces:
  - sandbox → a one-command repro that fails in seconds;
  - root cause;
  - regression test red → fix → green;
  - merge back;
  - worker-tier final review unless the fix touches auth, security, data or concurrency;
  - a ≤ 10-line record at `.superlite/bugs/`.

**Tests:**
- `feature-delta-gate` (fixture: sitegen reference): "Add pagination to the index page" creates the delta doc with 2 valid Mermaid blocks and stops for the gate. The main checkout is unchanged.
- `bugfix-seeded` (fixture: sitegen with a seeded slug bug and a hidden regression test, unattended, budget $4):
  - the sandbox is created and then torn down;
  - the hidden test passes in the main checkout;
  - the diff touches only `slug.py` and a test file;
  - the `.superlite/bugs/` record exists;
  - `skill_invoked superlite:systematic-debugging`.

**Wave:** 2

### T8: `superlite:refactor-flow`

**Files:** create `skills/refactor-flow/SKILL.md`; scenario `bench/evals/scenarios/refactor-*.json`.

**Interfaces:**
- Consumes `superlite:writing-specs` (target-diagram style), `superlite:sandboxing` (optional), `superlite:writing-plans` and `superlite:subagent-driven-development`.
- Produces:
  - current/target Mermaid diagrams and the must-not-change list;
  - characterisation tests committed first;
  - the environment choice as a `Ruling:` (in place by default, a sandbox when cross-cutting);
  - waves that stay green;
  - a top-tier final review on "no behaviour change".

**Tests:**
- `refactor-split` (fixture: sitegen reference, unattended): "Split site.py into a pipeline package, no behaviour change".
  - The hidden suite stays 89/89 green.
  - The characterisation-test commit precedes the first move commit.
  - The progress file has an environment `Ruling:` line.

**Wave:** 2

### T9: SDD 0.2 engine

**Files:** modify `skills/subagent-driven-development/SKILL.md`, `agents/implementer.md` and `agents/reviewer.md`; scenario `bench/evals/scenarios/sdd-cache.json`.

**Interfaces** (per 0.2 spec §3, with tier names from flows spec §2):
- **Pipelined wave review,** with the blocking-finding rule.
- **Final review as a fork** of the orchestrator, plus a fresh-reviewer option for experiment 1.
- **Shared wave preamble:** a fixed preamble, then the plan header and full plan pasted, then the sandbox manifest path. The per-task tail comes last.
- **Warm-then-fan-out** dispatch.
- **Identical sibling dispatch shape.**
- **Resume, not respawn.**
- **Batched waits,** and per-wave timings in the progress file.
- **Agents:**
  - `implementer`:
    - frontmatter: `experimental.cacheTtl: 1h`;
    - body says "the plan is above in your brief; do not re-read it";
    - **first step:** write the task spec per `superlite:writing-specs`, then TDD against it;
    - the report adds a `spec:` path line.
  - `reviewer`:
    - wave and final brief shapes;
    - verifies *code ⊨ task spec ⊨ plan contract*, and flags spec–plan divergence as Important.
- **Coordinator:** passes task-spec paths to the reviewers, never reads the specs itself (except to settle a dispute), and commits each task spec with its code.

**Tests:**
- `sdd-cache` (the sitegen task, unattended, budget $6):
  - one task spec per implementer dispatch, each passing `spec_check`;
  - wave siblings 2..n have `first_cache_read > 0` (target ≥ 80% of the prefix);
  - `max_dispatch_chain ≤ 4`;
  - the final review is a fork, or uses the top-tier model;
  - hidden suite 89/89.
- Size checks.

**Wave:** 2

### T10: writing-plans 0.2

**Files:** modify `skills/writing-plans/SKILL.md`.

**Interfaces:**
- Produces the `detail: contracts | tests | full` header field (default `tests`), with a per-task `full` override.
- Produces the wave preamble block: everything after the plan header is a stable prefix, so task order and wording don't change once execution starts.
- Plan budget ≤ 25k chars (above that, briefs fall back to path references).
- Plan location `.superlite/plans/`.

**Tests:** these are covered by T9's `sdd-cache`, and by T6/T7 (plans land in `.superlite/plans/`). Plus a size check, and the template contains a `detail:` line.

**Wave:** 2

### T11: brainstorming and finishing

**Files:** modify `skills/brainstorming/SKILL.md` and `skills/finishing-a-development-branch/SKILL.md`.

**Interfaces:**
- **Brainstorming** becomes the shared toolkit the flows call: the intent batch, the decision-batching rules (≤ 4 per AskUserQuestion, recommendation first, ADR write-back to `.superlite/decisions.md`) and unattended rulings. Its sizing section defers to the router in `using-superlite`.
- **Finishing** adds the sandbox merge-back and teardown, and ensures the `.superlite/` artifacts are committed. The remote push or PR stays a human choice.

**Tests:** these are covered by T6–T8 scenarios: `.superlite/decisions.md` exists after `mvp-unattended`, and the worktree is gone after `bugfix-seeded`. Plus size checks.

**Wave:** 3

### T12: Benchmark tasks

**Files:** create `bench/tasks/sitegen-feature/` (SPEC delta + hidden tests), `bench/tasks/sitegen-bugs/` (3 seeded-bug patches against the reference + hidden regression tests), `bench/tasks/sitegen-refactor/` (brief; it reuses the 89 hidden tests), and `bench/tasks/tasklist-web/` (a one-paragraph brief, `steps.json` for `prototype_check`, and hidden API/unit tests against the brief's stated contract). Move `bench/task/` to `bench/tasks/sitegen/`, and update the `harness.py` default path.

**Interfaces:**
- Each task dir has `brief.md`, `fixture/` (optional starting repo), `hidden_tests/` and `task.json` `{flow, max_budget_usd, gates_expected}`.
- Consumes `harness.py --task` (T1, names only).

**Tests:**
- Every fixture + reference passes its own hidden tests where the task expects a pass (feature: the reference with the feature applied by the task author).
- Each seeded bug makes exactly its own hidden regression test fail.

**Wave:** 3

### T13: README, docs and version

**Files:** modify `README.md` (the flows section with the router diagram, the tiers, the `.superlite/` folder, sandboxes, cache settings), `AGENTS.md` and `CLAUDE.md` (if they list skills), and `.version-bump.json` → 0.3.0.

**Tests:** every skill directory is listed in the README; `scripts/bump-version.sh --check` passes.

**Wave:** 4

### T14: Evals, experiments and report (coordinator)

1. Run all scenarios with `bench/evals.py`. Every one must pass. Fix rounds follow the SDD rules.
2. **Experiment matrix,** 3 sequential reps each, on `sitegen` and the new tasks:
   - 0.1 vs 0.2 engine;
   - final review: Sonnet-high vs a top-tier fork vs a fresh top-tier reviewer;
   - `detail`: contracts / tests / full;
   - cache: baseline vs preamble + warm-then-fan-out.
3. Update `bench/report.md` (mean/min/max, sibling cache hit rate, orchestrator cost share, gates hit) and the README benchmark table.
4. The default settings become whatever wins. Record the choices as rulings.

**Done:**
- all evals pass;
- 0.3 is at least as fast as 0.1 on `sitegen` (±15%);
- sibling cache hit rate ≥ 80%;
- the report is committed.

The push waits for the user.

## Rulings

- Ruling: 0.2 and 0.3 ship from one branch, but the experiment matrix measures each 0.2 lever separately (via `--variant`), so the flows don't confound the engine numbers.
- Ruling: skill behaviour is tested with headless scenario evals on the real harness, not unit tests. Each scenario is budget-capped ($0.50–$8), and the full eval pass is estimated at under $40.
- Ruling: browser checks use Playwright and the Mermaid CLI via `npx`. When node is missing they report "skipped", never "pass".

## Related
- Spec: [[2026-10-17-superlite-flows-design]]
- Project: [[Superlite]]

Hub: [[Plans index]]
