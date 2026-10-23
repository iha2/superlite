---
name: writing-specs
description: Use when agent_specs is on (or the user asks for a spec) to write what done looks like and why before a plan or implementation.
---

# Writing specs

A **spec** defines what “done” looks like and **why**. A **plan** chooses **how** to get there. One spec can outlive many plans: rewrite or replace the plan without rewriting success criteria. The spec is durable authority; if a plan drifts, fix the plan or report `DONE_WITH_CONCERNS`—never silently override the spec.

## When

- Check `bash "$SL" agent_specs status`. If **off**, skip unless the user asked for a spec.
- **Feature flow (default):** write one **feature-sized** spec before implementers code (and before or beside the `.feature.md` / machine plan as the success criteria). Optional task-local notes under `.superlite/specs/<plan-topic>/` refine *how* for a long-running task; they must stay consistent with the feature spec.
- No human gate on task-local notes. Feature-level specs may be opened for the user when `open_in_harness` is on.

## Paths

- Feature (or sub-feature) spec: `docs/plans/YYYY-MM-DD-<topic>.spec.md` (or the path the user names).
- Task-local note (optional): `.superlite/specs/<plan-topic>/T<N>-<slug>.md`.

## Shape

Use `templates/feature-spec.md`. Prefer diagrams and tables; keep prose outside fences and tables ≤ 150 words for task-local notes. Feature specs may be longer but stay dense: intent/why, goals and non-goals, constraints, what “done” looks like (acceptance), decided vs left to the builder. Mermaid first for structure, flow, or state.

## Open in harness

When status shows `open_in_harness: on`, after writing the file, open it for the user (Cursor: `open_resource`; else path + five-line summary in chat). `sl agent_specs open off` turns that off without disabling specs.

## Style (flows §9)

| Rule | Instead of |
|---|---|
| Diagram first for structure, flow, or state | Paragraphs of boxes and arrows |
| Tables for contracts, options, edge cases | Long bullet prose |
| Precise technical register | Vague paraphrase |
| Every sentence a fact, constraint, or decision | Filler and restatement |
