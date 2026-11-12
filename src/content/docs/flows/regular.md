---
title: "Regular flow"
description: "Size the request, plan it, build it in parallel, review it and fix it."
sidebar:
  order: 2
---

```text
 REGULAR
 ------------------------------------------------------------------------------
  [1 ASK]
      v
  [2 SIZE]
      v
  [3 PLAN]
      v
  [4 APPROVE]
      v
  [5 GATE]
      v
  [6 BUILD]
      |
      +-----------+-----------+
      v           v           v
  [Haiku]     [Sonnet]    [Sonnet]
  3 tasks     3 tasks     2 tasks
      |           |           |
      +-----------+-----------+
      v
  [7 REVIEW + TEST]
      v
  [8 FINAL REVIEW]
      v
  [9 FIX]
      v
  [10 REPORT]
 ------------------------------------------------------------------------------
  1  ASK      You say what you want.
  2  SIZE     Trivial, bounded or large. Large gets one batch of questions.
  3  PLAN     The design and the task plan. Human mode adds a review plan.
  4  APPROVE  Human mode: you approve before anything runs. AI mode: skipped.
  5  GATE     Lint checks the plan. Only stable tasks go out.
  6  BUILD    Agents run side by side, sized to the load. Haiku: mechanical.
              Sonnet: the rest.
  7  REVIEW   Reviewers fix their own findings. Tests run in the background.
  8  FINAL    One fresh reviewer reads the packet and tiers the findings.
  9  FIX      Fixers by tier (Sonnet, Sonnet high, Opus), then tests again.
  10 REPORT   Time and cost, projected vs actual. Nothing is committed.
 ------------------------------------------------------------------------------
```

## What you do

Say what you want. If the request is large, answer one batch of questions. In human mode, read the review plan and approve it before anything runs.

## What superlite does

superlite sizes the request: trivial, bounded or large. It writes the design and the task plan, and lint checks the plan. Agents then build side by side: Haiku for mechanical work, Sonnet for the rest.

Reviewers fix their own findings while tests run in the background. One fresh reviewer reads the packet and ranks the findings by tier. Fixers by tier (Sonnet, Sonnet high, Opus) follow, then tests run again.

The report shows time and cost, projected against actual. Nothing is committed.

## Example

```text
/superlite:start regular Build a REST API for a bookshelf, with tests
```

You pick human or AI orchestration. In human mode you approve the review plan with a reply like "approve", and the build starts.

The run works on its own [session](/superlite/sessions/sessions/) branch. Nothing is committed. See [all commands](/superlite/start/commands/).
