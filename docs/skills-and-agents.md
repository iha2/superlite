# Skills and agents

- `using-superlite` (injected at session start)
- `brainstorming`
- `writing-plans`
- `subagent-driven-development`
- `test-driven-development`
- `systematic-debugging`
- `feature-prototype` (feature mode: how to cut the prototype's container, by target kind)
- `verification-before-completion`
- `finishing-a-development-branch`
- `mvp-prototype` (MVP mode: the clickable prototype and its feedback)

Agents (`agents/`):

- `implementer` (sonnet, medium): builds one task, then stays idle for reuse.
- `implementer-haiku` (haiku, high): builds one `Kind: mechanical` task (fully specified, small, fast tests) exactly as written; stops rather than guesses.
- `package-reviewer` (sonnet, medium): reviews one committed task or one package, then fixes its own findings with regression tests.
- `final-reviewer` (opus, high): a fresh one-turn reviewer that reads only the review packet (Read, Grep, Glob). It returns findings, each with a fix tier, and never fixes.
- `fixer-sonnet` (sonnet, medium): tier 1, cause known (including a fully specified change), fix confined to one module.
- `fixer-sonnet-high` (sonnet, high): tier 2, cause known but the fix spans modules or a seam, or a known probe class.
- `fixer-opus` (opus, high): tier 3, cause unknown (it diagnoses), or a lower tier failed twice.
