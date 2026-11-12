# Research plan: how superlite learns across runs

Slug: `superlite-learning-loop` · Level: Researcher · Orchestrator: human

## Question

Knowing how superlite works, what parts of it can we use to improve from one run to the next? Do we store errors and learn from them? What would help superlite perform better in each repo?

## Scope

- In: the superlite plugin source in this repo (`skills/`, `agents/`, `commands/`, `hooks/`, `skills/swarm/scripts/*.py`, `tests/`), `bench/` and its results, this repo's own `.superlite/` run data, and the docs. Also external literature on how coding agents improve themselves (memory, reflection, routing calibration).
- Out: run data in the user's other local repos (Ruling from the user: this repo only). No code changes in this run.

## Evidence tiers (best first)

- **A**: superlite code and tests, checked by reading or running them.
- **B**: stored run data (`.superlite/incidents.jsonl`, reports, history, `bench/results`).
- **C**: superlite docs and README claims.
- **D**: external papers and articles on agent self-improvement.

A newer source of a higher grade supersedes an older one. Among D sources, the most recent well-referenced paper ranks highest.

## Sub-questions (one researcher each)

1. **Capture.** What does superlite write down during and after a run? This covers incidents (kinds, fields, who logs them), checkpoints, journal, metrics, cost, reports, history, predict, and the review packet and findings. Where are these files kept, are they per run or per repo, and how long do they last (gitignored, removed on finish or on session close)?
2. **Read-back.** Which of that stored data is read back and changes what superlite does next? Examples: incident hotspots in reviewer briefs, `routing.py` kind-miss (no more haiku), `predict.py` calibration, budget, `project_config`. Is this read-back within one run or across runs? What is written but never read?
3. **Bench and calibration.** What does `bench/` measure (harness, ladder10, predict_calibration, routing_evidence, mechanical A/B, history_import)? Do its results feed back into the shipped defaults, and how (by hand or automatically)? What does this repo's own `.superlite/` and `bench/results` data actually contain?
4. **External practice.** How do current coding-agent systems learn across runs per repository? Examples: reflection/Reflexion-style memory, project memory files (CLAUDE.md, AGENTS.md, Cursor rules), failure-pattern libraries, routing and model-tier calibration from outcomes, eval-driven prompt tuning. Which mechanisms have evidence behind them, and which fit superlite's file-based, stdlib-only design?

## Search strategy

- Sub-questions 1–3: grep and read the scripts, their tests and the skill/agent prompts; run CLI subcommands read-only (`incident hotspots`, `report`, `history`) in this worktree; follow each file path from write site to read site.
- Sub-question 4: web search for 2024–2026 papers and engineering posts. Record date and grade D.

## Deliverable

A Researcher-level findings report with:

1. A capture → read-back gap map (stored / used within the run / used across runs / wasted).
2. A ranked list of per-repo improvement mechanisms, each tied to existing code it could build on.

## Rulings

- Ruling: tiers A,B,C,D as proposed (because the user chose them).
- Ruling: run data from this repo only (because the user chose it).
- Ruling: the report ends with a gap map and ranked ideas (because the user chose it).
- Ruling: researchers read plugin code at `main` (9a407ed) via `git show main:<path>` / `git grep main`, not this worktree's older base ce12462 (because main carries the newest flows; the user did not choose otherwise).

## Round 2 (practitioner advice; approved by the user's "yes" on 2026-11-12)

Round 1 searched academic papers only. Round 2 looks for advice from people who build and run agent harnesses (engineering blogs, tool docs, practitioner write-ups), grade D, same ledger.

5. **Harness improvement from run logs.** How do teams use transcripts, failures and incident logs to improve agent harnesses (Anthropic, OpenAI, Cognition, Sourcegraph, Factory, OpenHands and others)? Failure taxonomies, error analysis, trace review cadence.
6. **Per-repo memory and rules in practice.** How Cursor rules, Copilot instructions, Aider conventions, Claude Code CLAUDE.md, auto memory and hooks, and Windsurf/Devin knowledge are recommended to be written, kept small, and updated from mistakes. What practitioners report works and fails.
7. **Eval-driven routing and prompt tuning in production.** How teams pick model tiers per task, run regression evals on agent prompts and skills, and decide when a change ships (LLM-as-judge, golden tasks, cost/quality dashboards).
