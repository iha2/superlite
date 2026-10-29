# superlite docs

The README covers what superlite is, the results, and how to install and use it. These pages cover how it works.

| Page | What it covers |
|---|---|
| [How a run works](how-a-run-works.md) | The flow from request to a finished, measured build |
| [Sessions](sessions.md) | Per-chat run state: layout, lifecycle, resolution, and how sessions relate to flows and worktrees (mermaid) |
| [Skills and agents](skills-and-agents.md) | The skills, and the seven agents with their model and effort |
| [Hooks](hooks.md) | What each hook blocks, rewrites or records, and when it is active |
| [Tools](tools.md) | Every `sl` tool, one honest line each |
| [Config](config.md) | `models.json`, `predictor.json`, `routing.json`, `gitignore.json`, `testprobe.json` |
| [Language](language.md) | Any project language; what is Python-only |
| [How the rules are enforced](gates.md) | The gates that hold the rules in place with code |
| [Example review plan](example-review-plan.md) | What a human orchestrator is shown before approving a build |
| [MVP flow](mvp-flow.md) | Design of MVP mode: feature plan, clickable prototype, then the normal build |
| [Feature flow](feature-flow.md) | Feature mode on existing code: research, a validated plan, a prototype you try in its own worktree, then the integration build; and how superlite suggests a flow |
| [Benchmark report](benchmark-report.md) | The full comparison table and observations |
| [Current architecture](specs/2026-10-19-superlite-architecture-current.md) | The whole system on one page, with the model for each role and what is still being built |
| [superlite vs superpowers](specs/2026-10-19-superlite-vs-superpowers.md) | Detailed comparison with diagrams and the benchmark numbers |
| [Specs](specs/) | Design documents: architecture, flows, language adapters, the cost baseline, the coordinator-model decision, [implementer batch objective](specs/2026-10-25-implementer-batch-objective.md) |
| [Plans](plans/) | Implementation plans, including implementer batches and the cost-parity and coordinator-health plans |
