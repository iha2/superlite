# Who orchestrates: human orchestrator or AI orchestrator

When someone asks superlite to create something, it first settles who orchestrates (D40).

- **Human orchestrator:** the human validates the plan before it is linted and sent off, in two phases. Phase 1: the review plan
  (`docs/plans/<date>-<topic>.review.md`, plain words with diagrams; template in `../drafting/review-plan.md`) is shown with its
  path and a five-line summary. The same message offers the read-only agent map, so approval stays the one stop: "Reply
  approve. Add `map` to watch the run in the agent map pane (`/superlite:agents`: agents, tasks and the ledger)." Phase 2,
  after the yes: `sl orchestrator approve --review <path>`, open the map if the reply asked for it (no terminal tail: the map
  shows the ledger; `/superlite:tail` only where the mod is not installed), then the coordinator writes the machine plan from
  it (header `**Review plan:** <path>`), `schedule --insert`, `lint --stable`, dispatch.
- **AI orchestrator:** does everything itself (plan, lint, dispatch, rulings) and never stops to ask, except for the
  destructive, outward-facing or security-sensitive actions in the SDD "Stop conditions". It asks nothing about views: at the
  first dispatch it prints one line naming `/superlite:agents`.

## The question (inventing, once)
One single-choice question, in the same `AskUserQuestion` call as inventing's other open questions (up to 4 per call):

- `Human orchestrator: I approve the plan before it runs`
- `AI orchestrator: hands off, it does everything`

Do NOT ask when the request already settles it: "decide yourself", "I am away", pre-approved design and plan, or any other
unattended signal (all mean AI). When no human can answer (a headless `claude -p` run), default to AI and record it:
`sl orchestrator set ai --by coordinator --ruling "orchestrator defaulted to AI - headless run, no human can answer - cost if wrong: none"`.

## The tool (`sl orchestrator`)
- `set human|ai [--by NAME] [--ruling TEXT]` writes `.superlite/orchestrator.json` `{mode, approved, by, time}`
  (`approved` is true for AI, false for human until `approve`).
- `approve --review PATH [--by NAME]` records the approval. Human mode only (exit 2 otherwise); `--review` is required (exit 2: write the
  review plan first; a missing file also exit 2); and a real human reply is required: `.superlite/human-replies.jsonl` (written only by the
  UserPromptSubmit hook, `{t, approves, text}` per human prompt while unapproved) must hold an `approves: true` line later than the review
  file's modification time, else exit 3 `BLOCKED: no human approval reply since the review plan was written; show the plan, ask the human to
  reply approve, end your turn`. The path and sha256 go to `orchestrator.json` (`review_path`, `review_sha256`). AI mode never needs `approve`.
  `status [--review PATH]` and `finish` print the review plan path and whether an approving reply exists.
- `status` prints one line: `orchestrator: human, awaiting approval` / `human, approved <time> by <name>` / `AI` / `not set (ungated)`.
- Gate: in human mode without an approval `plan_tools lint --stable`, `dispatch`, `wave0` and `build_prime` exit 4 with
  `BLOCKED: human orchestrator selected, show the plan, ask for approval, then run sl orchestrator approve`.
  `schedule --insert`, `prune` and plain `lint` are never gated. With no `orchestrator.json` nothing is gated, so existing
  runs behave as before. `finish` prints the mode line (with the approval time in human mode).
- The gate stops the tools, not the model: the coordinator could still call `approve` itself. Only a human answer to the
  approval question may precede `approve`.

## The hard rule (human mode)
No orchestrated build starts until the human approves the plan: nothing is linted, dispatched or sent to an agent before the human's
approving reply. The approval is asked in PLAIN TEXT, never with AskUserQuestion, because the typed reply is what the hook verifies: show
the review plan path, a five-line summary and "Reply approve to start, or tell me what to change", then end the turn. Only after the reply
run `approve --review`. `set human` writes `approved: false`. The hook (`block-coordinator-edits`) denies superlite agent dispatch and the
build tools while the mode is human and unapproved, and always denies the model writing `orchestrator.json` or `human-replies.jsonl`.

In MVP mode the MVP plan (`docs/plans/<date>-<topic>.mvp.md`) and its prototype are the review plan: `approve --review` takes the
`.mvp.md` path.

In feature mode the feature plan (`docs/plans/<date>-<topic>.feature.md`) is the review plan. The try-out of the prototype is the one
exception to "not asked again after approval": a second planned gate, feature mode only, because trying the prototype is the point of the
flow. Ask it in plain text ("reply it works, or list the changes"), end the turn, then `sl proto tryout --approve`, which needs an
approving reply in `human-replies.jsonl` newer than the prototype's last checkpoint (exit 3 otherwise). While `.superlite/feature.json`
says `tryout`, the hook denies integration dispatch and `lint --stable`, `dispatch`, `wave0`, `build_prime` exit 4, as before plan
approval. AI mode: the acceptance checks decide, as a `Ruling: [tryout]`.

Small inline work (trivial or bounded, no plan) is not orchestrated: it needs only the human's yes in chat, and no review plan.

After the approval the run decides on its own, like the AI orchestrator. The human is not asked about scope or interface changes: each
one is recorded as `Ruling: [scope] <decision> - <why> - <cost if wrong>`, and `sl finish` lists them under "Changed from the approved
plan". Destructive, outward and security-sensitive actions still follow the SDD "Stop conditions".

## Benchmarks
Every prompt file under `bench/` (`prompt*.md`, `tasks/*/prompt*.md`) ends with `Orchestrator: AI, do not ask me to choose or to approve.`
(a test checks this), so the decider is never asked. To test the human gate, run the session in human mode:
`sl orchestrator set human` before the run (or ask for it in the prompt), expect `BLOCKED` from `lint --stable` until
`sl orchestrator approve`, and expect a headless run to end on the approval question.

## Proposed, not built: a hook (owned by the hooks session)
A PreToolUse hook on `AskUserQuestion` that, when `.superlite/orchestrator.json` says `ai`, denies the call with
`AI orchestrator: decide yourself, record a Ruling` unless the question's text names a destructive, outward-facing
(push, merge, publish) or security-sensitive action. It should allow the call when the file is absent or says `human`,
log each denial as an incident `kind: rule-block`, and also deny a Bash call of `sl orchestrator approve` unless an
`AskUserQuestion` answer was received earlier in the session.

## Waiting on tests
Never wait on tests through a loop or a command that hides output (`until npx vitest run x.test.ts >/dev/null 2>&1; do sleep 20; done`,
`pytest >/dev/null`): a hang then looks like a slow run, and the `rewrite-test-runs` hook denies it. Start `sl run_tests --full --background`
and wait with `sl run_tests --wait`. It exits 3 with `SUSPECT HANG` (verdict blocked or spinning, process tree) once the run stops producing
output, and a wait that runs out says `TIMEOUT`. After a SUSPECT HANG do not wait again: the loop is in the code under test, so hand the file to
a fixer; `--wait --patient` only when the tests are known to be slow.
