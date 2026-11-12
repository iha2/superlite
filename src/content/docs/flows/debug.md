---
title: "Debug flow"
description: "Find the root cause first, then fix it with a regression test."
sidebar:
  order: 5
---

```text
 DEBUG
 ------------------------------------------------------------------------------
  [1 BUG]
      v
  [2 REPRODUCE]
      v
  [3 ROOT CAUSE]
      v
  [4 FAILING TEST]
      v
  [5 FIX]
      v
  [6 VERIFY]
      v
  [7 REPORT]
 ------------------------------------------------------------------------------
  1  BUG         You describe what fails and how to see it.
  2  REPRODUCE   Run it and watch it fail.
  3  ROOT CAUSE  Find the cause before touching code.
  4  TEST        A regression test that fails for that cause.
  5  FIX         By how well the cause is understood:
                 tier 1 known, one module: Sonnet
                 tier 2 spans modules or a seam: Sonnet high
                 tier 3 cause unknown: Opus
  6  VERIFY      The new test passes and the whole suite is green.
  7  REPORT      The cause, the fix and the test. No widening of scope.
 ------------------------------------------------------------------------------
```

## What you do

Describe what fails and how to see it. If you give nothing, superlite asks in one line.

## What superlite does

superlite runs the failure and watches it fail. It finds the cause before it touches code, then writes a test that fails for that cause.

Fixes go to agents by tier: Sonnet when the cause is known in one module, Sonnet high when it spans modules, Opus when the cause is unknown. It then checks that the new test passes and the whole suite is green.

After two failed fixes it stops and re-reads the evidence. The report gives the cause, the fix and the test. It does not widen the scope.

## Example

```text
/superlite:debug This test fails on leap days: find the root cause and fix it
```

You get the cause, the fix and the regression test. The work happens in a [session](/superlite/sessions/sessions/); see [all commands](/superlite/start/commands/).
