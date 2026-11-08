# Research flow

`/superlite:research` answers a topic with parallel agents, a sourced
ledger and a report at the language level you pick.

## The nine steps

1. Ask: topic, level (Director, VP, Researcher, Simple), evidence tiers.
2. Plan: scope, sub-questions, search strategy, tiers, in
   `docs/plans/<date>-<topic>.research.md`.
3. Questions: critical questions asked once (AI mode: `Ruling:` lines).
4. Approve: human mode only, `superlite-cli orchestrator approve --review`.
5. Research: `superlite:researcher` agents, one sub-question each, write
   to the ledger through `superlite-cli research`.
6. Contradictions: each conflict gets a cause and a status; gaps loop to 5.
7. Lint: `research lint` checks sources, causes and structure.
8. Write: report, sources and contradictions; `research lint --parity`.
9. Download: optional, only after a yes.

## Ledger

Level-free JSON: `topic`, `tiers`, `sources` (id, url, date, grade, kind,
file_url, size), `claims` (id, text, sources), `contradictions` (claims,
cause, status `resolved` or `open`). Tiers are user-defined. The newest
well-referenced source of the highest grade wins over an older one.

## Levels

Director, VP, Researcher and Simple differ in wording only. Every level
shows the same claim ids, citations, numbers and contradictions with
causes. Changing level re-renders from the ledger (`research render
--level`); nothing is re-researched.

## Output files

- `docs/plans/<date>-<topic>.research.md`: the plan
- `docs/plans/<date>-<topic>.findings.md`: the report at your level
- `docs/plans/<date>-<topic>.sources.md`
- `docs/plans/<date>-<topic>.contradictions.md`

## Download policy

`research downloads` lists name, source and size. `research download
--yes` fetches only after you say yes. Paywalled items stay link-only.
