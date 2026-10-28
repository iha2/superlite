# How the rules are enforced

Instructions drift. Scripts and hooks do not. These gates run on every build:

| Gate | What it enforces | How |
|---|---|---|
| **The coordinator cannot write code** | Every change goes to a fix-only or implementer agent, by tier; the block message says which | `block-coordinator-edits` hook |
| **You approve before anything is orchestrated** | In human mode nothing is linted, dispatched or sent to an agent until a real typed reply approves the review plan. Any clear approval counts (yes, go ahead, looks good, or similar) | `block-coordinator-edits`, `record-human-reply`, `sl orchestrator approve --review` |
| **No unsafe kills** | No `pkill` or `taskkill /IM`; only PIDs the run started | `block-unsafe-kill` hook |
| **Tests never block the coordinator** | A raw `pytest`, `npm test`, `go test` and so on is rewritten into a background `sl run_tests` | `rewrite-test-runs` hook |
| **No hidden waits on tests** | A test run inside an `until`/`while` loop or with its output sent to `/dev/null` is denied; wait with `sl run_tests --wait`, which reports a SUSPECT HANG | `rewrite-test-runs` hook, hang probe |
| **A plan must lint before work starts** | Stable tasks dispatch at once; others wait with a reason. Size band, frozen interfaces, and chain rulings that must carry the id lint printed | `lint --stable` |
| **Same plan, same agents** | Task features pick the agent tier from `routing.json`; Haiku only for mechanical tasks off the critical path, never for fixes | `routing.py` |
| **Agents stay in their tier** | A task goes only to an idle agent of its own tier; nothing queues behind a busy one | `dispatch` |
| **Cost caps are enforced** | Warns at 70% of the cap; over the cap it stops optional work | `sl budget` |
| **Every run is measured** | Each agent's tokens, cost and time are captured automatically and feed the next run's predictions | capture hooks, `~/.superlite/history.jsonl` |
| **No agent spawns agents** | Subagents get explicit tool lists with no `Agent` | agent definitions |

---
