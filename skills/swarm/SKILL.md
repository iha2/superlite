---
name: swarm
description: Use when executing a superlite plan that has a %% taskgraph. Runs a ready-queue scheduler over an idle agent pool, with scripts for the mechanical steps, selective reviews, and a packet-only final review.
---

# Swarm

You are the coordinator. You decide, dispatch and rule; scripts do everything with a definite answer; agents design, write, review and fix. Read each script's one-line output. One line of narration per tool call.

Shorthand (every agent brief states `SUPERLITE=<path>`):
`SUPERLITE` = the plugin root, two levels above this skill's "Base directory for this skill: ..." path: `SUPERLITE="$(cd "<base dir>/../.." && pwd)"`, passing the base dir through `cygpath -u` first when that exists. `SL="$SUPERLITE/skills/swarm/scripts/sl"`; always run it as `bash "$SL" ...`. `PLAN` = the plan file; pass `--plan $PLAN` everywhere. Exit 3 means no Python: `bash "$SL" incident add` it and continue.

**You never edit code or run raw `pytest`; fixes go to fixer agents: tiers, escalation and contract in `fixing.md` (Fixing). Never pass `model`.**

**Rulings, not stalls.** Decide conflicts and plan defects yourself (**spec** binds when present: done+why outranks the plan's how; `sl agent_specs status`). Append `Ruling: <decision> - <why> - <cost if wrong>` to `.superlite/progress.md`.

## Setup

- **One feature, one worktree** (AGENTS.md): if this plan is a feature (or any multi-file build that could collide with another in-flight change), work only in that feature's git worktree on branch `superlite/<topic>` — cut with `git worktree add -b superlite/<topic> .superlite/worktrees/<topic> <base>` if missing, and move the agent root there before edits. Never edit a second feature in the same checkout. Trivial one-line fixes on the current branch are exempt. Write the start SHA to `.superlite/progress.md` (`- start <sha>`). `checkpoint` adds `.superlite/` to `.git/info/exclude` itself.
- Wave 0 is never yours: when the machine plan is written, run `bash "$SL" wave0 --plan $PLAN` and send Wave 0 in the same message as `lint --stable`. Lint skips START FIRST once the brief exists; `STUB UPDATE:` means refresh stubs. `wave0 --plan` reprints the line. Then `checkpoint T0`. Tasks do not wait for T0. Then `bash "$SL" plan_tools adapter --plan $PLAN`.
- Prime now: `bash "$SL" build_prime --plan $PLAN` prints `hash size` and writes the slim `.superlite/prime.md` (rules, header, constraints, stable interfaces; `--prime full --spec SPEC --sections "1,3"` = whole plan). Start the first ready tasks now.
- Incidents: `.superlite/incidents.jsonl` (agents call `bash "$SL" incident add`); you log BLOCKED and NEEDS_CONTEXT with `--agent coordinator`.
- Graphify: `bash "$SL" graphify_gate` prints JSON. `use` false: skip. Installed: `graphify update .`. `needs_install` and `allowed`: `uv tool install graphifyy`, `graphify extract . --code-only`. You alone run `graphify update .` after each package is checkpointed.

## Planning check

Fixed order, nothing waits on plan polishing: write plan -> `bash "$SL" plan_tools prune --plan $PLAN` (drops edges that `e.g.` examples make removable, then schedules) -> `bash "$SL" plan_tools lint --plan $PLAN --spec SPEC --stable` (lists STABLE and FIX tasks, writes `.superlite/stable.json`; prints `SEND BEFORE ANY PLAN EDIT:`, ends with `DISPATCH NOW: T2 T4` or `none`) -> `dispatch` and start every task on that line in the same turn -> only then patch the FIX tasks (edits or a rewrite) -> re-lint -> dispatch the newly stable tasks -> repeat until lint passes. No agent waits on plan polishing: never patch, rewrite or re-lint while a `DISPATCH NOW` task is unsent.

Each dispatch round, in the same message as the Agent calls, run the `THEN LOG` line lint prints (it appends `unblocked: dispatched T2 T4 at HH:MM:SS; waiting on fixes: T1 T3` to `.superlite/progress.md`). `finish` checks it against `lint-log.jsonl`.

Dispatched tasks are frozen: a rewrite keeps their blocks byte-identical, else lint says `FROZEN:` (restore it; a real change is a follow-up fix task). `**Status:** draft` is an optional manual hold. `MISSED TARGET: ... critical path T1 -> T5` is a warning: split or stub the named tasks, or `Ruling: accept predicted <N> min - <why sequential> - <cost>`. `NOTE:` is advice.

## The scheduling loop

```mermaid
flowchart TB
  N[dispatch] --> A{per batch}
  A -- agent=ID --> M[task message]
  A -- poll IDS --> Q[relevance poll] --> M
  A -- fresh --> P[prime verbatim] -- READY --> M
  M --> D[DONE reports] --> C[checkpoint T.. --dispatch] --> A
  C -. background .-> PR[probe_run --checkpoint]
  C -. both sides in .-> SM[pt_review contract seam]
```

Keep `pool.json` under `sl session path` (legacy `.superlite/pool.json`): `{"agents":[{"id","tasks":[...],"files":[...],"context_k":N,"idle_min":N,"busy":false,"current":"Tn","tier":"haiku|sonnet"}]}` (`tier` = the line's; T0's is `haiku`). Sending a line or spawning its agent sets `busy: true` and `current: Tn` (a batch: `Tn,Tm`); `checkpoint` clears each task it records. The ledger and `current` are the state: you keep no lists.

1. `bash "$SL" dispatch --plan $PLAN --cap 10` prints one line per batch (`Tn` or `Tn,Tm`) from packing that minimizes max(B+A, F/T) (busy counted; CP alone; context budget), longest first: `Tn agent=ID tier=T`, `Tn poll ID,ID tier=T` or `Tn fresh tier=T agent=TYPE`; no empty lines, no agent on two. Tiers from lint (`config/routing.json`); never override or cross tiers. Start exactly those batches. Compact every 30 minutes (`compact_due`).
2. Per line: `agent=ID`: SendMessage the task to that idle agent. `poll ID,ID`: one SendMessage to each listed agent, "Relevance 0-10 for Tn (files, consumes)? Reply `score: N - reason`"; use the replies present at your next turn, highest wins, tie to the smallest `context_k`. `fresh tier=T agent=TYPE`: Agent TYPE with its tier's prime file pasted verbatim (session `prime.md`; haiku: `build_prime --tier haiku` writes `prime-haiku.md`), same bytes for the whole run, and send its task only after its reply `READY`. `evict=ID` (stderr): drop it. `dispatch` never offers a busy agent, or one past its tier's limits.
3. Task message (a batch: one message, tasks in line order): heading and `PLAN`, "your task text: `bash "$SL" task Tn --plan $PLAN`" (current plan, never retyped; also reads neighbours), Owns and files not to touch, the Review Focus categories for its files, rulings and interface names (a fake returns the producer's `e.g.` examples), `SUPERLITE=<path>`, the verify command (`bash "$SL" run_tests --fast PATHS --background`, then `--wait`), and "reply in at most 200 words per task: status DONE / DONE_WITH_CONCERNS / NEEDS_CONTEXT / BLOCKED, files, test result, open questions".
4. Mark reporters `busy: false`, then record all DONE tasks in one call (hidden checkpoints; `sl autocommit on` = real commits): `bash "$SL" checkpoint T3 T5 --plan $PLAN --dispatch` saves each task's own files, prints `Tn <sha>` or `Tn FAILED` (one task: just the SHA), then `next:` and dispatch lines. A FAILED task lists unowned files or syntax/import problems: send it back. DONE_WITH_CONCERNS: fix correctness ones first. NEEDS_CONTEXT: always answer the same implementer; if the plan is silent, add a `Ruling:` and answer with it; resume with the answered task first (`checkpoint --question` / `--answered`). BLOCKED: log (`checkpoint --blocked T --tier X`); fixer one tier up (Fixing). A haiku miss: `bash "$SL" routing miss --task Tn --plan $PLAN`; never retried on Haiku. Act on the `next:` lines.
5. Each checkpoint has already started `probe_run --checkpoint` and, for each seam whose two sides are now both checkpointed, `pt_review contract --seam` in the background. A pass logs `verified`; a mismatch is an incident. Route the fix per `fixing.md`.
6. Probe failures: `incident hotspots --files <task files>` before the next work; open failures go to fixers (`fixing.md`). `checkpoint` accepts generated `tests/regression/` files from any task.

## Reviews (D16)

```mermaid
flowchart LR
  C[checkpoint] --> T{seam, Complexity >= 5, Review Focus risk, open incident?}
  T -- yes --> R1[package-reviewer on that task]
  T -- no --> W[wait for the package] --> R2[one package-reviewer, all its diffs]
  R1 & R2 --> X[fixes + regression tests] --> K[checkpoint again]
```

- Matching tasks get `superlite:package-reviewer` on checkpoint, in the same message as the next dispatches. Others get one review per package once checkpointed. In the last package, review each task on checkpoint.
- Brief: `SUPERLITE=<path>`, checkpoint or range, spec slice, `PLAN`, files in scope, `incident hotspots` output, adapter path. It reviews, fixes with regression tests, logs; you checkpoint its fixes; its files are off limits meanwhile.

## Coordinator batching

- One message per turn: all dispatches, reviews, background starts.
- Every test run is `run_tests ... --background`; never wait, except the one `--wait` before the final checkpoint.

## Final review

```mermaid
flowchart TB
  G[all reviewed + saved] --> PK[packet from existing summary]
  G --> ST[run_tests --full, background]
  PK --> FR[fresh final-reviewer, findings only]
  FR --> V{findings, suite red?}
  ST --> V
  V -- yes --> FX[fixers by tier] --> ST2[run_tests --background]
  V -- no --> W[run_tests --wait] --> Z[freeze]
```

1. Gate: every task checkpointed, every review reported and checkpointed, every seam `verified`.
2. Build the packet now: `bash "$SL" plan_tools packet --plan $PLAN --base <start-sha>` uses the existing `.superlite/test-summary.json`, the probe results and the incidents, and writes `.superlite/review-packet.md`. Its header says `FAST PATH` or `REVIEW REQUIRED`. In the same message start `bash "$SL" run_tests --full --background`; do not wait for it.
3. Start `superlite:final-reviewer` at the same moment, fresh, brief = `SUPERLITE=<path>` and "read .superlite/review-packet.md", nothing more. The tree stays frozen. It answers in one turn from the packet only: `Verdict: APPROVED` or `CHANGES_REQUESTED` plus `file:line - problem - fix - tier N` lines.
4. Red suite lines are findings too. Send all to fixers (`fixing.md`: tier by understanding, split by file), then `checkpoint Tn` per owning task and one more `run_tests --background`. Leftovers become a `Ruling:`.
5. Right before the final checkpoint, `bash "$SL" run_tests --wait` once; green means freeze.

## Measure and budget

Hooks write `.superlite/metrics.jsonl`; details in `metrics.md`. `lint` and `dispatch` print `budget:` lines when they change (`**Budget-usd:**` in the plan header is the cap). At `WARN` finish what is in flight; at `OVER CAP` start no extra review rounds and no fix rounds beyond the first, then `checkpoint` and finish. `LATE: critical path ...` names the task to unblock first.

## Freeze, rulings, finish

After a green suite: freeze. Archive `.superlite/incidents.jsonl` and `progress.md` next to the plan. Run `bash "$SL" finish --plan $PLAN`; print its rulings in your closing message under "Rulings I made", each with its cost. Its last block is the report summary: print it before any merge or PR option; full: `<plan>.report.md`. Then invoke `superlite:shipping`.

## Stop conditions

Stop and ask only for: an irreversible or destructive operation; a security-sensitive action; an outward side effect (merge, push, publish); a plan so broken every path is a guess.

**AI orchestrator** (`sl orchestrator status`; also user away, "decide yourself"): never end your turn to wait. Take the safe path: defer the outward or destructive action, read a broken plan conservatively, record the ruling; list what you deferred. **Human orchestrator**: nothing is built, dispatched or written but the review plan before the human's approving reply; ask in plain text, end the turn, then if you understand their answer as approval send `bash "$SL" orchestrator approve --review PATH` and the machine plan. Do not pass their words through. `lint --stable`, `dispatch`, `wave0`, `build_prime` refuse before (`orchestrator.md`).

## Red flags

| Thought or act | Do instead |
|---|---|
| Typing the prime or a task before READY | `build_prime`, paste verbatim, wait for READY |
| Work to a busy agent, or "after T3" | `dispatch` |
| Editing code, raw `pytest`, or a foreground suite | Fixer agent; `run_tests --background` |
| `git commit` by hand | `checkpoint T3 T5 --dispatch` |
