---
name: diagnosis
description: Use when encountering any bug, test failure, or unexpected behavior, before proposing fixes
---

# Diagnosis

Find the root cause before fixing. A fix without a known cause is a guess, and guesses create new bugs. Do not stack several changes at once.

## Obvious failures: fix directly

A typo, a missing import, a clear stack trace pointing at the faulty line, a failing assertion whose cause is plain on reading the code. Fix it, re-run the focused test, move on. No ceremony.

## Everything else

1. **Reproduce.** Get a reliable, minimal trigger: a command, a test, an input. Read the full error and stack trace, not just the first line. If it cannot be reproduced, gather more data (logs, inputs, environment); do not guess.
2. **Check what changed.** Recent diffs, new dependencies, config and environment differences often point straight at the cause.
3. **Trace the evidence.** In multi-component systems, log or inspect what enters and leaves each boundary to find which component breaks. For bad values, trace backward through the call chain to where the bad value originates and fix it there, not at the symptom. Compare with similar working code and list every difference.
4. **Hypothesize.** State the single most likely root cause in one sentence: "X is wrong because Y". Base it on evidence, not hunch.
5. **Probe.** Test the hypothesis with the smallest possible change or observation: one print, one assertion, one input. Change one thing at a time. Confirmed: go on. Refuted: form a new hypothesis from what you learned. Do not pile fixes on top.
6. **Fix.** Write a failing regression test that captures the bug (see `superlite:testing`), apply one fix at the root cause, and run the focused tests. Do not bundle refactors or "while I'm here" changes.
7. **Verify.** Confirm the original reproduction now passes and nothing nearby broke (focused run; the full suite goes through `bash <skill-dir>/../swarm/scripts/sl run_tests --full` at the wave or branch end; focused: `--fast PATHS`; read its one-line summary).

## When fixes fail

- **After 2 failed fixes:** stop. Re-read the evidence from scratch. Your model of the problem is wrong somewhere; list what you assumed and check each assumption.
- **After 3 failed fixes:** change the approach. Attended: tell the human what you tried and what you learned, and ask. Unattended: hand it to a fresh agent on a stronger model with the repro, the evidence and the failed attempts. Three failures in different places often signal an architectural problem, not a bug. Record the ruling.

## Red flags: back to step 1

- "Quick fix now, investigate later."
- "Just try changing X and see."
- Proposing a fix before tracing where the bad value comes from.
- Another fix attempt without new evidence.
- "It's probably X" with nothing checked.

## Handing off investigation

Give a subagent the repro, the error text and the hypotheses already ruled out. Ask for the cause and evidence, not a patch. Keep its report short.

## Wrap-up

State the root cause, the fix, the regression test, and the evidence that it passes. If the cause was never found, say so rather than shipping a guess.
