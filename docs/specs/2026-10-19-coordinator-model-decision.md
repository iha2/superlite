# When is Opus overkill for the superlite coordinator?

Date: 2026-10-19. Design and analysis only: no repo code changed, nothing committed. Companion to the cost plan (`Plans/2026-10-19-superlite-cost-parity-plan.md`, lever 4 and step 5b) and the cost baseline (`Specs/2026-10-19-superlite-cost-baseline.md`). This note does not repeat their numbers; it decides *when* the coordinator can be cheaper, and corrects one number.

## 1. Short answer

Your two hypotheses are right, and they are one idea. What matters is **how often a judgment call arises, times what it costs if wrong**. superlite already writes that second factor into every ruling (`Ruling: <decision> - <why> - <cost if wrong>`). The strongest signal is not a property of the run at all: it is the **phase**. About 80% of the coordinator's turns in k1 were "run a script, read its one-line output, send what it printed", and the rules for those are enforced by hooks, not by the model. The design judgment lives in a few turns: writing the plan, patching FIX tasks, rulings, and review triage.

Recommendation: **Sonnet runs the session; Opus is called for the two judgment jobs (write the plan, give a ruling) as agents; the run escalates to Opus on named triggers; risky work runs fully on Opus.** Expected saving on k1: about **$0.9 to 1.5** (central $1.2), roughly half of the $2.3 cost gap, with no change to the task graph. It is untested: no Sonnet-coordinator run exists.

## 2. Facts that shape the answer

1. **Opus is only 2x Sonnet, not 5x.** `config/models.json`: input 4 vs 2, output 20 vs 10, cache read 0.20 vs 0.10, 1-hour cache write 8 vs 4. Even moving the whole coordinator to Sonnet saves at most half of k1's $4.01, about $2.0. The loop alone (about $2.0 of Opus) saves about $1.0 on Sonnet. The baseline's lever 4 says "about $1.6 for the loop"; at these prices that figure only holds if the planning also leaves Opus, which this note does not recommend. Correct it to about $1.0 for loop-only, $1.2 for the split below.
2. **A plugin cannot set the session model.** Docs (model-config, skills, sub-agents pages): only the user's `/model`, `--model` or `ANTHROPIC_MODEL`; a `PreModelSwitch` hook can only block or confirm. `opusplan` (Opus in Claude Code plan mode, Sonnet otherwise) exists, but it keys on Claude Code's own plan mode, in which writes are restricted, while superlite writes plan files; it is not documented for plugin flows and probably does not fit. A skill's `model:` frontmatter overrides the model "for the rest of the current turn" and the session model returns at the next prompt; with `context: fork` it sets the forked subagent's model. What "turn" means in a headless run (one prompt, one long turn) is not documented: test it (section 6).
3. **Agents carry their own model.** `agents/*.md` frontmatter sets it (`fixer-opus`, `final-reviewer` are Opus today). So "Opus for one decision" is an Agent call, with no session switch. This is the existing pattern.
4. **Nesting is allowed.** The docs allow subagents to spawn subagents, three layers by default (`CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`), and `skills/using-superlite/references/claude-code-tools.md:17` says so. The baseline (section 5, item 6) says it is impossible; that is out of date. It still should not be used here: a dispatch loop inside a subagent adds a hop to every report and hides the pool state.
5. **The coordinator is 43% of k1's cost** ($4.01 of $9.38) and median context 141k, peak 182k. Context-only reconstruction of its 62 calls (output tokens are an early snapshot and under-count): plan 11 calls, loop 40, finish 11, at Opus about $0.70 / $1.80 / $0.49, so the loop is the largest slice; the plan's cost is mostly output (`Write` of the plan).
6. **k1's 73 coordinator tool calls:** 30 `sl` calls, 21 `Agent`, 5 `SendMessage`, 3 `commit_task`, 6 `Edit` and 3 `Write` (all plan work: draft plus the FIX patches for T7, T15, T16), 2 `Skill`, 1 `Read`. Judgment: the plan draft, one `Ruling:`, three FIX patches. Everything else is relay.

## 3. Where the coordinator actually judges

| Judgment | Where | Cost if wrong | Needs Opus? |
|---|---|---|---|
| Size the request, pick bounded vs architectural | brainstorming | wrong shape for the whole run | yes (planner) |
| Write the plan: task graph, interfaces, `e.g.` examples, sizes | writing-plans | everything downstream; the largest lever on time and quality | yes (planner) |
| Patch FIX tasks after `lint --stable` | SDD "Planning check" | lint re-run; mechanical mostly (the linter names the defect) | no, if the plan is sound |
| `Ruling:` on open choices and spec conflicts | SDD "Rulings, not stalls" | varies; each states its own cost | yes when cost-if-wrong is high |
| Scope-change detection: "a fix that changes scope or an interface the human saw goes back to the human" | writing-plans step 5 | breaks the human's approval | yes (subtle) |
| Fix tier choice (1/2/3), escalation after two failed rounds | `fixing.md` | one wasted round (~$0.1 to 0.3) | no: rule-based |
| Review triage: seam, Complexity >= 5, Review Focus risk | SDD "Reviews" | missed or extra review | no: rule-based |
| Dispatch, commit, probes, packet, finish | scripts | none: the script decides | no |
| Stop conditions: destructive, security, outward actions | SDD "Stop conditions" | irreversible | yes |
| Human-mode plan rewrite after approval | brainstorming / writing-plans | drifts from what was approved | yes (planner does it) |

Four of ten are scripted or rule-based; the loop's relay work (the bulk of its turns) needs only reading comprehension.

## 4. Decision signals, ranked

Ranked by how much each separates "Sonnet is fine" from "use Opus", and by how cheaply it can be measured today.

| # | Signal | Why | Source today | Measured? |
|---|---|---|---|---|
| 1 | **Phase** (plan vs loop vs finish) | Design judgment is concentrated in the plan; the loop is scripted | transcript phase split | k1 only |
| 2 | **Risk class** (auth, secrets, personal data, payments, deletion, migration, security) | Cost-if-wrong is large and unbounded; the final reviewer is already Opus for this | Review Focus categories, request keywords | no |
| 3 | **Open decisions at plan time** (open questions, `Ruling:` count, `spec-ambiguity` incidents) | Your hypothesis (a): the number of calls that need judgment | progress.md, incidents | not logged as a count |
| 4 | **Plan clarity** (your hypothesis (b)): share of tasks STABLE on first lint, `e.g.` coverage, frozen interfaces, MISSED TARGET warning | A clear plan leaves the loop nothing to decide; k1 had 3 FIX of 17 (82% stable first pass) | `lint --stable`, `lint-log.jsonl` | k1 only |
| 5 | **Live trouble signals during the run**: seam mismatches, a tier-3 fix, two failed rounds at a tier, `spec-ambiguity`, the coordinator repeating a failing command | Plan was less clear than lint thought | `incidents.jsonl`, `pt_review` | seam mismatches yes (k1 1 of 3; v3 9 of 9; w3 4 of 8) |
| 6 | **Run size**: task count, concurrent agents, coordinator context | More agents means more reports and a longer context for a weaker model to hold; also a bigger saving | k1: 17 tasks, 18 agents, 141k median context | yes |
| 7 | **Orchestrator mode**: human (approved scope, fewer rulings) vs AI (all rulings are the coordinator's) | AI mode raises the ruling count and has no human backstop | `orchestrator.json` | no |

Signals not worth using: raw incident count (v3 and w3 have 117 and 142, almost all `exception`, which look like tool or environment noise, not decisions); task LOC; share of Haiku tasks.

Where the data is thin: **there is no Sonnet-coordinator run, so no signal is validated against coordinator quality.** Quality has been 177/177 hidden tests in every run on both arms, so hidden tests cannot discriminate; robustness probes (superlite 3 to 4 of 7) are noisy. n=1 for the current stack.

## 5. The router

An advisory router with three decision points and one escalation rule, configured in `routing.json` next to the implementer tiers. It is advisory because the plugin cannot set the session model (fact 2); what it can set are agent models (fact 3) and what `/superlite:start` and the start-of-run line recommend to the user.

### 5.1 Inputs and outcomes

Outcome values: `split` (Sonnet session, Opus planner and ruling advisor), `opus` (Opus session, today's behavior), `sonnet` (no Opus agents at all).

| Decision point | Inputs | Rule | Outcome |
|---|---|---|---|
| A. Request (brainstorming, before planning) | risk class; bounded vs architectural; orchestrator mode | risk class present -> `opus`. Bounded -> `sonnet`. Else -> `split` | printed recommendation; the start menu shows it |
| B. After first lint | stable share, FIX count, MISSED TARGET, task count | stable share < 0.6, or tasks > 25, or an unresolved MISSED TARGET -> recommend `opus` for the rest of the run (patching is design work again) | advisory line |
| C. During the loop | the triggers below | any trigger -> ruling or patch goes to the Opus advisor; two triggers or a risk-class file touched -> recommend `/model opus` and log it | agent call, incident |

Thresholds are starting guesses to be tuned from reps: stable share 0.6 (k1 was 0.82), task cap 25 (k1: 17), 2 seam mismatches, 2 failed rounds at one tier.

### 5.2 Escalation triggers (Sonnet coordinator -> Opus advisor)

Send the Opus advisor a brief of at most 200 words (the conflict, the two options, what each costs) and take back a ruling of at most 100 words, recorded as `Ruling:`. Triggers:

1. A ruling whose own "cost if wrong" is not "none" or "re-run".
2. A scope or interface change that touches what the human approved.
3. A seam mismatch that is not explained by a named defect, or 2 seam mismatches in one run.
4. A tier-3 fix needed, or 2 failed rounds at the same tier.
5. Lint FIX tasks still present after the second patch round.
6. A `spec-ambiguity` incident.
7. The same `sl` command failing twice in a row.

Each use logs `coordinator-escalation: <trigger>` as an incident, so the report can show how often the Sonnet coordinator needed help.

### 5.3 Mechanisms (what to build, in order)

1. **`agents/planner-opus.md`** (model opus, effort medium): runs `writing-plans` including the review plan, returns only the plan path and a five-line summary. In `split` the session writes no plan. Also the agent that does the human-mode rewrite after approval.
2. **`agents/ruling-advisor.md`** (model opus, effort medium): one turn, input as in 5.2, output a ruling; no tools beyond reading. Cost per call about $0.03 to 0.08 from the small prompt.
3. **`routing.json` `coordinator` block** (data, like the tiers): `{"default": "split", "rules": [{"when": {"risk": true}, "outcome": "opus"}, {"when": {"bounded": true}, "outcome": "sonnet"}], "escalate": {"seam_mismatches_gte": 2, "failed_rounds_gte": 2, "stable_share_lt": 0.6}}`; read by a new `sl route coordinator` that prints one line with the reasons.
4. **Keep what already holds the rules:** the hooks (`block-coordinator-edits`, `rewrite-test-runs`, `record-human-reply`) and the scripts. A cheaper coordinator is viable *because* superlite moved rules from prompt text into code; do not loosen any of them.
5. **Skill `model:` frontmatter**, only if the test in section 6 shows it holds for a whole headless run: put `model: opus` on `writing-plans`, which removes the need for the planner agent in some cases.

Do not do: a Haiku coordinator; a dispatch loop in a subagent; per-turn automatic model switching (not possible).

## 6. Expected effect and how to prove it

### Expected effect (k1 shape, unmeasured)

| Item | Now (k1) | `split` |
|---|---|---|
| Plan phase | Opus session, about $1.9 incl. output | Opus planner agent, about $1.4 to 1.9 (fresh, smaller context) |
| Loop and finish | Opus, about $2.0 | Sonnet, about $1.0 |
| Rulings | inside the session | advisor, about $0.1 |
| **Coordinator total** | **$4.01** | **about $2.5 to 3.1** |
| Saving | | **$0.9 to 1.5 (central $1.2)**, 10 to 16% of k1's total |
| Time | 16.1 min | same or a little faster (Sonnet 80 tok/s against Opus 45); the planner is one extra hop (about 10 to 20 s) |

Quality risk: a weaker model holding 140k tokens of relay state. Mitigation is the hooks and scripts, the escalation triggers, and the Opus final reviewer, which is unchanged. Together with the cost plan's levers (slim prime about $1.0, Haiku about $0.5 to 0.8) this would put the run at about the $7.10 target, but this lever alone does not.

### What to log (additive, no behavior change)

- Per coordinator call: phase (`plan`, `loop`, `finish`), model; so `report.py` can print coordinator cost by phase. (Transcript parsing in this note already shows it can be derived.)
- Judgment-call events: `ruling` count, FIX patch count, `coordinator-escalation` with its trigger, advisor and planner calls with cost.
- Plan clarity at first lint: stable share, FIX count, MISSED TARGET.
- Rule violations by the coordinator: `rule-block` incidents (k1 had 2) and any `FROZEN:` restores.
- Coordinator model and who set it (`/model`, start-menu recommendation).

### Experiment, inside the benchmark protocol (one superlite-only rep, stop and analyze at 15 min)

0. **Before any bench: a 5-minute check**, run quickly under the quick-test timebox: does a skill `model: opus` frontmatter hold for a whole `claude -p` run, and does `/model sonnet` plus an Opus `planner` agent work from the plugin? This decides whether the planner can be a skill or must be an agent.
1. One rep of the k1 task in `split` (Sonnet session, planner and advisor agents), same SPEC and flags, run alone.
2. Judge on **phase-level and paired measures, not the total**: one rep carries about $2 of noise, so a $1.2 saving cannot show in the total. Compare against k1: coordinator cost per call and call count in the loop (k1: 40 calls plus 11 finish); plan lint first-pass stable share (k1 0.82); `rule-block` incidents; escalations used.
3. Pass criteria: hidden 177/177; wall-clock at or under about 16 min; loop calls at most 1.3x k1 (about 52 or fewer); stable share not worse than 0.7; no coordinator rule violation that a hook did not catch; robustness probes not below 4 of 7.
4. If it passes, three reps are still required to claim the saving (the cost plan's own rule). If the Sonnet loop takes noticeably more turns, drop to loop-only (Opus plan, Sonnet loop) and lower the claim.
5. If it fails on quality, keep Opus and record which trigger would have caught it; tune thresholds, do not widen the rule.

## 6.1 Ask-when-unsure and counting escalations (added after review)

- **Instruction.** The Sonnet loop agent's brief (and the session prompt in design A) says: when unsure, or when a health warning repeats, escalate; never guess on a ruling whose "cost if wrong" is real. This is the primary layer; the script-detected triggers (`sl health`) are the backup for when the model does not report. A weaker model overrates itself, so the backup stays, and the counts show which layer caught what.
- **One path to escalate: `sl escalate --kind advisor|planner|takeover --trigger self|health|type --reason "<text>"`.** It writes an incident `coordinator-escalation` with the fields below, so the count is deterministic and never depends on the model remembering to log it.
- **Fields:** id, level, trigger (self-reported, health warning, or decision type), task, cost in dollars, outcome (`resolved`, `overruled` = the advisor disagreed with what the Sonnet coordinator was about to do, `no-change`).
- **Report line:** `escalations: 3 (2 advisor, 1 planner, 0 takeover) $0.19, self 1 / health 1 / type 1, overruled 1 of 3`.
- **What the counts tell us:** the escalation rate per run (if it is high, the Sonnet saving is not real); `overruled` is the direct measure of Sonnet's wrong calls caught; self-reported escalations against health-warning escalations show whether "I am unsure" is a useful signal at all.
- **Limits:** a cap of 3 advisor or planner consults per run (the cost rises with each); a takeover is never silent, it prints in the closing message under the rulings.

## 7. Not measured and open

- No Sonnet-coordinator run exists; all savings come from k1's token counts and the price table. Coordinator output tokens are under-counted in transcripts (early snapshot), so the plan phase is understated in section 2.5.
- Decisions per run are not logged as a count today (rulings live in `progress.md`).
- The thresholds in 5.1 are guesses with one run behind the stable-share figure.
- Whether `opusplan` or a skill `model:` override can be used from a plugin is undocumented (the docs pages were truncated in the check): test, do not assume.
- Human-mode behavior (the approval gate, scope-change detection) is unmeasured on a Sonnet coordinator.
- The risk class keywords are not defined yet; start from the Review Focus categories and add auth, secrets, personal data, payments, deletion, migration.
