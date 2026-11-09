# Research flow

`/superlite:research` answers a topic with parallel agents, a sourced
ledger and a report at the language level you pick.

## The ten steps

The slug is the `slug=` field of the first `SESSION` line that
`superlite-cli session ensure` prints (a repeated topic may get `battery-2`,
not `battery`). It is the `<topic>` in the file names and the `--slug` of
every research call.

1. Ask: topic, level (Director, VP, Researcher, Simple), evidence tiers.
2. Plan: scope, sub-questions, search strategy, tiers, in
   `docs/plans/<date>-<topic>.research.md`.
3. Questions: critical questions asked once (AI mode: `Ruling:` lines).
4. Approve: human mode only, `superlite-cli orchestrator approve --review`.
5. Research: `superlite:researcher` agents, one sub-question each, write
   to the ledger through `superlite-cli research`. Every call carries
   `--slug <slug>` (or `--ledger <path>`); the brief gives the slug, the
   plugin root and the exact commands.
6. Contradictions: each conflict gets a cause and a status; gaps loop to 5.
7. Lint: `research lint --slug <slug>` checks sources, causes and structure.
8. Write: report, sources and contradictions; `research lint --slug
   <slug> --parity` on the level reports.
9. Download: optional, only after a yes.
10. Finish: `superlite-cli finish` after the report and the download
    decision.

## Ledger

Level-free JSON: `topic`, `tiers`, `sources` (id, url, date, grade, kind,
file_url, size), `claims` (id, text, sources), `contradictions` (claims,
cause, status `resolved` or `open`; ids K1, K2, ... are assigned). Open a
conflict with `contradiction --claim C1 --claim C2 --cause ... --status
open`; close it with `contradiction --id K1 --status resolved --cause ...`.
Tiers are user-defined. The newest
well-referenced source of the highest grade wins over an older one.

## Levels

Director, VP, Researcher and Simple differ in wording only. Every level
shows the same claim ids, citations, numbers and contradictions with
causes. Changing level re-renders from the ledger (`research render
--slug <slug> --level`, or `/superlite:research --level L [slug]`);
nothing is re-researched. After a session switch or close the ledger is
parked in the session store: `research list` shows every ledger as
`<slug> <live|parked:<session>> <path>`; render, lint and downloads read a
parked one, while init, source, claim, contradiction and download refuse it
until `superlite-cli session use` brings that session back.

## Output files

- `docs/plans/<date>-<topic>.research.md`: the plan
- `docs/plans/<date>-<topic>.findings.md`: the report at your level
- `docs/plans/<date>-<topic>.sources.md`: `research render --slug
  <slug> --part sources`, redirected into the file
- `docs/plans/<date>-<topic>.contradictions.md`: `research render
  --slug <slug> --part contradictions`, redirected into the file

`research lint --slug <slug> --parity` runs only on the complete level
reports (the `.findings.md` files), not on the sources or contradictions
files.

## Download policy

`research downloads` lists name, source and size. `research download
--slug <slug> --yes` fetches only after you say yes. Paywalled items
stay link-only.
