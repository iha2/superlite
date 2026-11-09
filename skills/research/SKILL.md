---
name: research
description: Use when the user wants a topic researched with sources, a sourced report at a chosen language level, or conflicting evidence compared
---

# Research

Gather evidence in parallel, keep it in a level-free ledger, and render the report from the ledger. Truth and depth never change with the level; only the wording does. Keep every claim tied to a source id. Never touch `skills/using-superlite`.

CLI: `bash <skill-dir>/../swarm/scripts/superlite-cli research` (subcommands `init`, `source`, `claim`, `contradiction`, `lint`, `render`, `downloads`, `download`). Every call carries `--slug <slug>` (the session slug, the `slug=` field of the first `SESSION` line that `session ensure` printed, not the slug first requested; it is also the `<topic>` in the plan file names) or `--ledger <path>`; without one it exits with an error. Plans and reports live in `docs/plans/`. Below, `research` means that CLI call and `S` is the slug.

## Steps

1. **Ask.** Use the session slug you were handed as S. Get the topic and the language level: Director, VP, Researcher or Simple. Ask the user to define the evidence tiers (for example `A,B,C`, best first); the tiers are theirs, not a fixed scale. Run `research init --slug S --topic "<topic>" --tiers <A,B,C>`.
2. **Plan.** Write `docs/plans/<date>-<topic>.research.md`: scope, sub-questions, search strategy, evidence tiers. The most recent well-referenced paper is the highest evidence; a newer source of a higher grade supersedes an older one.
3. **Critical questions.** Ask the human every open question in ONE batch. In AI mode answer them yourself as `Ruling: <decision> (because <reason>)` lines in the plan.
4. **Approve.** Human mode: wait for the human's approval, then run `bash <skill-dir>/../swarm/scripts/superlite-cli orchestrator approve --review docs/plans/<date>-<topic>.research.md`. AI mode: skip.
5. **Research.** Dispatch `superlite:researcher` agents in one message, one sub-question each, as many as needed. Each brief must give SUPERLITE (the plugin root, so the agent can run `bash $SUPERLITE/skills/swarm/scripts/superlite-cli research ...`), the slug S, the sub-question, and the exact commands, each with `--slug S`: `research source --slug S --id <id> --url <url> --date <date> --grade <tier>`, `research claim --slug S --id <id> --text "<claim>" --source <source id>`, `research contradiction --slug S ...` and `research lint --slug S`. Agents write only through the research CLI, never to the report or the ledger file.
6. **Contradictions.** Open a conflict with `research contradiction --slug S --claim C1 --claim C2 --cause "<cause>" --status open` (ids K1, K2, ... are assigned). Close it later with `research contradiction --slug S --id K1 --status resolved --cause "<cause>"`, never by recording the claims again. Cause is one of: definition, date, population, method, source quality, error. A gap or an unexplained conflict loops back to step 5 with a new sub-question.
7. **Lint.** Run `research lint --slug S`: every claim has a source, every conflict has a cause. Fix what it reports.
8. **Write.** Render the report at the chosen level with `research render --slug S --level director|vp|researcher|simple` into `docs/plans/<date>-<topic>.findings.md`. Generate the other two files from the ledger, not by hand: `research render --slug S --part sources` into `docs/plans/<date>-<topic>.sources.md` and `research render --slug S --part contradictions` into `docs/plans/<date>-<topic>.contradictions.md`. Every level carries the same claim ids, citations, numbers, and open and resolved contradictions with causes. Then run `research lint --slug S --parity docs/plans/<date>-<topic>.findings.md` on the complete level reports only, and fix mismatches; the sources and contradictions files are not checked by `--parity`. If the user changes level, re-render from the ledger into the `.findings.md` file and lint it again; do not re-research.
9. **Download (opt-in).** Run `research downloads --slug S`, then ask once, listing name, source and size of each file. Run `research download --slug S --yes` (optional `--out DIR`) only after a yes. Paywalled items stay link-only.
10. **Finish.** After reporting, validation and the download decision (also when downloads are declined), run `bash <skill-dir>/../swarm/scripts/superlite-cli finish` in the session checkout and report its summary.

## Rules

- The ledger is the single source of truth; reports are views of it.
- Never soften, drop or add a fact to fit a level.
- Never download without an explicit yes.
