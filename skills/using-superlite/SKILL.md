---
name: using-superlite
description: Use when starting any conversation - explains superlite, how to size a request, and which skill fits which task
---

Superlite: parallel work, short reports.

**Start menu.** First message, no mode named, not trivial: `sl suggest_flow`, then `/superlite:start`.

**Size the request first.**
- Trivial: just do it (human orchestrator: after a yes in chat).
- Bounded (change to an existing flow): short design in chat, build.
- Architectural: batched questions (one AskUserQuestion, up to 4), one combined design+plan doc.

**Who orchestrates?** Ask once, in that same batch: human orchestrator (approves the plan before it runs) or AI orchestrator (hands off). Skip when the request settles it ("decide yourself", away, headless): AI, as a Ruling. `sl orchestrator set human|ai`. AI stops only for destructive, outward or security-sensitive actions.

**Skill map**
- build -> superlite:inventing
- plan -> superlite:drafting
- execute -> superlite:swarm
- bug -> superlite:diagnosis
- tests first -> superlite:testing
- claim done -> superlite:proof
- finish -> superlite:shipping

**Builds with 2+ tasks always run through `superlite:swarm`;** load it before writing the plan. No `general-purpose` agents for build or review. Build inline only when small (a few files, one chain); say so.

**Final review always applies.** A non-trivial build ends with a fresh `superlite:final-reviewer` on the review packet before claiming done.

**Tools.** `bash <skill-dir>/../swarm/scripts/sl <tool>`, never `python`. Incidents go to `.superlite/incidents.jsonl`; Graphify only if `sl graphify_gate` allows.

**Platform adaptation.** Off Claude Code, read the matching `references/<harness>-tools.md` (antigravity, codex, cursor, gemini, hermes, muse, pi); it maps tool names and says how to run `agents/*.md`.
