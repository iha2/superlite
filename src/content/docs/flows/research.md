---
title: "Research flow"
description: "Answer a topic with parallel researchers, a sourced ledger and a report at your level."
sidebar:
  order: 8
---

```text
 RESEARCH
 ------------------------------------------------------------------------------
  [1 ASK]
      v
  [2 PLAN]
      v
  [3 QUESTIONS]
      v
  [4 APPROVE]
      v
  [5 RESEARCH] <------------------+
      v                           |
  [Agent A] [Agent B] [Agent C]   | gaps found
  (one sub-question each, as many as needed)
      v                           |
  [6 CONTRADICTIONS] -------------+
      v
  [7 LINT]  every claim sourced, every conflict has a cause
      v
  [8 WRITE]  report at your level: Director | VP | Researcher | Simple
      v
  [9 DOWNLOAD]  optional, only on your yes
 ------------------------------------------------------------------------------
  1  ASK            You give the topic and pick the language level.
  2  PLAN           Scope, sub-questions, search strategy, evidence tiers.
  3  QUESTIONS      Critical questions asked of you (or the agent) up front.
  4  APPROVE        Human mode: you approve the plan. AI mode: skipped.
  5  RESEARCH       Agents side by side write sources and claims to a
                    ledger, never to the report.
  6  CONTRADICTIONS Conflicts found, cause recorded, resolved or left
                    open. Gaps go back to 5.
  7  LINT           Checks citations, causes, and that all levels match.
  8  WRITE          Report, sources and contradictions as markdown. Same
                    facts and depth at every level, only wording changes.
  9  DOWNLOAD       Lists name, source and size. Fetches only after a yes.
 ------------------------------------------------------------------------------
```

## What you do

Give the topic and pick a language level: Director, VP, Researcher or Simple. Answer the critical questions. In human mode, approve the plan.

To change level later, run `/superlite:research --level vp`, or another level. Nothing is researched again.

## What superlite does

superlite plans the scope, sub-questions, search strategy and evidence tiers. Researcher agents take one sub-question each. They write sources and claims to a ledger, never to the report.

Conflicts are recorded with a cause and resolved or left open. Gaps go back to research. Lint checks citations, causes and that all levels match.

It writes the report, sources and contradictions as markdown. Levels differ in wording only. Downloads happen only after your yes.

## Example

```text
/superlite:research Research the state of the art in vector search, for a VP
```

You get a report at VP level. The same claims and citations appear at every level. See [all commands](/superlite/start/commands/).
