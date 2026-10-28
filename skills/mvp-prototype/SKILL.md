---
name: mvp-prototype
description: Use in MVP mode (/superlite:mvp), after the .mvp.md feature plan is written, to make the one-file clickable prototype, check it, and read the user's feedback before the agent-format plan is written.
---

# MVP prototype

One self-contained page under the active session (`$(sl session path)/mvp/prototype.html`, or legacy
`.superlite/mvp/prototype.html` when no session): raw HTML and JS, no build step, no network, fake data. It is a spec
for behavior, not product code. Design: `docs/mvp-flow.md`. Tools: `sl mvp_check` (defaults follow the session).

## 1. The plan the page is built from

`docs/plans/<date>-<topic>.mvp.md` opens with the usage diagram, then `**Target:**` (`web`, `cli`, `library`, `api`, `other`;
combined as `web, api`), optionally `**Estimate:** 30-45 min, $4-7`, then one heading per feature, which `mvp_check` parses:

```markdown
### F1: Create a note
- **Gets:** a note saved in one step
- **Use:** type, press Enter
- **Depends:** none
- **Size:** S
- **Priority:** must
- **Recommend:** in - the core of the product
```

Features considered and left out go under `## Left out` as plain bullets, with no `F` id.

## 2. Write the mock, not the page

Write only the main area as an HTML fragment at `$(sl session path)/mvp/mock.html` (legacy: `.superlite/mvp/mock.html`),
in the style for the target from [mock-patterns.md](mock-patterns.md). Then:

```bash
MVP=$(bash <skill-dir>/../swarm/scripts/sl session path)/mvp
bash <skill-dir>/../swarm/scripts/sl mvp_check build --plan docs/plans/<date>-<topic>.mvp.md --mock "$MVP/mock.html"
```

`build` fills [page-template.html](page-template.html) (side panel, footer, save logic) with the plan's features and your mock,
stamps the page's sha, and checks it; it does not open it. Do not copy or rewrite the template by hand.

**Show the page** to the user; how is your choice, by what your harness offers: an in-IDE view (in the Claude desktop
app, send the page as a file with display render), a browser tool you can drive, or `sl mvp_check open` for the default
browser. In a headless run, skip it. If the view cannot save files, Save feedback falls back to a download, and Copy
feedback to the clipboard.

Mock rules:

- Every element a feature adds carries `data-feature="F2"` (several ids: `data-feature="F2 F4"`, hidden if any is cut). Every
  feature appears at least once. Cutting it in the panel hides those elements.
- `data-screen="S1" data-title="Note list"` marks a screen (web: one shown at a time), `data-go="S2"` navigates on click,
  `data-edit` marks a label the user may retype (never on the same element as `data-go`). `data-sl-order` on a container
  reorders its `data-feature` children with the panel's order.
- Fake data inline. No `http://` or `https://` anywhere (write `api.example/notes` or `/notes`), no `fetch`, no CDN, no fonts.

## 3. Check

`sl mvp_check check --plan <mvp.md>` must print `mvp_check: ok`. It checks: the page parses and its structural tags close,
the panel's data matches the plan, every feature id is in the mock, no external URLs or network calls, and (when the feedback
file exists) the feedback schema, its features against the plan, and its `prototype_sha` against the page. Fix the mock or the
plan, then `build` again. After any hand edit of the page run `sl mvp_check stamp`.

## 4. Human orchestrator

Show the path, a five-line summary (goal, target, features in, features out, rough time and cost) and: open the page, cut or
reorder features, edit labels, leave notes, press Save feedback, then reply in chat. END THE TURN. The chat reply
approves, never the file. If you understand that reply as approval, send approve: `sl orchestrator approve --review docs/plans/<date>-<topic>.mvp.md`, then
`sl mvp_check fetch` (the session feedback file, else the newest `mvp-feedback*.json` in Downloads, copied in; if none, ask the user to
paste the Copy feedback block and save it yourself to `$(sl session path)/mvp/mvp-feedback.json`), then
`sl mvp_check check --plan <mvp.md> --require-feedback`. A sha mismatch means the file came from an older page: ask once for a
fresh save. From here on the human is not asked again: a scope change is a `Ruling: [scope] ...` line, listed by `sl finish` under
"Changed from the approved plan".

## 5. AI orchestrator (self-check, no stop)

`sl mvp_check check --plan <mvp.md>` until ok, then `sl mvp_check ai --plan <mvp.md>` writes the feedback with `source: "ai"`,
every feature as the plan recommends, and no edits. Log `Ruling: MVP scope taken as the plan recommends - AI orchestrator -
cost if wrong: a feature rebuilt later`. No `approve`.

## 6. Feedback into the agent-format plan

Schema and rules: [feedback-schema.md](feedback-schema.md). `in: false` features go to "Out of scope"; kept features become tasks
in `order`, each keeping its `F` id as the trace id; `added` entries become features marked "unreviewed"; label `edits` change
the FROZEN names; screens and click paths become FROZEN interfaces and CHAINs; fake data becomes EXAMPLE blocks. Write
`docs/plans/<date>-<topic>.md` with `**Review plan:** docs/plans/<date>-<topic>.mvp.md`, then `lint --stable`. If lint fails,
fix the plan, never the feedback.

## 7. At finish

`sl finish` deletes the session tree (including `mvp/`) unless the target includes `web` and the feedback says
`keep_as_frontend: true`; then copy the page out before finish — it is the first producer task's starting file, converted to
the plan's framework by that task.
