# Feature plan: `research` mode

**Orchestrator:** human. **Slug:** `research-mode`. **Kind:** feature (existing code, new flow).

## Goal and who it is for

A new superlite mode, `/superlite:research <topic>`, for anyone who needs a researched answer with the scrutiny of a university paper: a plan, the critical questions asked up front, parallel research, contradictions found and explained, every claim tied to a source. The reader picks the language level (Director, VP, Researcher, Simple); truth, depth and the contradiction analysis are identical at every level, only the wording changes.

## What the user will see or do differently

Target kind: a slash command / flow plus one CLI tool and one skill. No UI.

1. `/superlite:research <topic>` (also offered by `/superlite:start` and `suggest_flow`) prints the stored flow diagram, runs `session ensure`, then asks one batched question: language level, orchestrator (if unset), and the critical questions it found that only the human can answer.
2. It writes a research plan `docs/plans/<date>-<topic>.research.md` (scope, sub-questions, critical questions, source-tier rules, search strategy). Human mode: the user approves it before any research agents run.
3. Parallel research agents each take one sub-question and write only to a ledger (sources, claims, evidence grade), never to the report.
4. A contradiction pass compares claims across agents and sources, records each conflict and its root cause (different definition, date, population, method, source quality, error), and resolves it or flags it open.
5. Output, next to the other plan kinds in `docs/plans/`: `<date>-<topic>.research.md` (the research plan), `<date>-<topic>.findings.md` (the report at the chosen level), `<date>-<topic>.sources.md` (source ledger), `<date>-<topic>.contradictions.md`. The report is not named `<date>-<topic>.md`, which is the machine-plan name.
6. Re-render: `/superlite:research --level vp` re-renders the report from the stored ledger at another level without re-researching.
7. Download: at the end it lists each downloadable file (name, source, size) and fetches only after the user says yes, into `docs/plans/<date>-<topic>.sources/`. Paywalled or unreachable items stay link-only.

## Decisions (from the user's answers)

- Ruling: output lives in `docs/plans/` with a `.research.md` suffix, same as `.feature.md`, `.review.md`, `.proto.md`.
- Ruling: the user defines the evidence tiers. The plan ships with a `## Evidence tiers` section the user fills at approval. Until set, the only built-in rule is the user's stated one: the most recent, well-referenced research paper is the highest grade; every claim records grade, publication date and link, and a newer higher-grade source supersedes an older one.
- Ruling: downloads are opt-in, listed with name, source and size, fetched only on a yes.
- Ruling: the level is chosen at start; findings, evidence and contradictions are stored level-free in a ledger so any level can be re-rendered.

## Acceptance checks

1. `/superlite:research` appears in `/superlite:start`, `help`, `intro`, README and `suggest_flow` (a request such as "research how X compares to Y" suggests `research`).
2. Running it on a sample topic produces the research plan, then (after approval) the three markdown files.
3. Every claim in the report cites a source id that exists in the sources file; `superlite-cli research lint` fails otherwise.
4. Every contradiction has a recorded cause and a status (resolved or open); lint fails on a missing cause.
5. Rendering the same ledger at all four levels passes `research lint --parity`: every render is checked against the ledger's records (claims with their quantities and citations, source metadata, and open and resolved contradictions with their causes) before levels are compared to each other. Only the surrounding wording differs by level.
6. No file is downloaded without a yes; the list shows name, source, size.
7. Existing tests pass; new tests cover suggest_flow, the lint, the flow end-to-end, and the help/intro contract.

## Code map

Modes are not an enum. A flow is: a command file, a stored diagram, a skill, `suggest_flow` tables and mentions in docs/help.

- New: `commands/research.md` (model on `commands/debug.md`), `assets/flows/research.txt` (80 columns max, style of `debug.txt`), `skills/research/SKILL.md` (two-key frontmatter, description starts "Use when"), `skills/swarm/scripts/research.py` (CLI tool, argparse `main(argv)`, state under `.superlite/research/<slug>/ledger.json`, subcommands `init`, `source`, `claim`, `contradiction`, `lint [--parity FILES]`, `fetch-list`), `docs/research-flow.md`.
- Edit: `commands/start.md` (lines 2, 3, 8, 12, 20, 23-26), `skills/swarm/scripts/suggest_flow.py` (`FLOWS` line 25, `WORDS` 31-48, `suggest()` 98-146, docstring), `assets/help.txt`, `assets/intro.txt` (both ≤ 80 columns, must list every `commands/*.md`), `README.md` (lines 30-33, 43, 44-47), `docs/tools.md` (line 34 plus the new tool), `docs/feature-flow.md`, `docs/sessions.md` line 74, `docs/skills-and-agents.md`, `docs/README.md`, `skills/swarm/scripts/superlite-cli` header comment.
- Frozen / must not change: hook behavior (no mode names; the approval gate stays as is, and the researcher gets no exemption from it), per-harness manifests (skills registered by directory) except the agent-name lists in the Kimi and OpenCode adapters, the orchestrator state files, `skills/using-superlite/SKILL.md` (2,000-character cap, already near it; the skill map is left alone, `start` routes to research).
- Agents: research needs web search. Existing agents grant no web tools, `general-purpose` is blocked during gated runs, and `Explore` is allowed (`hooks/_edits:69`). Decision for the build: add one agent, `agents/researcher.md` (flat frontmatter, `tools: Read, Write, Grep, Glob, Bash, WebSearch, WebFetch`; Bash is needed to run the research CLI), and add it to the hard-coded agent name lists in `.opencode/plugins/superlite.js:74` and `.kimi-plugin/plugin.json:23`. It is not added to the approval-gate exemption list in `hooks/_edits`: researchers stay blocked until the human approves and run normally after. The agent writes only through `research.py`.
- Tests (pytest, `uv run pytest tests/scripts tests/bench`): `test_suggest_flow.py` rows, `test_flows.py::test_flow_research`, `test_session_start.py` (help/intro coverage), `test_doc_contract.py` (all `superlite-cli` calls in new docs must be real), new `test_research.py` (lint, parity), `test_agent_files.py` (new agent).
- Conventions: Python 3.12 with uv, bash 3.2-compatible hooks, LF endings, one feature per worktree, no agent trailers on commits.

## Prototype scope (sandbox, L1 stubs, about 6 tasks)

Smallest runnable cut: `research.py` with `init`, `source`, `claim`, `contradiction`, `lint` against a JSON ledger, a canned two-source topic fixture, a renderer stub that emits a level-tagged report from the ledger, and the parity check. The user tries it with a fixture ledger: run lint (pass and fail cases) and render all four levels to see identical facts with different wording. No web calls, no agent, no docs edits.

## Integration build outline (one task per touched module, existing interfaces frozen)

1. `research.py` full tool plus `test_research.py`.
2. `skills/research/SKILL.md` (plan, critical questions, fan-out, contradiction pass, level rendering, opt-in download) and `agents/researcher.md` plus the name-list edits (no hook exemption).
3. `commands/research.md`, `assets/flows/research.txt`, `commands/start.md`.
4. `suggest_flow.py` plus tests.
5. `help.txt`, `intro.txt`, README, docs (`tools`, `feature-flow`, `sessions`, `skills-and-agents`, `README`, new `research-flow.md`).
6. `test_flow_research` and seam checks: doc contract, 80-column limit, agent file lint, existing suites green.

## Open items for you at approval

- Fill in the evidence tiers (see Decisions). If you leave them blank, the build uses only the recency-and-reference rule above and marks the tiers as "user-defined, unset".
- Confirm adding a new `researcher` agent with web tools (the alternative is reusing `Explore`, which cannot be relied on for web search).
