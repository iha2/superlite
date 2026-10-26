---
name: proof
description: Use when about to claim work is complete, fixed, or passing, before committing or creating PRs
---

# Proof

Evidence before claims. Never say "done", "fixed" or "passing" without fresh proof from this session.

## Acceptable evidence

- The one-line summary (`passed failed skipped seconds`) from `bash <skill-dir>/../swarm/scripts/sl run_tests --full` after the last change.
- Or a focused run you executed yourself after the last change, covering exactly what you claim.
- For a build, a lint or a type check: that command's exit status and summary line.
- For a bug fix: the original reproduction now passing, plus its regression test.

## Rules

1. Name the command that proves the claim.
2. Run it fresh, or reuse a result only if nothing changed since that green run. If nothing changed, do not re-run.
3. Read the 2 to 3 lines the claim rests on: the counts, the exit code, the failure ids. Not just "no error scrolled by".
4. State the result honestly: "47 passed, 0 failed (run_tests --full, after the last edit)", or the actual failures.
5. Scope the claim to the evidence. A passing unit test does not prove the build works. A clean lint does not prove tests pass. An agent saying "DONE" does not prove the diff is right: check `git status --short` and `git diff --stat`, and read the evidence it reports.

## Full-suite runs

Run `bash <skill-dir>/../swarm/scripts/sl run_tests --full` for full suites (or `--fast PATHS` for a focused run); it prints one summary line, so logs stay out of your context. Run it once per wave and once at the end, not after every small edit.

## Requirements and delegated work

Re-read the plan or request and tick each requirement off against the code and tests. A green suite does not mean the spec is met. Confirm the files a subagent changed match what its task owned.

## Red flags: stop and verify first

- "Should work now", "probably fine", "I'm confident".
- Satisfaction before the check ("Great!", "Done!").
- About to commit, push or open a PR without a check.
- Trusting a report without looking at its evidence.
- "Just this once", or being tired and wanting to finish.

## Never claim

Success with failures you decided to ignore. Say what fails and why it is acceptable, or fix it.
