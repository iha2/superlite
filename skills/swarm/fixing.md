# Fixing: routing, escalation, contract

The coordinator never edits source or tests and never runs raw `pytest`. Every fix goes to a fix-only agent.

| Tier | Agent | Use when |
|---|---|---|
| 1 | `superlite:fixer-sonnet` (sonnet, medium) | Cause known, including a fully specified change (exact `file:line`); fix confined to one module, plus a regression test. |
| 2 | `superlite:fixer-sonnet-high` (sonnet, high) | Cause known, but the fix spans modules or a seam, or a known probe class (deep recursion, huge input, protocol edge). |
| 3 | `superlite:fixer-opus` (opus, high) | Cause unknown and needs diagnosis, or a lower tier failed 2 rounds. |

Haiku never fixes: a fix keeps a decision. Haiku stays only as `implementer-haiku` on `Kind: mechanical` tasks.

- **Escalation:** after 2 failed rounds at a tier, move up one. Log each failed round with `incident`.
- **Owner reuse:** if the task's own implementer is idle, within reuse limits (<=3 tasks, <=40k context for fix work, <=50 min idle) and its model matches the tier, send it a fix-only brief instead of a fresh fixer. Over 40k context, start a fresh fixer instead.

## Fix notes: compaction in a file

Subagents can't compact themselves, so the working state of a fix lives in `.superlite/fixes/<id>.md`, not in a long session. Give each finding an id in the brief (`F-<Tn>-<n>`). Every fixer round ends by rewriting that note, cumulatively, in at most 1,500 chars:

```
# F-T4-2 · tier 1 · round 1 · NOT_FIXED
Goal: <the finding and what done looks like, one line>
Where: <file:line-range for each relevant span>
Changed: <file:lines - what - why>, or "nothing kept"
Tried and failed: <attempt - failure - reason>, newest first
Evidence: <failing test or probe id - one-line error>
Next: <best next hypothesis, and which spans to read first>
```

- **A later round never resumes the old session.** Start a fresh agent (round 2 at the same tier, or the next tier on escalation) whose brief is only `$CLI`, `Tn`, the finding id and "read `.superlite/fixes/<id>.md` first". Paste no transcript or report into it.
- This keeps each round's context to the note plus the spans it names, roughly 10-20k instead of a reused 60-100k session. Escalation needs it anyway, since a different model shares no cache.
- `.superlite/` is never committed. Notes still marked NOT_FIXED after the last tier become a `Ruling:` and go in the closing message.
- **Package reviewers** still fix what they find, but must close every finding, including ones sent later, and report a closed list.
- **Final reviewer** only reports findings. They go to fixers, routed by tier and split by file.
- **Lessons for fixers:** append `bash "$CLI" lessons brief --agent fixer --task Tn --file F` output (one call per finding file) to the fixer brief.
- **Fixer contract:** fix only the named findings; one regression test per fix; run `bash "$CLI" run_tests --affected --files <touched files> --background` and wait only for that summary; `checkpoint <Tn>`; report a closed list. Never refactor, never touch files outside the finding.
- Never pass `model` to a superlite agent call (it overrides the agent file). Never dispatch an extra or ad-hoc reviewer, and never `general-purpose` for build, review or fix work. An inadequate review gets a `Ruling:`.
- **Mechanical misses:** when an `implementer-haiku` task ends NEEDS_CONTEXT, BLOCKED or red, route it to tier 1 (`fixer-sonnet`) with a fix note, and run `superlite-cli routing miss --task Tn --plan PLAN` (logs `kind-miss Tn`; the task is never retried on Haiku) so the first-time pass rate of mechanical tasks can be measured.

## Fixer agent rules (shared by all three fixer files)

Your brief gives the plugin script as `$CLI` (the `superlite-cli` path), a task id `Tn`, and a closed list of findings, each `file:line - problem - fix`, or a failing test id or probe id. You fix those and nothing else.

- Fix only the listed findings. No refactors. Touch no file outside the findings, except the test file for the regression test.
- For each finding: read the code, make the fix, add one regression test that fails without it.
- Run only the touched test files, in the background: `bash "$CLI" run_tests --affected --files <touched files> --background`, then `bash "$CLI" run_tests --wait --run <id>` (the `<id>` from the `wait:` line it printed; `--fast PATHS` is the manual fallback). Read only its summary. Never run the full suite, raw `pytest` or `python`.
- Your brief names a finding id. If `.superlite/fixes/<id>.md` exists, read it first: it is your only history. Start from its Where and Next spans, and do not repeat anything in its Tried-and-failed list.
- Before your report, rewrite `.superlite/fixes/<id>.md` for each finding, in at most 1,500 chars, with a heading `# <id> · tier N · round R · FIXED|NOT_FIXED`, then the lines Goal, Where (file:line ranges), Changed (what and why), Tried and failed (attempt, failure, reason), Evidence, Next. Write it even when the fix succeeded.
- Checkpoint: `bash "$CLI" checkpoint <Tn>` (task ids only; it records that task's Owns files plus tests/regression/ as a hidden checkpoint, and blocks anything else, so put regression tests there or in the task's own test files). Never run `git add`, `commit`, `switch`, `checkout` or `stash`. Never spawn agents.
- Kill only processes you started, by PID. Never `taskkill /IM`, `pkill`, `killall` or `Stop-Process -Name`.

Report, at most 150 words, no file contents or logs. A closed list with one line per finding: `FIXED - <finding>` or `NOT_FIXED - <finding> - <reason>`. Then the test result as `tests: <passed> <failed> <skipped> run <id>` and the checkpoint id. Never leave a finding off the list.
