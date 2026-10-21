# Proposal: pilot superlite in place of superpowers for MoveDocs

**Status:** draft for review, 2026-10-18
**Author:** Claude (for CWogu)
**Decision requested:** approve a time-boxed rollout of superlite on `movedocs` (section 6), starting with named developers and aiming for three tickets in parallel per developer. This is a request to pilot, not to switch the whole repo today.

## 1. Summary

MoveDocs currently runs the `superpowers` plugin (`.claude/settings.json`, and the "MoveDocs delta" in `CLAUDE.md`). superlite is a build workflow for autonomous, parallel work: you hand it a spec and come back to finished, tested work. The latest and most optimized run, **k1, built the combined benchmark in 16.1 minutes**. Superpowers took 25.7 minutes on average when it built inline and 30.0 minutes when it orchestrated subagents. Every run passed all 177 hidden tests.

Recommendation: introduce it through one or two trusted developers (section 6) on real tickets with superlite's **human-orchestrator** mode (you approve the plan before anything runs), judged on the speed and correctness gates in section 6, with the goal of each developer working three tickets at once. Switch the repo default only if the gates pass.

## 2. Why this fits MoveDocs

MoveDocs is already moving toward clearer and clearer written documentation: a binding `CLAUDE.md`, per-project `CLAUDE.md` files, a written plan before code, briefs and reports handed over as files, and domain skills under `.claude/skills/`. superlite works from exactly that kind of input. Its results come from a clear spec and a plan written as a task graph, so the more precise our written contracts get, the more of the work an autonomous workflow can take without a human steering each step.

- **Written plan first.** The human-orchestrator mode produces a two-page, plain-language review plan with diagrams. Nothing runs until it is approved, which matches "a written plan, read by a human, before an implementer touches anything".
- **Files, not chat.** superlite records rulings, progress and test results in `.superlite/` (`progress.md`, `test-summary.json`, `metrics.jsonl`), which fits "artifacts move as file paths" and "tests pass requires read output as evidence".
- **Humans keep the irreversible steps.** superlite never pushes; a human merges.
- **Autonomy as the goal.** Our work is mostly specified tickets (an endpoint, a controller and its tests, a migration script) that split into independent tasks. That is the shape superlite is built to run in parallel while the team does other work.

## 3. What the benchmarks show: speed

Task: sitegen + minisheet as one project (177 hidden tests), same machine, headless Opus. Times are wall-clock to a finished build. All three arms passed 177/177 in every run, so quality on the spec did not separate them.

"Orchestrated" means the score file shows implementer subagents were dispatched (`implementer_dispatches` > 0). Runs with none are inline.

| Arm | Runs | Wall-clock per run (min) | Mean (min) |
|---|---|---|---|
| superpowers (inline) | e2, v1 | 30.1, 21.2 | **25.7** |
| superpowers, orchestrated | b1, e1, v2, v3 | 27.2, 13.3, 31.9, 47.6 | **30.0** |
| **superlite, current stack** | k1 | **16.1** | **16.1** |

How k1 compares:
- **37% less wall-clock** than inline superpowers (16.1 against 25.7 min).
- **46% less wall-clock** than orchestrated superpowers (16.1 against 30.0 min).
- k1 ran **18 agents at once** (3 to 4 for superpowers), and its last build task ended at about 9 minutes; the rest was review and test time.

Earlier superlite builds on the same task were slower and varied more (15.5, 21.4, 23.2, 25.0, 30.5, 32.8 and 33.9 min). They predate the 0.3.1 work that made k1 faster (fewer process spawns in each coordinator step, one `dispatch` call for ready tasks, early dispatch of stable tasks, fixers and test runs taken off the coordinator), so this proposal is written against k1, not that history.

**What to weigh honestly:**
- **Superpowers has not been re-run** since the 0.3.1 changes. A same-day superpowers baseline is part of the pilot (section 6).

## 4. Conflicts with our `CLAUDE.md`

The "Agent Orchestration Conventions" section binds agents in this repo, so these need a decision before the pilot, not after.

| `CLAUDE.md` rule | superlite behaviour | Proposed handling |
|---|---|---|
| Fan-out is opt-in; ceiling about 15 agents | Up to 18 agents at once, by default (k1 used 18) | Pilot only with explicit operator approval per run; cap agents at 15 if superlite's config allows it (check `superlite\docs\config.md`; not verified) |
| One fresh implementer per task, with an explicit `model` override on every dispatch | Tier is set by the agent definition (implementer, implementer-haiku, fixers). Passing `model` is treated as a deviation | Amend the rule to "model is set by the agent definition", or accept the deviation for the pilot |
| Two-stage review after **each** task | Batched package review, one fresh Opus final review, rolling review for the last package (untested) | Accept batched review in the pilot, and use the final-review findings as a gate |
| One worktree per dispatched agent | Parallel agents share one tree, with owned files per task and a pool ledger | Run each pilot ticket in one worktree under `.claude/worktrees/<slug>/`; a conflict means stop and report |
| Dispatch **one** fix agent for the whole findings list | Tiered fixers, escalating after two failed rounds | Accept; findings go to the tier that fits |
| Process skills ship in `superpowers`; "do not reimplement them" | superlite replaces these skills | Needs a `CLAUDE.md` edit. This is a policy change, so it needs sign-off from whoever owns the upstream Orchestration Policy page |
| Plan mode is the default | The AI orchestrator can plan unattended | Use the **human orchestrator** only |

superlite's README says the two plugins conflict (shared skill names, both inject session context). **Swap them in `.claude/settings.json`; never enable both.**

## 5. How superlite gets its speed

Parallel waves and a plan built for them are the cause; the benchmark analysis found most of the gain comes from collapsing serial chains:
- The plan is a task graph with interfaces and examples, so independent tasks start together.
- Mechanical work goes to smaller, faster agents; harder work stays on stronger ones.
- The coordinator does not edit code or run test suites in the foreground; fixers and background test runs take that off its path.
- One fresh final review reads a packet built from the run logs, not the whole project.

## 6. Introducing it to the team

**Goal:** each developer who adopts superlite works on **three tickets at once**. superlite does the building in parallel inside each ticket; the developer acts as the human orchestrator, approving three short plans and reviewing three PRs instead of writing three tickets' worth of code serially.

**How three at once works under our rules:**
- One worktree and branch per ticket under `.claude/worktrees/<slug>/` (our existing worktree-isolation rule), so the three tickets never touch each other's files.
- `CLAUDE.md` caps a workflow at about 15 agents, so each ticket runs with a cap of about 5 agents (3 x 5 = 15). superlite's default is up to 18 per run, so the cap needs to be set (see section 4).
- The developer's time is the shared resource: each ticket needs a plan approval up front (a two-page review plan) and a PR review at the end. Start the three tickets staggered by a few minutes, not at the same instant, so the plans can be read one at a time.
- Pick tickets of two to six independent tasks. Exclude anything visual (`CLAUDE.md` requires a human brainstorm), migrations (DBA-owned) and hotfixes. Prefer three tickets in different areas of `movedocs-api` so their tests don't collide.

**Prerequisites** (before anyone starts): the section 4 deviations are approved, the plugin swap is in `.claude/settings.json` on the pilot branches only, and the existing `unit-testing`, `observability-review` and `performance-testing` skills are left untouched.

### Who to start with

The right first users are developers whose feedback the team will trust, who already work from written specs and care about test quality. From the repo, **Alex Jones** fits that most directly: the `unit-testing` and `performance-testing` skills under `.claude/skills/` are his area, and superlite's correctness gate runs through those practices. **Bisrat** is the other natural voice: as a releaser, his view on whether a PR is safe to ship is exactly what the correctness gate needs. Kenna, Mike, Jessica and Jordani are the other developers I have names for, but I don't have enough information to say which of them to put first; that choice is yours.

### Options

| Option | Who | What happens | Best when | Trade-off |
|---|---|---|---|---|
| **A. One champion** | Alex Jones | Alex runs 1 ticket, then 3 at once, for two sprints, and reports to the team | You want the fastest, cleanest signal and one trusted voice | One person's workflow; weaker on how a less specialist developer finds it |
| **B. Champion plus releaser** | Alex Jones and Bisrat | Alex builds three tickets at once; Bisrat reviews and releases the resulting PRs and judges them for release safety | Correctness is the main worry | Needs Bisrat's review time; two people to coordinate |
| **C. Small cohort** | Alex Jones, Bisrat and one more developer of your choice | Each runs three tickets at once for one sprint; 30-minute feedback session at the end of each week | You want to know whether it works for different working styles before a team decision | Three people's time at once, and more cost to roll back if it disappoints |
| **D. Demo, then opt-in** | Alex presents a finished three-ticket run to the team; volunteers sign up | Anyone who wants it starts at one ticket and scales to three | The team is split on the idea and you want pull, not push | Slowest; volunteers may be the already-convinced |

**Recommendation: B, then widen to D.** It tests speed with a developer who knows the testing practice and correctness with the person who signs off releases, which are the two gates. Move to a demo and opt-in once the gates pass.

### Rollout steps (for any option)

1. **Week 0, setup (about half a day):** prerequisites above; a 30-minute walkthrough of the human-orchestrator flow and the review plan.
2. **Week 1, one ticket:** the champion runs one ticket end to end and compares it with the same-day superpowers baseline where a like-for-like ticket exists.
3. **Weeks 2 to 3, three at once:** the champion runs three staggered tickets in parallel.
4. **Feedback:** after each ticket, three questions: was the plan clear, what did you have to fix by hand, would you use it again. A 30-minute weekly conversation with the owner of the pilot.
5. **Week 4, decide:** present the gate results to the team; pick D (open it up), extend, or roll back.

### Gates

Two gates, both measured per ticket and across the three-at-once weeks:

| Gate | Pass line |
|---|---|
| **Speed** | Each ticket takes at least 25% less wall-clock (plan approved to PR-ready) than the baseline; and a developer gets all three tickets to PR-ready in no more than the time the baseline workflow takes for two |
| **Correctness** | Build and test gates pass on every ticket; at least two of three PRs are approved by the human reviewer at the first review; and the reviewer finds nothing that superlite's own reviewers should have caught |

**Rollback:** revert the settings change. superlite writes only to `.superlite/` and the worktrees, so there is nothing else to clean up.

## 7. Risks

| Risk | Mitigation |
|---|---|
| Both plugins loaded by mistake | One settings change; check `/plugin` output in the first session |
| Plan adherence is stochastic (superlite once skipped its own flow) | Human-orchestrator plan review; count a run where no `sl` tool ran as an adherence failure and rerun it |
| superlite is our own plugin (`iha2/superlite`) with a bus factor of one | Pin the version in the settings (`1.0.0`); keep superpowers as the fallback |

## 8. Open questions

1. Who owns the upstream Orchestration Policy page, and can the `CLAUDE.md` rules in section 4 be amended for a pilot?
2. Is the 15-agent ceiling per developer or per workflow? Three tickets at once at about 5 agents each fits per developer; per workflow it would need a lower cap.
3. Which developer should be the third in option C, and who owns the weekly feedback session?

## 9. Sources

- `superlite\bench\results\combo-*.json` (wall-clock and dispatch counts per run; k1 is `combo-superlite-k1.json`)
- `superlite\README.md` (positioning, "Not a rival to superpowers", install and conflict note)
- `C:\Work\Planning\Specs\2026-10-17-superlite-architecture-design.md` §12.3 to §12.7
- `movedocs\CLAUDE.md` (Agent Orchestration Conventions, MoveDocs delta) and `movedocs\.claude\settings.json`

## Related
- Depends on: [[2026-10-17-superlite-flows-design]], [[2026-10-17-minisheet-benchmark-design]]
- People named as options: [[Alex Jones]], [[Bisrat]]
- Projects: [[MoveDocs]], [[Superlite]]

Hub: [[Specs index]]
