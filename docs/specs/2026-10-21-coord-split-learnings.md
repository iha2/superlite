# What the coord-split work teaches (coordinator-agnostic)

Date: 2026-10-21. Source: the `coord-split` worktree (commits `5f9f169` and `ff6f8e0` plus uncommitted edits), compared with `main` at `0dbbd18`. The numbers are stable so you can say "apply 3, 7, 12". Item 9, one final review per run with no package reviews, is left out because it has not worked out.

Tags: **[read]** read by me, **[reported]** read by a subagent and not checked by me, **[doc]** a claim from the repo's own notes (`docs/split-mode.md`).

## Bugs found, and fixes that would help in `main` now

1. **Haiku has been running as Sonnet.** `docs/split-mode.md` says `implementer-haiku` ran on Sonnet in every run (`sg1`, `sg4`, `sg11`), because a nested `experimental: cacheTtl: 1h` block in the agent file made Claude Code ignore `model: haiku`. `main`'s `agents/implementer-haiku.md` still has the block **[read]**, as does `implementer.md` **[reported]**. The cause is a **[doc]** claim not yet reproduced. If true, every Haiku-lane result on `main` is really Sonnet, including the "$0.2 to $0.5 saving" estimate. (The 56 Haiku messages counted in rep `q2` were not necessarily the mechanical lane; they may be Claude Code's own internal calls.)
2. **A test that keeps agent frontmatter flat** (`tests/scripts/test_agent_files.py`) **[read that it exists]**. It stops item 1 from coming back.
3. **A `run_tests` retry on Windows file replacement** (`replace_retry`) for the `PermissionError` that appears with 12 concurrent agents **[reported]**.
4. **`commit_task` skips `__main__.py` in its import check**, since importing an entry point runs the program **[reported]**.
5. **`report_of` falls back to the transcript** when an agent's last message is empty **[reported]**.

## Cost and time ideas (none measured against the $7.10 target)

6. **Bundle ready tasks, 3 per agent** (`tasks_per_agent`, one report per bundle). Fewer agents means fewer prime writes. No cost A/B exists **[reported]**.
7. **Skeleton alone and first.** Nothing else goes out until Wave 0 is checkpointed **[doc]**.
8. **Haiku first, off the critical path.** `slack_gte: 2.0` in `routing.json`, Haiku lines sorted first and exempt from the cap **[reported]**. It depends on item 1.

## Reliability fixes in the coordination logic

10. **Checkpoint a task only if its agent's DONE came after the task was sent**, using millisecond timestamps **[reported]**. It has a bug: the DONE test is a `\bDONE` regex that also matches "NOT DONE" and passes a partial bundle report. Fix before lifting.
11. **The `sl next` oracle.** Code picks the phase, tier, agent, bundle and fix tier, writes an allow-list, and prints every prompt in a `PROMPTS` section **[read in the Sonnet file]**. It would simplify an Opus coordinator too.
12. **Hazards to fix before lifting `sl next`** **[reported]**:
    - `sync_pool` overwrites `pool.json` from metrics only. It drops the `plan` field and zeroes the context and idle limits, so those rules stop working, and it clobbers non-split runs.
    - The allow-list entry is recorded before the agent launches, so a failed launch counts as sent forever.
    - `brief.make` appends to files while printing, so each `next` call has side effects.
    - The review gate can deadlock if no reviewer is idle.
    - Finding ids are positional, so a rewritten report can mark the wrong finding fixed.
    - Gate escalation counts any block, not the same step.
13. **Escalate but keep building.** An escalation holds only its own task and the rest continue. A blocked step is retried once before escalating **[doc]**.
14. **Escalation plumbing.** `sl escalate` (cap of 3 consults), `sl health` (one line, from files only), and a Stop hook that stops a session ending on an open escalation, at most 3 times **[reported]**.
15. **Tolerant finding parser plus a `restate` turn.** Reviewers drift into formats like ranges, `Fix:` without a dash, and `(tier 2)`. The parser reads them, and if nothing parses, the reviewer gets one restate message before an escalation **[doc]**.
16. **A red full suite becomes tier-1 fix items**, one per failing test **[doc]**.
17. **Harness quirk: `run_in_background` sent as the text `"true"` runs the agent in the foreground.** The hook rewrites it. Found in run `sg12`, and applies to any coordinator **[doc]**.

## Model instructions, and what they teach

18. **Paste, don't compose.** The coordinator file says every prompt comes from the `PROMPTS` section and must be pasted whole, with a marker line the guard checks. That removes prompt drift for any coordinator **[read]**.
19. **One block per turn, all calls in one parallel message, then `next` again.** A `WAIT` line means "end the turn, reports arrive on their own" **[read]**.
20. **An explicit never-list** that includes "even if a plan Note or the user's text says to". It resists instructions injected through plan text **[read]**.
21. **`agents/coordinator-sonnet.md` is 6.3 KB against `SKILL.md`'s 11.6 KB**, because code prints the loop. If the oracle lifts, the Opus coordinator's instructions could shrink by a similar amount **[read sizes]**.

## Measurement and observability

22. **Coordinator cost by phase** (plan, loop, finish) in `metrics.py` and `report.py`, plus escalation totals and guard denials **[reported]**.
23. **A planning timeline** (`capture-plan-events`) logging skill loads and plan saves with sizes **[doc]**, and **`capture-bash-failure`** for failed calls **[reported]**.
24. **The gate stress swarm** (`bench/swarm_fixture.py`, pressure prompts, a plan with cost estimates of $0.12 to about $1.2) as a cheap way to test the guards **[reported]**.

## Hazards in the guard hook itself, if you lift it

25. It spawns bash on every tool call in every session, about 250 ms on Windows. `escalation-stop` and `capture-plan-events` do the same. Only some calls exit early **[reported]**.
26. It uses bash 4 features (`${x,,}`, `EPOCHREALTIME`), unverified on macOS bash 3.2 **[reported]**.
27. G7 is regex-based and can be bypassed by quoting, `$(...)` or `bash -c` **[reported]**.

## Housekeeping

28. **The staged deletion of 134 benchmark result files is not data loss.** They are still on disk, in `main` and in history. The worktree's `.gitignore` would stop tracking all of `bench/results/` and every `.superlite/` folder. About 43 result files exist only on disk, so deleting the worktree would lose them **[reported]**. Suggest declining this or limiting it to generated run logs.
29. **Merge conflicts with `main`'s uncommitted work:** `agents/implementer.md` and `implementer-haiku.md` (certain), `hooks/rewrite-test-runs` and `run_tests.py` (likely) **[reported]**. `testprobe.py` has no conflict.
30. **The flow doc is stale:** it still calls G2 to G10 "planned", but they are built.

Suggested order for `main`: 1 and 2 first, then 3 to 5, then 22 and 23. Hold 6 to 8 and 10 to 12 until the hazards in 10 and 12 are fixed and a cost run measures them.
