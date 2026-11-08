---
name: research
description: Use when the user wants a topic researched with sources, a sourced report at a chosen language level, or conflicting evidence compared
---

# Research

Gather evidence in parallel, keep it in a level-free ledger, and render the report from the ledger. Truth and depth never change with the level; only the wording does. Keep every claim tied to a source id. Never touch `skills/using-superlite`.

CLI: `bash <skill-dir>/../swarm/scripts/superlite-cli research` (subcommands `init`, `source`, `claim`, `contradiction`, `lint`, `render`, `downloads`, `download`). Plans and reports live in `docs/plans/`.

## Steps

1. **Ask.** Get the topic and the language level: Director, VP, Researcher or Simple. Ask the user to define the evidence tiers (for example `A,B,C`, best first); the tiers are theirs, not a fixed scale. Run `research init --topic "<topic>" --tiers <A,B,C>`.
2. **Plan.** Write `docs/plans/<date>-<topic>.research.md`: scope, sub-questions, search strategy, evidence tiers. The most recent well-referenced paper is the highest evidence; a newer source of a higher grade supersedes an older one.
3. **Critical questions.** Ask the human every open question in ONE batch. In AI mode answer them yourself as `Ruling: <decision> (because <reason>)` lines in the plan.
4. **Approve.** Human mode: wait for the human's approval, then run `bash <skill-dir>/../swarm/scripts/superlite-cli orchestrator approve --review docs/plans/<date>-<topic>.research.md`. AI mode: skip.
5. **Research.** Dispatch `superlite:researcher` agents in one message, one sub-question each, as many as needed. Agents write only through the research CLI (`source`, `claim`, `contradiction`), never to the report or the ledger file.
6. **Contradictions.** Record each conflict with `research contradiction --claim ... --cause ... --status resolved|open`. Cause is one of: definition, date, population, method, source quality, error. A gap or an unexplained conflict loops back to step 5 with a new sub-question.
7. **Lint.** Run `research lint`: every claim has a source, every conflict has a cause. Fix what it reports.
8. **Write.** Render the report at the chosen level with `research render --level director|vp|researcher|simple` into `docs/plans/<date>-<topic>.findings.md`, and write `<date>-<topic>.sources.md` and `<date>-<topic>.contradictions.md` from the ledger. Every level carries the same claim ids, citations, numbers, and open and resolved contradictions with causes. Then run `research lint --parity <files...>` on the written files and fix mismatches. If the user changes level, re-render from the ledger; do not re-research.
9. **Download (opt-in).** Run `research downloads`, then ask once, listing name, source and size of each file. Run `research download --yes` (optional `--out DIR`) only after a yes. Paywalled items stay link-only.

## Rules

- The ledger is the single source of truth; reports are views of it.
- Never soften, drop or add a fact to fit a level.
- Never download without an explicit yes.
