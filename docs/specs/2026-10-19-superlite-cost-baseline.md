# superlite cost: baseline, drivers, levers

Date: 2026-10-19. Goal: superlite cost per build at or below superpowers on the same task, without losing the speed or quality gains.
Source: `bench/results/combo-*.json` (the sitegen + minisheet task, 177 hidden tests, headless Opus), `bench/results/routing-recommendation.md`, `skills/swarm/config/models.json` prices. Costs are API-equivalent (`model_usage.costUSD`).

## 1. Baseline

| Arm | Run | Min | Cost | Opus | Sonnet | Dispatches |
|---|---|---|---|---|---|---|
| superpowers inline | e2 | 30.1 | $7.07 | 4.15 | 2.92 | 10 |
| superpowers inline | v1 | 21.2 | $5.59 | 1.75 | 3.84 | 8 |
| superpowers orchestrated | b1 | 27.2 | $9.03 | 7.41 | 1.61 | 9 |
| superpowers orchestrated | e1 | 13.3 | $4.58 | 1.56 | 3.03 | 6 |
| superpowers orchestrated | v2 | 31.9 | $9.25 | 6.04 | 3.20 | 15 |
| (superpowers orchestrated, outlier) | v3 | 47.6 | $10.18 | | | 11 |
| superlite, current stack | k1 | 16.1 | **$9.38** | 4.01 | 5.35 (+0.02 Haiku) | 21 |
| superlite, older builds | b1 / v1 / v2 / v3 / w3 / e1 / e2 | 15.5 to 33.9 | $5.54 / 7.20 / 9.25 / 8.73 / 7.81 / 7.89 / 9.17 | | | 9 to 17 |

Means (outlier excluded): superpowers inline **$6.33** (n=2), orchestrated **$7.62** (n=3), all five **$7.10**. superlite current stack **$9.38** (n=1). To match: cut $2.3 (to the five-run mean) or $3.1 (to inline).

Caveats. Run-to-run spread is large in both arms ($4.58 to $9.25 for superpowers, $5.54 to $9.38 for superlite), so one rep cannot confirm a saving under about $2. Every superlite figure except k1 is an older build. All runs scored 177/177 hidden tests; robustness probes: superlite 3 to 4 of 7, superpowers 1 to 6 of 7.

## 2. Where the $9.38 went (k1)

By token type (cache writes are 1-hour: 2x input price, $8 per M on Opus, $4 per M on Sonnet):

| Item | $ | Share |
|---|---|---|
| Cache writes, Sonnet agents (672k tokens) | 2.69 | 29% |
| Cache writes, Opus coordinator (193k) | 1.54 | 16% |
| Output, Sonnet (224k) | 2.24 | 24% |
| Cache reads, Opus (8.07M; 62 calls, median context 141k, max 182k) | 1.61 | 17% |
| Output, Opus (47k) | 0.93 | 10% |
| Cache reads, Sonnet (5.5M) | 0.55 | 6% |
| Haiku | 0.02 | 0% |

By role (from the per-dispatch context cost; output is spread by model):

| Role | Count | Cost |
|---|---|---|
| Opus coordinator | 1 | 4.01 (planning, calls 0 to 15: about $1.0 context plus most output; dispatch and finish loop, calls 16 to 61: about $2.0 context) |
| Sonnet implementers | 17 | about $0.27 each, 4.5 total |
| Sonnet package reviewers | 2 | 0.46 |
| Opus final reviewer | 1 | 0.13 context |
| Haiku Wave 0 | 1 | 0.02 |
| Fix-only agents | 0 | 0 (no fixes were needed in k1) |

Compared with the superpowers five-run mean (Opus 4.18, Sonnet 2.92), superlite's **Opus is already at parity (4.01)**. The whole gap is **Sonnet: 5.35 against 2.92**. Sonnet cache writes are 672k tokens against about 367k, output 224k against about 164k.

## 3. Findings

1. **Width does not buy time here, and it costs.** k1 ran 17 implementers at once (peak 18) and took 16.1 min. Older build b1 ran 6 implementers (peak 3) and took 15.5 min for $5.54. Wall-clock in k1 is planning (first dispatch at 244 s), a critical path of 489 s, then a 416 s tail (reviews, tests, finish). More agents shortened none of the three. Each extra agent costs about $0.27 (mostly its own prime and context writes).
2. **The prime is not shared.** Each agent reads `.superlite/prime.md` (26 KB: the whole plan plus spec sections) as a tool result, so it is written to the cache again by every agent. Sibling first-call hit rate is 0.235; only the 4.7k system prefix is shared. Agents then read their own SPEC slice again. Each implementer wrote about 15k tokens for its READY turn (16 agents, $0.98) and re-read them ($0.19): $1.17 in all. A slim prime of about 3k tokens saves about $0.94; sharing the full prime across staggered agents saves about $0.89; both about $1.11.
3. **The coordinator context is large.** Median 141k tokens against 73k in b1 and about 90k in superpowers. The dispatch and finish loop is 46 of the 62 calls and about $2.0.
4. **The cost forecast is low by half.** `sl report` projected agent cost $2.43 against $4.52 actual (+86%). The predictor does not charge each task its prime and context writes, so the plan's cost estimate (and the 70% budget warning) understate real spend.
5. **Haiku is barely used.** One Haiku dispatch in 21 (Wave 0, $0.02). Evidence: 10 Haiku samples on one task (80 LOC, mechanical), 9/9 first-time pass, median $0.01 against $0.115 for Sonnet on the same band. Sonnet's median task is about $0.20. There is no Haiku data above 100 LOC.
6. **Cache writes are 45% of the bill.** All are 1-hour TTL. Claude Code has `FORCE_PROMPT_CACHING_5M` (confirmed in the installed binary). 5-minute writes cost 1.25x input instead of 2x, so about 37% off writes, about $1.6 on k1. It applies to superpowers too, so it does not change the comparison, only the absolute spend. Risk: idle pool agents and the coordinator lose cache after 5 idle minutes and rewrite it.

## 4. Levers

| # | Lever | Est. saving on k1 | Risk | Evidence needed |
|---|---|---|---|---|
| 1 | Plan fewer, larger tasks: cap implementers at about 8 (merge components that share a package; keep the longest task near the old critical path of 450 s) | $1.0 to 2.0 (b1 vs k1 gap is $3.84, part of it coordinator context) | Longer critical path if the merged task is too big; lint size band 1.5 to 6 min already guards | 3 reps at cap 8 vs 3 at current |
| 2 | Slim prime for Sonnet: preamble, constraints, interface lines of all tasks, no whole plan, no spec sections (the agent reads its slice) | about $0.9 to 1.1 | Agents lose cross-task context; contracts stay in Produces/Consumes | Compare first-time pass and rework on 3 reps |
| 3 | More Haiku: tag mechanical work in plans (data tables, fixtures, thin CLI and config wrappers, README and docs, plain adapters) as `Kind: mechanical`, 100 LOC or less, off the critical path with 120 s slack | $0.1 to 0.2 per task, 3 to 5 tasks, so $0.5 to 0.8 | Thin evidence (one task); rework costs more than it saves if first-time pass drops under 80% | The existing `mechanical_ab.py` on 3 more task shapes before widening the rule |
| 4 | Coordinator on Sonnet for the dispatch and finish loop, Opus only to write and lint the plan | about $1.6 | Plugin cannot switch the session model, and a subagent cannot spawn agents, so this is a user setting (`/model`) after the plan is stable | One rep with Sonnet session plus an Opus planner agent |
| 5 | Shorter coordinator turns: batch DONE handling, fewer wake-ups, smaller reports | $0.3 to 0.7 | Report lag slows dispatch | Count calls with a batched wait |
| 6 | `FORCE_PROMPT_CACHING_5M` | about $1.6 | Cache rewrite after idle gaps | Rep with the env var set, both arms |
| 7 | Calibrate the cost predictor (charge per-agent prime and context writes) | $0 (accuracy only) | none | Fit on the 18 history rows |

Levers 1, 2, 3 and 5 together are about $2.2 to 3.9, enough to reach the five-run mean ($7.10) and plausibly the inline mean ($6.33). Lever 4 and 6 are optional extras.

## 5. Where else Haiku could work in orchestration

Ranked by confidence:

1. **More mechanical implementers** (lever 3). The largest and best-measured use: 10 to 20x cheaper than Sonnet per task.
2. **Digest agents for the coordinator.** Long reads (spec, test output, review packet, progress files) go to a Haiku agent that returns at most 150 words; the coordinator never pays Opus rates to carry them. `test-runner` already works this way. Risk is a lossy digest, so use it for logs and results, not for the spec the plan is written from.
3. **Reviewer triage.** A Haiku pass that checks the review packet against a fixed checklist (spec coverage by example, probe results, open incidents) and escalates only flagged packages to the Sonnet package reviewer. Unmeasured; start by running both and comparing findings.
4. **Docs and finish chores.** README, usage docs, changelog entries and the finish summary.
5. **Not Haiku:** fixes (existing rule), critical-path tasks, the final review, the plan.
6. **Not possible:** a Haiku dispatch loop, because a subagent cannot spawn subagents. The loop runs in the session model.

## 6. Not measured

- Any lever above. All savings are estimates from k1's token counts and from older runs.
- Superlite's current stack has one run. Superpowers has two inline and three orchestrated.
- b1's older stack differs (no Wave 0, no packet review), so lever 1 is a hypothesis from a cross-build comparison.
- Quality effect of any lever (hidden tests were 177/177 in every run; robustness probes vary 1 to 6 of 7 and are noisy).
- Cost on a subscription (this is API-equivalent dollars).

Plan: C:\Work\Planning\Plans\2026-10-19-superlite-cost-parity-plan.md

## 7. Rep s1 (ts-sitegen, slim prime), stopped at 17 min

Not scored, so no cost or time comparison. The slim prime for that task was 5,048 bytes against 18,364 in f1. Two bugs surfaced, neither caused by the prime: a test run hung about 7.4 min (`tests/markdown.test.ts`, cause not yet found), and a git-ignore ordering bug. Wave 0 ran `npm install` at 18:49:51 and wrote `.gitignore` at 18:51:38, so T4's scope check at 18:50:43 flagged 1,292 node_modules files as outside Owns (1,362 incident lines against 2 in f1).

Fix in progress: a per-language ignore template (`config/gitignore.json`) that `wave0.py` appends to any existing `.gitignore` before any agent runs; an optional plan header line `**Ignore:**` where the planner names project-specific generated paths; the current `.gitignore` shown in every implementer's prime; a union ignore list in `commit_task.py`; incidents grouped one row per folder. Details in the plan.
