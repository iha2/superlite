---
title: "MVP flow"
description: "Plan the smallest working version on a clickable prototype, then build it."
sidebar:
  order: 3
---

```text
 MVP
 ------------------------------------------------------------------------------
  [1 ASK]
      v
  [2 FEATURES]
      v
.- SANDBOX: one HTML file, fake data -------------------.
: [3 PROTOTYPE]                                         :
:     v                                                 :
: [4 FEEDBACK]                                          :
'-------------------------------------------------------'
      v
  [5 APPROVE]
      v
  [6 PLAN]
      v
  [7 BUILD]
      |
      +-----------+-----------+
      v           v           v
  [Haiku]     [Sonnet]    [Sonnet]
  3 tasks     3 tasks     2 tasks
      |           |           |
      +-----------+-----------+
      v
  [8 REVIEW + FIX]
      v
  [9 REPORT]
 ------------------------------------------------------------------------------
  1  ASK        You say what you want.
  2  FEATURES   The MVP plan: every feature, each marked in or out.
  3  PROTOTYPE  One clickable HTML file in a sandbox, with fake data. No
                product code.
  4  FEEDBACK   You open it, cut features, press Save feedback.
  5  APPROVE    Human mode: you approve. AI mode: it checks and logs a ruling.
  6  PLAN       The task plan from the features you kept. Lint gates it.
  7  BUILD      Agents run side by side, sized to the load. Cut features
                stay out.
  8  REVIEW     Package reviews, one final review, fixes by tier, tests.
  9  REPORT     Time and cost, and the list of what was left out.
 ------------------------------------------------------------------------------
```

## What you do

Say what you want to build. superlite lists the features, each marked in or out. It then makes a clickable HTML prototype with fake data.

Open the prototype in your browser. Cut features, reorder them, add notes, then press Save feedback. In human mode, reply "approve" in chat. The reply approves; the button only saves the file.

## What superlite does

superlite writes one self-contained HTML file in a sandbox. It holds no product code and needs no build step or network. The build runs in a [session](/superlite/sessions/sessions/) branch.

After approval it writes the task plan from the features you kept. Cut features go under "Out of scope". Agents build in parallel, then package reviews, one final review, fixes by tier and tests follow.

In AI mode it checks the prototype itself and logs a ruling. The report lists what was left out.

## Example

```text
/superlite:mvp Build me a static site generator from this spec
```

You get the prototype to open, cut what you do not need, and approve. The build covers only the features you kept.
