---
name: inventing
description: Use when the user asks to build a feature or component, add functionality, or change behavior, before any implementation code is written.
---

# Inventing

Turn a request into an approved design with as little ceremony as the request allows. Intent comes first: know what the user wants to accomplish and what success looks like before you pick an approach. Who orchestrates is chosen first: with a human orchestrator the human approves the plan once, before it runs; with an AI orchestrator nothing is asked. After that, nothing stops you except destructive, outward-facing (push, merge, publish) or security-sensitive actions.

## 1. Size the request, in one line

Announce it: "This looks bounded, so I'll ask one batch of questions and show a short design here." The user can override.

- **Trivial** (a few tool calls: a typo, a rename, a one-line fix): do it. Exit this skill. With a human orchestrator, say the change in one line and wait for the human's yes in chat; only an orchestrated build needs a review plan.
- **Bounded** (a change to a flow that already exists in the repo): short in-chat design, one approval.
- **Architectural** (a new project or subsystem, or changes to interfaces others depend on): one combined design and plan doc, one approval.
- **Spike** ("can we...", "is it possible...", throwaway is fine): say what you will try in 2 sentences and try it. The output is an answer, not code you keep.

Hidden complexity found mid-task upgrades the size. Say so and step up. If the request describes several independent subsystems, say that first and brainstorm only the first one.

## 2. Explore and ask, once

Read the relevant code and recent commits first. Use an Explore agent (it runs on Haiku) for broad sweeps. Do not ask what the repo or the request already answers.

If purpose, constraints or success criteria are still missing, ask them in ONE `AskUserQuestion` call: up to 4 questions, multiple choice where possible, your recommended option first. For architectural work, include the plan `detail` choice in the same batch: `contracts`, `tests` or `full`. Recommend `full` when the core is algorithmic or risky, otherwise `tests` (the default). Add the orchestrator choice to that same call as one single-choice question, options `Human orchestrator: I approve the plan before it runs` and `AI orchestrator: hands off, it does everything` (details in `../swarm/orchestrator.md`); never ask it when the request settles it, and in a headless run default to AI with a `Ruling:`. Then record it: `bash <skill-dir>/../swarm/scripts/sl orchestrator set human|ai`. Never one question per message. Bounded work gets at most one batch. If nothing is missing, ask nothing and restate your understanding in the design instead.

## 3. Design

Write what you understood (separate what the user said from your assumptions), then the design. Cover architecture, components, data flow, error handling and testing. Keep it as short as the size allows.

- **YAGNI.** Cut every feature the user did not ask for or the stated success criteria do not need.
- **Clear units.** Each unit has one purpose, a defined interface, and can be tested alone. For each, you can say what it does, how it is used and what it depends on. If someone must read the internals to use it, fix the boundary.
- **Fit the codebase.** Follow existing patterns. Include a targeted fix only where existing code blocks the work. No unrelated refactors.
- Name the approach you chose and, when a real alternative exists, why you rejected it in one sentence. Do not present three approaches by default.

## 4. Approve once, then build

**Bounded:** present the design in chat and stop until the user says yes. Then implement with `superlite:testing`. No spec file, no plan doc.

**Architectural:**

1. With an AI orchestrator write ONE doc, `docs/plans/YYYY-MM-DD-<topic>.md`: the design above, then the plan. With a human orchestrator write the review plan instead (`<date>-<topic>.review.md`, template `../drafting/review-plan.md`); the machine plan comes after the yes. Invoke `superlite:drafting` for the plan half and append it to the same file. If the user named a different location, use theirs.
2. Self-review inline: placeholders, contradictions, scope (one plan, or decompose?), and requirements that could be read two ways. Fix them in place. No review loop, no subagent.
3. With a human orchestrator ask in plain text, not AskUserQuestion (the human's typed reply is what the hook verifies): the review plan's path, a five-line summary and "Reply approve to start, or tell me what to change"; END THE TURN and do nothing until the human replies. Only then run `sl orchestrator approve --review <path>` and write the machine plan from it. The human approves the design in the review plan, never the machine plan: that one is task-graph format for the orchestrator, is not shown for approval, and must not add or change anything the human approved. With an AI orchestrator skip the approval. Run `bash <skill-dir>/../swarm/scripts/sl graphify_gate`; when it reports `needs_install: true`, add the Graphify install opt-in (`uv tool install graphifyy`) to that same question. Never install without a yes.
4. On approval, invoke `superlite:swarm` once, in this session: this session is the one coordinator. It runs `lint --stable`, dispatches the stable tasks at once and patches only the FIX tasks.

Until approval, only read-only exploration is allowed. No scaffolding, no dependency installs, no product code. Approval covers the doc as presented. If the user changes the design, revise and re-present once.

## AI orchestrator (unattended)

If the user says they are away, or "decide yourself", or the prompt pre-approves design and plan, or the orchestrator is AI, the gate is already passed. Skip the questions and the approval. Choose the sensible default for each open question, including the plan `detail` (`full` for algorithmic or risky cores, otherwise `tests`), and record it in the doc, or in chat for bounded work, as a line:

`Ruling: <decision> (because <reason>)`

Install Graphify only if `SUPERLITE_ALLOW_INSTALL=graphify` is set. Then proceed straight to implementation (bounded) or to `superlite:swarm` (architectural). The closing message lists all rulings. Destructive, outward-facing and security-sensitive actions are deferred, not waited on: take the safe path, record it, and keep going.

## Red flags

| Thought | Reality |
|---|---|
| "Too simple to need a design" | Trivial is exempt. Bounded gets a short design and a yes. |
| "Let me ask one thing first" (again) | Batch everything into one `AskUserQuestion`. |
| "I'll start while they read the design" | The approval is the gate. Present, then stop. |
| "I know this kind of app, so it's bounded" | Bounded means the flow exists in this repo. A new project is architectural. |
| "Let me offer three approaches" | Recommend one. Mention an alternative only if it is real. |
| "They are away, so I will ask anyway" | Unattended means rule and record, not wait. |
