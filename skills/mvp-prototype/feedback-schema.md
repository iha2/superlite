# `mvp-feedback.json`

Written by the page's Save feedback (human) or by `sl mvp_check ai` (AI orchestrator). Read from
`$(sl session path)/mvp/mvp-feedback.json` (legacy flat: `.superlite/mvp/mvp-feedback.json`); `sl mvp_check fetch`
copies it in from Downloads when the browser saved it there.

```json
{
  "schema": 1,
  "source": "human",
  "plan": "docs/plans/2026-10-18-notes-app.mvp.md",
  "prototype_sha": "<sha256 of prototype.html with the sl-prototype-sha meta emptied>",
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

Rules `sl mvp_check check` enforces:

| Field | Rule |
|---|---|
| `schema` | `1` |
| `source` | `human` or `ai`; `ai` has no `edits` and no `added` |
| `plan` | the `.mvp.md` path the check was given |
| `prototype_sha` | equals the current page's sha; a mismatch means it was saved from an older page |
| `approved`, `keep_as_frontend` | booleans; `keep_as_frontend: true` only when `target` includes `web` |
| `target` | one or more of `web`, `cli`, `library`, `api`, `other` (`web, api`) |
| `features` | exactly the plan's feature ids, each once, with `title`, `in` (bool), `order` (1..n, each once), `note` |
| `screens` | optional; each has `id` and `edits` of `{label, to}` |
| `added` | optional; each has a non-empty `title` |
| `notes` | optional text |

`approved` records the user's wish; only the approving chat reply approves. `title` may differ from the plan: the user renamed
the feature, and the agent-format plan uses the new name.
