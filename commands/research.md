---
description: Research a topic with parallel agents, a sourced ledger and a report at your level.
argument-hint: "[the topic or question | --level director|vp|researcher|simple [slug]]"
---

Superlite research mode. Topic: $ARGUMENTS

First, read `${CLAUDE_PLUGIN_ROOT}/assets/flows/research.txt` (if the variable is not expanded, it is `assets/flows/research.txt` in the superlite plugin directory) and print it verbatim in one ```text block, before anything else. It is stored, so never redraw or edit it. Skip it only when it was already printed this turn.

Arguments: `--level director|vp|researcher|simple [slug]` is a re-render of an existing run (see Re-render below). The topic words for a new run are everything that is not a `--level` flag.

## New run

If the topic is empty, ask for it in one line (the question, and the language level: Director, VP, Researcher or Simple).
Once the request is known, and BEFORE the first other `superlite-cli` call, run `superlite-cli session ensure --slug <topic>` (`<topic>`: a short slug of the request, a-z, 0-9, `-`; reuse it for plan file names and as `--slug` in every research call). On a `SESSION worktree=<path>` line, move the agent root to `<path>` when it differs from the current checkout; on `REDIRECT worktree=…`, open that worktree instead.
If ensure prints an `ASK` line (`ASK untracked=...` or `ASK move ...`, exit 4, nothing changed), ask exactly what the `ask:` line after it says, then do exactly what the chosen answer's `on ...:` line says (on a refusal, stop; nothing changed). If the hook printed `superlite sync:`, ask the same way; sync is fast-forward only.
After ensure, ask the orchestrator choice (human or AI) unless the user already said (`superlite-cli orchestrator status` shows the current one); run `superlite-cli orchestrator set human|ai`.
Then load `superlite:research` and follow it: plan, critical questions, approval (human mode), parallel researcher agents writing to the ledger, contradictions, lint, then the report at the chosen level. Download files only after an explicit yes. Last, after reporting, validation and the download decision (also when downloads are declined), run `superlite-cli finish` in the session checkout and report its summary.

## Re-render

With a `--level` flag, only re-render: no `session ensure` for a new run, no `research init`, no agent dispatch.
1. List the existing ledgers with `ls .superlite/research/`. Use the slug given; otherwise, if more than one ledger exists, ask which; if one, use it.
2. Run `superlite-cli research render --slug <slug> --level <level>` into `docs/plans/<date>-<topic>.findings.md`.
3. Run `superlite-cli research lint --slug <slug> --parity docs/plans/<date>-<topic>.findings.md`.
4. Report the file, the level and the lint result.
