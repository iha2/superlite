---
name: testing
description: Use when implementing any feature or bugfix, before writing implementation code
---

# Testing

Write the test first. Watch it fail. Write the minimum code to pass. No production code without a failing test that demands it.

If you wrote code before the test, delete it and restart from the test. Do not keep it as "reference".

## Cycle

1. **Red.** Write one small test for one behavior: a clear name, real code (mocks only when unavoidable), exact expected values. Run only that test. Confirm it fails for the right reason (the feature is missing, not a typo or import error). A test that passes immediately proves nothing: fix the test.
2. **Green.** Write the simplest code that passes. No extra features, no refactoring of other code. Run the focused tests (the new test plus the tests in the same file or module). Confirm green and no new failures there.
3. **Refactor.** Remove duplication, improve names, keep tests green. Skip if nothing needs it.
4. Repeat for the next behavior.

## Scope of runs

- Run only focused tests inside a task: the new test, then its file or module.
- Do not run the full suite inside a task. The wave's `bash <skill-dir>/../swarm/scripts/sl run_tests --full` run covers it; inside a task use `--fast PATHS`. When working inline without waves, run `--full` once at the end and read its one-line summary.

## Bugfixes

Reproduce the bug as a failing test first, see it fail, then fix. The test is the regression guard. Use `superlite:diagnosis` to find the root cause before writing the fix.

## Good tests

- One behavior per test; "and" in the name means split it.
- Assert observable behavior, not implementation details.
- Cover the contract's edge cases: empty input, errors, boundaries.

## Exceptions

Throwaway prototypes, generated code and pure configuration need no test-first. If unsure whether it applies, it does.

## Red flags: stop and restart from the test

- Code written before the test.
- The test passed on its first run.
- "I'll add tests after", "too simple to test", "just this once".
- Several behaviors implemented before any test ran.

## Done checklist

- Every new function or behavior has a test that you saw fail first.
- Each failed for the expected reason, and the minimal code made it pass.
- Focused tests are green, output clean.
- Edge cases and errors covered.

Cannot check every box: say which and why. Then see `superlite:proof`.
