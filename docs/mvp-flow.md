# MVP flow (design)

Status: partly built. `commands/mvp.md` runs the steps, and in human mode the `.mvp.md` plan is approved with `sl orchestrator approve --review <path>.mvp.md`. Not built yet: `skills/mvp-prototype/` and `mvp_check.py`.

## What MVP mode is

MVP = smallest working version of something new, planned on a clickable prototype first. The user assesses and edits the prototype in a browser, approves it, and only then does the normal orchestration flow build it.

## Step order

1. **Orchestrator choice.** Human or AI, asked once with the other questions (`sl orchestrator set`).
2. **Feature plan.** The plan can be as long as it needs to be, but it is organised around features, because the goal of the MVP stage is to agree on the MVP features. `docs/plans/<date>-<topic>.mvp.md` opens with an ASCII diagram of how the product is used, chosen to fit the target kind (see below), so the plan is read as an experience first. Feature ids are drawn on the diagram (for example `[F2]` on the part it adds), so each part maps to a feature. The goal, the target kind (`web`, `cli`, `library`, `api`, `other`), and the feature list follow. The prototype page is built from this diagram. Each feature has an id, a title, what the user gets from it, how it is used, its dependencies, its size, a priority (must, should, could) and an in-or-out recommendation with the reason. It also lists the features considered and left out. Screens, data and architecture appear only as far as a feature needs them.
3. **Prototype.** Generate one self-contained file, `.superlite/mvp/prototype.html` (raw HTML and JS, no build step, no network, fake data), from the skill's page template. No product code is written.
4. **Agree on features.** Human mode: the coordinator shows the path and a five-line summary, then ends its turn. The user opens the page, clicks through, cuts or reorders features, edits labels, leaves notes, and presses **Save feedback**.
5. **Approve.** The user replies "approve" in chat. The reply is what approves; the button only saves the file.
6. **Regenerate the plan.** The coordinator reads `mvp-feedback.json` and rewrites the plan in the agent format (task graph, interfaces, FROZEN, examples, chains, sizes), with cuts under "Out of scope". `sl orchestrator approve` runs, then `lint --stable`.
7. **Build.** The normal flow: Wave 0, tiered dispatch, tests, package reviews, final review.
8. **Discard.** The prototype is deleted at finish, unless the target is `web` and the approved feedback says `keep_as_frontend: true`; then it seeds the frontend task as its starting file.

## The opening diagram, by target kind

The diagram shows how the product is used, not a faux screen. Pick the form that fits:

| Target | Diagram | Shows |
|---|---|---|
| `web`, desktop, mobile | Screen wireframe, plus a line of arrows for the main navigation | Layout, controls, where each feature sits |
| `cli` | Command tree, then one sample session (typed commands and their output) | Commands, flags, exit codes, what the user sees |
| `library` | The public surface as a box per module with its main calls, then a short usage snippet as a call flow | Entry points, types in and out, how calls chain |
| `api` or service | An endpoint table (method, path, purpose), then a request and response sequence between caller, service and store | Routes, payloads, error cases |
| pipeline, bot, agent, other | A data-flow diagram: inputs, steps, outputs, and where the user intervenes | Stages, hand-offs, triggers |

Combined products use more than one (for example a web app with an API gets a wireframe and a sequence). The same diagram drives the mock in the prototype page, so the mock for a `cli` is the typed session and the mock for a `library` is the runnable call flow with sample results.

## How it fits the orchestrator choice

| | Human | AI |
|---|---|---|
| Prototype | Made, then the run stops for the user | Made as the spec and self-checked (it must parse and every feature id must appear in it); no stop |
| Approval | A human reply, as in the existing gate | None (a Ruling is logged) |
| `mvp-feedback.json` | Required before step 6 | Written by the coordinator with `source: "ai"` and no edits |

In human mode the prototype takes the place of the review plan: it is the thing the human approves. The plan summary and the rough time and cost are shown in the page's side panel, so the user does not read a second document. The page is organised around features: the user's main decisions are which features are in, their order and priority, and what each one does. Nothing else is skipped: lint, tests and the final review still run.

## The page

- **Main area:** a clickable mock of the product, with fake data. For `cli` it is a terminal session with typed commands and sample output; for `library` and `api` it is a list of example calls with sample results. The mock is built from the same feature list as the panel.
- **Side panel:** the features and tasks, each with a toggle (in or out), a priority order, a note field, and the estimated size. Cutting a feature removes it from the mock (the mock reads the panel's state). Time and cost estimates update as features are cut.
- **Footer:** Save feedback, and a copy-to-clipboard fallback.
- **Saving.** A browser cannot write into the project on its own. The page uses the File System Access API (`showSaveFilePicker`) where it exists, started in the project folder, and falls back to a download. The coordinator looks for `.superlite/mvp/mvp-feedback.json` first, then the newest `mvp-feedback*.json` in the user's Downloads folder, and copies it in. If neither exists it asks the user to paste the clipboard block. No server is involved.

## `mvp-feedback.json`

```json
{
  "schema": 1,
  "source": "human",
  "plan": "docs/plans/2026-10-18-notes-app.mvp.md",
  "prototype_sha": "<hash of prototype.html as generated>",
  "approved": true,
  "target": "web",
  "keep_as_frontend": false,
  "features": [
    {"id": "F1", "title": "Create a note", "in": true,  "order": 1, "note": ""},
    {"id": "F2", "title": "Tags",          "in": false, "order": 2, "note": "later"}
  ],
  "screens": [{"id": "S1", "title": "Note list", "edits": [{"label": "Add", "to": "New note"}]}],
  "added": [{"title": "Search by title", "note": "needed for MVP"}],
  "notes": "free text"
}
```

`approved` is the user's wish; it does not replace the chat reply. `prototype_sha` lets the coordinator detect a file saved from an outdated page.

## How the plan is regenerated

- Features with `in: false` become "Out of scope", listed in the final report.
- Features with `in: true` become tasks, in `order`; each feature id is kept as the task's trace id. `added` entries become new features and are marked "unreviewed" for the final review.
- Screens and flows become FROZEN interfaces (route or command names, public function names, data shapes visible in the mock). Fake data in the mock becomes EXAMPLE blocks, and each click path becomes a CHAIN.
- The prototype is a spec for behavior, not code: for non-web targets it is never copied into the project.
- If the regenerated plan fails `lint --stable`, the fix is made to the plan, not to the feedback.

## Layout (to build)

```
skills/mvp-prototype/
  SKILL.md              how to make the prototype and read the feedback
  page-template.html    the page shell: panel, mock slot, footer, save logic
  feedback-schema.md    the JSON above, with rules
  mock-patterns.md      mock styles for web, cli, library, api
commands/mvp.md         already updated
scripts/ (sl tool)      mvp_check.py: validates prototype.html and feedback.json
```

`mvp_check.py` is language-agnostic: it only reads the page and the JSON. Checks: every feature id from the plan is in the page, the page has no external URLs, the feedback `prototype_sha` matches, and the schema is valid.

## What the hook must allow

`hooks/block-coordinator-edits` already allows `.superlite/**` (except the two protected files) and `docs/plans/*.review.md` while a human run is unapproved. So:

- `.superlite/mvp/prototype.html` and `.superlite/mvp/mvp-feedback.json`: already allowed; no change.
- `docs/plans/*.mvp.md` (the MVP plan): already allowed. Before approval the hook blocks orchestration only, never file writes.
- Opening the page needs no tool (the user does it). Any open command from the coordinator is a Bash call that does not write a file, so it is allowed.
- Still blocked until approval: code writes, Agent dispatch of `superlite:*` and `general-purpose`, and `sl orchestrator approve` without an approving human reply newer than the file.
- **Approval:** `sl orchestrator approve --review docs/plans/<date>-<topic>.mvp.md`, with the usual rule (an approving human reply newer than that file). No change to `orchestrator.py` is needed.
- The final write of the agent-format plan happens after approval, when `docs/plans/**` is already allowed.

## Open points

- Whether a user who wants the written review plan as well can ask for both (suggest: `/superlite:mvp --review` adds it).
- A `web` target with a framework: whether the kept prototype should be converted by a task (suggest yes, as the first producer task).
