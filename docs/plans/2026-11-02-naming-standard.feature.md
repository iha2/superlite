# Feature: One name for branch, session, worktree and PR, and a guard that keeps worktrees in `.superlite`

## Goal

A line of work gets **one name** (its slug) and every artifact derived from it carries that name. Today the same feature is called `naming-standard` (session), `superlite/sessions/naming-standard` (branch), `superlite/<topic>` + `.superlite/worktrees/<topic>` + session `<topic>-int` (feature integration), `superlite/<topic>-proto` (prototype), `claude/<random>` (harness worktrees) and an unconstrained PR title. For whoever runs several lines of work at once (human or agent), this makes `git branch`, `git worktree list`, `superlite-cli session status` and the PR list read as the same list.

Second part (added after review): worktrees are also showing up in `.claude/worktrees/` (harness-made: `eloquent-shirley-…`, `pr29`, `run-graph-…`), outside superlite's registry. While a superlite session is on, **every new worktree must be a session, and every session lives under `.superlite/`**. A guard enforces it.

## The standard

**No `/` anywhere in a name.** A `/` in a branch makes git nest refs (`superlite/x` blocks `superlite/x/y`) and tools that derive a worktree folder from the branch name get confused. Names use `-` as the only separator.

One **name** per line of work, rendered from a template with the slug, and used verbatim for every artifact:

| Artifact | Name |
|---|---|
| Slug | `<slug>`: `a-z`, `0-9`, `-` (existing rule) |
| Name | template applied to the slug; **default `superlite-{slug}`** |
| Session | `<slug>` (registry key; the row records the rendered name) |
| Branch | `<name>` |
| Worktree | `.superlite/worktrees/<name>` (the main checkout hosts one session on that same branch) |
| PR head | `<name>` |
| PR title | `<name>: <summary>` |
| Prototype (derived, not a line of work) | branch and sandbox `<name>-proto`, at `.superlite/sandboxes/<name>-proto` |

**Changing the convention.** The project's `superlite.json` gets a new key `naming` (`"naming": {"template": "{slug}"}`), documented in `docs/config.md`. Rules, checked on load (bad value exits 2 and names the file, like the other keys): the template must contain `{slug}` exactly once, and the rest may only be `a-z`, `A-Z`, `0-9`, `-` (no `/`, `.`, `_`, spaces). Example for Jira: keep `{slug}` and make the slug the ticket, `superlite-cli session ensure --slug proj-123-csv-export` gives branch, worktree and PR title starting `proj-123-csv-export`; or set the template to `proj-{slug}` / `{slug}` as the team prefers. The user can ask for a convention in chat; the agent writes it into `superlite.json` and does not guess one.

**Legacy names.** Existing rows and branches named `superlite/sessions/<slug>` keep working: `session` commands read the branch recorded in the row (as `branch_for` already allows "the branch recorded for the slug"), and `slug_from_branch` still understands the old prefix. New sessions use the new convention; nothing is renamed automatically, and a legacy row shows `names:drift`. Worktrees under `.superlite/worktrees/sessions/<slug>` are likewise left in place.

The integration build of a feature no longer registers a second session `<topic>-int`: it runs in the feature's own session, the one `session ensure --slug <topic>` opened at the start of the flow.

Harness-made names (`claude/<random>`, `cursor/...`) are not renamed behind the user's back. They are adopted with `session ensure --branch B` and shown as drift.

## The guard

New `hooks/_worktrees` (`guard_worktrees`), sourced and run by `hooks/pre-tool` after `guard_kill`. It only acts while a superlite session is on (the hook's `proj` has `.superlite/` with a session registry row or an active run); outside that it exits 0 (fail open, like the other guards).

Denied (exit 2, reason on stderr, pointing at the right command):

- `Bash`/`PowerShell` `git worktree add …` (also `git -C … worktree add`) when the target path is not under `<main>/.superlite/worktrees/` (any depth, so legacy `sessions/` folders still pass) or `<main>/.superlite/sandboxes/`. Message: `use superlite-cli session ensure --slug <slug>` (or `session create --slug <slug> --worktree`).
- The `EnterWorktree` tool (it creates `.claude/worktrees/<name>`), unless it is entering an existing worktree under `.superlite/`. Needs `EnterWorktree` added to the `PreToolUse` matcher in `hooks/hooks.json` (and the Cursor hooks file if it has the same tool).
- Allowed: the CLI's own worktrees (it runs git itself, not through the tool hook), `git worktree list|remove|prune|move` within `.superlite/`, and `SUPERLITE_NO_WORKTREE_GUARD=1` as an opt-out (same style as the other opt-outs).

Not enforceable by a hook: a worktree the Claude desktop app makes by itself outside any tool call (the app's own "new session in worktree" button). For those, `session status` shows `location:drift` (the row, or an unregistered `git worktree list` entry, sits outside `.superlite/`) and `session ensure --branch B` adopts the branch into the standard location. State this limit in `docs/hooks.md`.

## What the user will see or do differently

Target kind: `cli` + docs (no UI).

- `superlite-cli session names --slug S [--summary "text"]` prints the derived names, one `key=value` per line: `slug`, `name`, `branch`, `worktree`, `pr_title`. Single source of truth; flows and `superlite:shipping` read it instead of composing names by hand.
- `superlite-cli session status` marks a row `names:drift` when its branch is not the rendered `<name>` (a legacy `superlite/sessions/*` or adopted harness branch) and prints the standard branch beside it. Informational; exit code unchanged.
- Feature flow step 6 and the swarm one-feature-one-worktree rule cut the integration worktree with the standard names instead of `superlite/<topic>` / `worktrees/<topic>` / `<topic>-int`.
- `superlite:shipping` opens the PR with `pr_title` from `session names`, head branch unchanged.
- Any attempt to create a worktree outside `.superlite/` during a session is refused with the command to use instead; `session status` flags existing strays as `location:drift`.
- `docs/sessions.md` gains a short **Naming** section holding the table above; `AGENTS.md` gets one rule line.

## Acceptance checks

1. `superlite-cli session names --slug export --summary "Add CSV export"` prints `branch=superlite-export`, `worktree=…/.superlite/worktrees/superlite-export`, `pr_title=superlite-export: Add CSV export`.
2. With `"naming": {"template": "{slug}"}` in `superlite.json` the same call gives `branch=export`; a template with `/`, with no `{slug}` or with two exits 2. An invalid slug (`Bad Slug`, `host`) is rejected with the same message `session create` gives.
3. `session status` on a session adopted from `claude/foo-123` shows `names:drift`; on a session made by `ensure` it shows none.
4. `grep -rnE "superlite/<topic>[^-]|worktrees/<topic>|<topic>-int" commands skills docs/*.md AGENTS.md` returns nothing (the prototype's `<topic>-proto` stays).
5. `superlite:shipping` text tells the agent to take the PR title from `session names`.
6. With a session on, `git worktree add .claude/worktrees/x -b y` through the Bash tool exits 2 naming `session ensure`; `git worktree add .superlite/worktrees/superlite-x -b superlite-x` is allowed; the `EnterWorktree` tool creating a new worktree is denied.
7. With no session on (no `.superlite/` registry), the same commands are allowed; `SUPERLITE_NO_WORKTREE_GUARD=1` allows them too.
8. `session status` shows `location:drift` for each worktree outside `.superlite/` (such as `.claude/worktrees/*`).
9. The full existing suite passes, including `test_session*.py` and `test_feature_flow.py`.

## Code map

- **Config:** `skills/swarm/scripts/project_config.py` (`KEYS`, `load`; add `naming` and its validation), `docs/config.md`.
- **Naming source:** `skills/swarm/scripts/session_registry.py` (`branch_for` becomes template-aware, `sessions_dir` becomes `.superlite/worktrees`, `validate_slug`, `slug_from_branch` keeps the legacy prefix). Add `names_for(root, slug, summary)` here. `session_ensure.py:118,124` and `docs/sessions.md` test `startswith("superlite/sessions/")`; they must accept both conventions.
- **CLI wiring:** `skills/swarm/scripts/session.py` (subcommands, status printer); `session_ensure.py` already builds `superlite/sessions/<slug>` through `branch_for`.
- **Text that hard-codes the old integration names:** `commands/feature.md:20`, `skills/swarm/SKILL.md:19`, `skills/drafting/SKILL.md:151`, `docs/feature-flow.md:84,93`, `docs/sessions.md:131`. `docs/tools.md:35` (`proto`) stays.
- **PR step:** `skills/shipping/SKILL.md:45` (`gh pr create` with a free title).
- **Guard host:** `hooks/pre-tool` (runs `_kill`, `_edits`, `_rewrite` in order; `_preamble` gives `input`, `proj`, `sd`, `jstr`, `jdecode`), `hooks/hooks.json` matcher, `tests/scripts/test_guard_matrix.py` + `fixtures/guard_matrix.json` (matrix of states x events; add rows), `test_hooks_bash32.py` (bash 3.2 and builtins-only rules apply to the new guard).
- **Text that tells agents to run `git worktree add` by hand:** `AGENTS.md:18`, `skills/swarm/SKILL.md:19`, `commands/feature.md:20`. They switch to `session ensure|create --worktree`.
- **Prototype names:** `skills/swarm/scripts/proto.py:151-152`; unchanged.
- **Tests:** `tests/scripts/test_session.py` (registry/CLI), `tests/scripts/test_feature_flow.py` (command and skill texts), `tests/scripts/test_doc_contract.py` (docs contract; check it does not pin the old strings).
- **Frozen interfaces:** the call signatures of `branch_for` and `sessions_dir` (the values change for new sessions only), `SESSION worktree=… slug=… branch=…` line, `sessions.json` row shape (only read, no new required field), `proto start` flags and paths, exit codes 0/2/3/4.
- **Conventions/toolchain:** Python scripts run through `superlite-cli`, pytest in `tests/scripts`, LF endings, `using-superlite` skill stays under 2,000 characters (not touched), `.githooks/commit-msg`.
- **UI component set:** none (CLI/docs only).

## Prototype scope

Smallest runnable cut: `names_for` + `session names` in a sandbox of the CLI, plus the `names:drift` marker in `session status`, tried from the terminal against acceptance checks 1-3. Text/doc edits are not prototyped; they are mechanical and land in the integration build. At most 3 tasks.

## Integration outline

1. `session_registry.py` + `session.py`: `names_for`, `session names`, drift marker (ported from the prototype).
2. Feature flow text: `commands/feature.md`, `skills/swarm/SKILL.md`, `skills/drafting/SKILL.md`, `docs/feature-flow.md`, `docs/sessions.md`, so integration runs in the feature's own session, branch and worktree.
3. `hooks/_worktrees` guard + `pre-tool`/`hooks.json` wiring + `session status` `location:drift` + `docs/hooks.md`; tests in the guard matrix.
4. `skills/shipping/SKILL.md` PR title step; `AGENTS.md` rule; `docs/sessions.md` Naming section; `docs/tools.md` row for `session names`.
5. Tests: new cases for checks 1-3 and a text check for 4-5; run the whole suite.

## Open points (defaults chosen, change at approval)

- **PR title form** `<name>: <summary>` (alternative: `[<slug>] <summary>`).
- **Prototype keeps the `-proto` suffix** rather than a second slug; it is a throwaway sandbox, not a line of work.
- **Default template** `superlite-{slug}` (flat, still namespaced). Alternative: bare `{slug}`.
- **No automatic migration** of legacy `superlite/sessions/*` branches and `worktrees/sessions/*` folders.
- **Harness branches are flagged, not renamed.** No `session rename` command in this feature.
- **Guard scope.** Active only while a superlite session is on; it does not touch worktrees made by the desktop app itself (flagged, not blocked).
- **Integration in the host checkout.** When the session lives in the main checkout, "one feature, one worktree" still wants a separate worktree for integration; the default is that `session ensure` already places the session where the existing move-out rules say, and the integration build uses that session's `SESSION worktree=` path with no second branch.
