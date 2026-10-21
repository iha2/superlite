---
description: Plan the smallest working version, assess it on a clickable HTML prototype, then build it with the orchestration flow.
argument-hint: "[what to build]"
---

Superlite MVP mode. Request: $ARGUMENTS

Design: `docs/mvp-flow.md`. If the request is empty, ask for it in one line. Then:

1. Ask the orchestrator choice (human or AI) unless the user already said; run `sl orchestrator set human|ai`.
2. Write the MVP plan, `docs/plans/<date>-<topic>.mvp.md`. It can be extensive but is organised around features, because the goal is to agree on the MVP features. Open it with an ASCII diagram of how the product is used, in the form that fits the target, never a faux screen for a non-UI product: web, a screen wireframe plus main navigation; cli, a command tree plus one sample session; library, a box per module with its main calls plus a usage call flow; api or service, an endpoint table plus a request and response sequence; pipeline, bot or other, a data-flow diagram. Combine forms when the product needs it. Draw feature ids on the parts they add, so the plan reads as an experience first. Then the goal, the target kind (web, cli, library, api, other), and for each feature: id, title, what the user gets, how it is used, dependencies, size, priority (must/should/could) and an in-or-out recommendation with the reason. List the features considered and left out. Show screens, data and architecture only as far as a feature needs them. Load `superlite:brainstorming` only for what is unclear.
3. Generate ONE self-contained file, `.superlite/mvp/prototype.html` (raw HTML and JS, no build step, no network, fake data): a clickable mock of the product with a side panel listing features and tasks. Cutting a feature in the panel removes it from the mock. For cli, library and api targets, mock the usage (a terminal session or example calls with sample output). Write no product code in this step.
4. Human orchestrator: show the path and a five-line summary, ask the user to open the page, edit, press Save feedback (`mvp-feedback.json`), and reply approve. End your turn. Nothing is built or dispatched until they approve. AI orchestrator: self-check the page (it parses, every feature id appears), write the feedback with `source: ai`, and continue.
5. Human orchestrator: after the approving reply, run `sl orchestrator approve --review docs/plans/<date>-<topic>.mvp.md` first (the MVP plan and the prototype are the review plan). Then read `.superlite/mvp/mvp-feedback.json` and write the agent-format plan as `docs/plans/<date>-<topic>.md` with the header `**Review plan:** docs/plans/<date>-<topic>.mvp.md`: cut features go to "Out of scope", kept features become tasks in the user's order, screens and flows become FROZEN interfaces, fake data becomes EXAMPLE blocks, click paths become CHAINs. Then `lint --stable` and `superlite:subagent-driven-development`. AI orchestrator: no `approve`.
6. Discard the prototype at finish, unless the target is web and the feedback says `keep_as_frontend`.
7. Every gate still applies (lint, tests, final review). MVP changes the scope, not the checks. List what was left out in the final report.
