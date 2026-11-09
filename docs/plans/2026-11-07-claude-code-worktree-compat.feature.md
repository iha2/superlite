# Feature plan: superlite sessions wrap Claude Code worktrees

Status: draft, awaiting human approval. Orchestrator: human.
Target codebase: the superlite plugin (`iha2/superlite`, v3.2.0), not this vault. Research read the installed copy at `~/.claude/plugins/cache/superlite-dev/superlite/3.2.0`.

## Goal and who it is for

A superlite user running inside a Claude Code worktree (desktop app worktree session, `claude --worktree`, `EnterWorktree`, or a subagent with `isolation: worktree`) gets a superlite session that **wraps that worktree**: it adopts the worktree and its branch as-is instead of cutting a second one, never deletes what the harness owns, and hands cleanup back to the harness. Outside a harness worktree, behaviour is unchanged.

## Design principle (set by the user, 2026-11-07)

When running under Claude Code, **prefer the harness's worktree abstractions for everything superlite needs a worktree for**: `EnterWorktree` / `ExitWorktree` for the session, feature and integration worktrees; `isolation: worktree` for parallel implementers and fixers; the desktop app's worktree sessions and `sync_with_base_branch` / `clean_up_worktrees` for sync and cleanup. Superlite's own `git worktree add/remove` paths remain only as the fallback for other harnesses (Codex, Cursor, Gemini, and so on) or when a Claude Code tool is unavailable. Superlite adds the session registry, branch/base bookkeeping and HUD on top; it does not duplicate what the harness does.

## What the user will see or do differently

- `superlite-cli session ensure --slug <topic>` run inside a harness worktree prints a `SESSION worktree=<that path> ... branch=<harness branch>` line. No `superlite/sessions/<slug>` branch and no `.superlite/worktrees/sessions/<slug>` worktree is created.
- The session HUD line names the harness branch (for example `worktree-foo`) and marks it harness-owned.
- Started from the hub with the harness not yet in a worktree, the flow no longer instructs a bare `git worktree add`. It tells the agent to use `EnterWorktree` (where available) and then run `ensure`.
- `session close` / `discard` / shipping in an adopted worktree: never `git worktree remove`, never `branch -D`; the finish message says to use `ExitWorktree` (keep or remove) instead.
- `proto start` and the integration worktree stay superlite-cut (they are extra, nested sandboxes), but `proto discard` / `session close` never touch the adopted worktree.
- Docs and commands stop saying "move the agent root" without a mechanism; they say "already there" for adopted worktrees and "use EnterWorktree" otherwise.

## Acceptance checks

1. In a linked worktree under `<hub>/.claude/worktrees/<name>` on branch `worktree-<name>`, `session ensure --slug x` registers it in place: row has `owned:False`, `adopted_branch:True`, correct `base` and `base_sha` (merge-base with the default base), and `git worktree list` and `git branch` are unchanged.
2. Same, for a worktree outside the repo tree and for a detached-HEAD worktree (registers; branch shown as detached; close never deletes anything).
3. `session close` and `session discard` on an adopted worktree remove no worktree and delete no branch, and say to use `ExitWorktree`. The harness branch survives.
4. From the hub with another session open, `ensure` does not split into a second superlite worktree when the harness has isolated the session (flag or detection); the existing move-out and `ASK` flows still work when it has not.
5. A subagent with `isolation: worktree` that runs `ensure` registers its worktree as a child of the parent session, not as a new top-level session (or is explicitly skipped, per decision D4).
6. HUD, `finish` (`mark_run`), `sync` and `exclusive` all resolve the right row for an adopted worktree.
7. Docs and commands (`commands/*.md`, `AGENTS.md`, `docs/sessions.md`, `docs/feature-flow.md`, `skills/shipping`, `skills/swarm`) agree: no instruction to hand-run `git worktree add` for the session worktree; "move the agent root" replaced.
8. Under Claude Code, the docs, commands and shipping skill instruct `EnterWorktree` / `ExitWorktree` first for session, feature and integration worktrees, and `git worktree add` appears only as a labelled fallback for other harnesses. With the harness absent, the old path still works unchanged.
9. Write-capable agents run with `isolation: worktree` under Claude Code (D7) and their results land through the harness; with isolation off, behaviour is unchanged.
10. Every worktree superlite cuts (parallel session, proto sandbox, integration, merge checkout) lands under `<hub>/.claude/worktrees/`; the hub reports no untracked files for it, `git stash -u` leaves it alone, `owned:true` rows there are closable by superlite, and `EnterWorktree path=` accepts it from a pinned agent. Sessions registered at old `.superlite/worktrees/...` paths still close and discard correctly.
11. For each home (A hub, B adopted harness worktree, C superlite-cut worktree) the full lifecycle works and is tested: create, stop/park, resume, close, discard. In A, WIP is stashed under a slug tag and restored by oid with branch switches; in B and C no stash is used and WIP stays in the worktree; stopping a dirty worktree never removes it; moving A to C via `split_move` keeps WIP; two parked sessions' stashes never cross.
12. All existing tests in `tests/scripts/` pass; `test_line_grammar.py` still accepts every emitted line.

## Code map (from research)

Sources: two read-only agent reports of the cached plugin; the docs agent used targeted greps, so details outside the cited lines are unverified.

- CLI: `skills/swarm/scripts/session.py` re-exports `session_registry.py` (helpers, registry `sessions.json` v2 at hub `.superlite/`), `session_ensure.py` (ensure/create/close), `session_switch.py` (use/discard/split/status), `session_sync.py`, `session_hud.py`.
- **Existing hook point:** `_register_linked` (`session_ensure.py:219-231`) already adopts an unregistered linked worktree in place (`owned:False`, branch = current branch, `hide_superlite`). Trigger at line 163. `_ensure_branch` / `register_in_place` (181-251) adopt a named branch with `adopted_branch:True`.
- **Gaps to close:**
  - `_register_linked` sets `owned:False` but not `adopted_branch`, so `discard` / `close` may still `branch -D` a harness branch (`session_switch.py:149-153`, `session_ensure.py:582-584`).
  - `base` comes from `base_now` (`reg.base` or `main`), not the harness branch's real parent.
  - `_go_parallel` (`session_ensure.py:296-313`) and `split_move` (`session_registry.py:523-587`) cut `superlite/sessions/<slug>` plus `.superlite/worktrees/sessions/<slug>` even when the harness already isolated the session; `_move_gate` refuses when the harness holds `main`.
  - `fresh_base` (`session_ensure.py:118,124`) treats a Claude-created hub branch as the session base; `slug_from_branch` turns `worktree-foo` into slug `worktree-foo`.
  - `session_hud.row_for` gives no row (no HUD line) for an unregistered nested worktree (`session_hud.py:69-102`).
  - Docs tell the agent to run `git worktree add -b superlite/<topic> .superlite/worktrees/<topic> <base>`: `commands/feature.md:20`, `AGENTS.md:18`, `skills/swarm/SKILL.md:19`, `.cursor/rules/feature-worktrees.mdc:10`; shipping removes worktrees via `git worktree remove` (`skills/shipping/SKILL.md:44-61`).
  - "Move the agent root" (`commands/{mvp,debug,feature,small}.md`, `docs/sessions.md:84,127`) has no mechanism.
  - Agents (`agents/*.md`) have no `isolation`; the edit guard (`hooks/_edits`) and `capture-agent-*` walk up to the nearest run, so an isolated subagent resolves a different `.superlite/`.
- Detection primitives already there: `state.hub_root` (`state.py:27-35`, works from any worktree via `git-common-dir`), `worktree_map`/`branch_owner` (skip detached), hook cwd resolution `hooks/_preamble:194-215` (`CLAUDE_PROJECT_DIR`, then cwd, `up_to_run`). No `CLAUDE_WORKTREE*` env var is used; none was found.
- Do not change: `SESSION` / `REDIRECT` / `ASK` line grammar (`test_line_grammar.py`), `STATIONARY` set, the registry schema version (add fields only), ownership gate `owned_worktree` (`session_registry.py:460-465`).
- Tests to extend: `tests/scripts/test_session_ensure.py:181` (unregistered linked worktree), `test_session.py`, `test_session_moveout.py`, `test_session_run_state.py:180`, `test_hooks_cwd_first.py`, `test_past_findings.py`. Run with the repo's own test command (`superlite-cli run_tests`).
- Toolchain: Python scripts under `skills/swarm/scripts`, pytest in `tests/`, `uv.lock`.

## Decisions (recommended; change any on approval)

- D1. **Detect, do not require.** A harness worktree is any linked worktree (`.git` is a file) that is not superlite-owned; path under `.claude/worktrees/` is a hint, not a requirement (desktop app and `--worktree` may place it elsewhere). Detached HEAD is supported.
- D2. **Adopt, never own.** Adopted rows get `owned:False` and `adopted_branch:True` always; superlite never removes the worktree or deletes the branch. Cleanup is `ExitWorktree`.
- D3. **Real base.** Record `base` / `base_sha` from the merge-base of the adopted branch with the default base; fall back to `reg.base`.
- D4. **Isolated subagents:** register as children of the parent session (`parent` field), no HUD line change; do not auto-run `ensure` from agents. Smallest option; revisit if needed.
- D5. **Harness-first creation (revised).** Under Claude Code, the proto sandbox, the integration worktree and the session worktree are created with `EnterWorktree` (and left with `ExitWorktree`), then registered by `session ensure` / `proto start --adopt`. `superlite-cli` only registers and records; it runs `git worktree add` itself only when not under Claude Code or when `EnterWorktree` is unavailable (fallback, current behaviour). Detect the harness via `CLAUDE_CODE` / `CLAUDE_PLUGIN_ROOT` (already used by the hooks) plus the available tools. The integration "dedicated worktree" rule becomes: reuse the harness worktree when it is dedicated to this feature, else `EnterWorktree` a new one.
- D7. **Implementers and fixers.** Add `isolation: worktree` to the write-capable agents (`implementer*`, `fixer-*`, `package-reviewer`) when running under Claude Code and the plan's task graph marks tasks as file-parallel; otherwise keep the shared checkout plus `exclusive.py`. Results come back through the harness's worktree merge/keep, not a bespoke one. Needs a prototype to confirm how `ensure` and the edit guard resolve in an isolated subagent worktree.
- D8. **Cleanup and sync.** Under Claude Code, shipping tells the agent to call `ExitWorktree` (keep or remove) and, in the desktop app, `sync_with_base_branch` / `clean_up_worktrees`, instead of `git worktree remove` and `session sync`. `session sync` stays as the fallback.
- D6. **Build target.** The build runs in a clone of `iha2/superlite` (on its own session/worktree), not in this vault. Needs a clone path from you, or I clone to a scratch location.

## How Claude Code manages worktrees (reference)

Source: the tool descriptions loaded in this session (`EnterWorktree`, `ExitWorktree`, the desktop app's `sync_with_base_branch`, `clean_up_worktrees`, `discard_kept_worktree`, and the Agent tool's `isolation: "worktree"`). Anything marked **unverified** comes from general knowledge, not those descriptions, and must be confirmed in the prototype.

### The four ways a worktree appears

| Path in | Who creates it | Where and which branch | Who removes it |
|---|---|---|---|
| `EnterWorktree` with `name` | The agent, mid-session | `.claude/worktrees/<name>`, new branch. Base from setting `worktree.baseRef`: `fresh` (default) = origin/<default-branch>, `head` = current local HEAD | `ExitWorktree` `keep` or `remove`; otherwise prompted on session exit |
| `EnterWorktree` with `path` | Someone else (for example `git worktree add`) | Any path in `git worktree list`; a pinned agent may only switch to one under `.claude/worktrees/` | `ExitWorktree` never removes it; `keep` only returns to the launch directory |
| Subagent `isolation: "worktree"` | The harness, per subagent | Its own worktree (path and branch name **unverified**; a bench fixture shows branches named `worktree-agent-<id>`) | Auto-cleaned if the subagent changed nothing; otherwise kept for the parent |
| Desktop app Code session | The app, per session | App-managed worktree on a session branch; the base is the open PR's base or the branch it was cut from | App: archive, `clean_up_worktrees` (inactive 30/60/90 days; branches and conversations stay), `discard_kept_worktree` (archived sessions with uncommitted work) |
| `claude --worktree` (CLI flag) | The CLI at launch | **Unverified**: expected `.claude/worktrees/<name>`; prompts keep or remove on exit | CLI prompt on exit |

### Lifecycle (mermaid)

```mermaid
stateDiagram-v2
    [*] --> Hub: session starts in main checkout
    Hub --> Worktree: EnterWorktree(name)<br/>.claude/worktrees/NAME, new branch<br/>base = worktree.baseRef
    Hub --> Worktree: EnterWorktree(path)<br/>existing worktree from git worktree list
    [*] --> Worktree: desktop app session / claude --worktree<br/>(harness already placed the session)
    Worktree --> Hub: ExitWorktree(keep)<br/>dir and branch stay
    Worktree --> Removed: ExitWorktree(remove)<br/>refuses if uncommitted or unmerged<br/>unless discard_changes=true
    Worktree --> Worktree: sync_with_base_branch<br/>(desktop app fetches origin and merges base)
    Worktree --> Kept: session archived with uncommitted work
    Worktree --> Idle: no activity 30/60/90 days
    Idle --> Pruned: clean_up_worktrees<br/>worktree removed, branch + session stay
    Kept --> Removed: discard_kept_worktree<br/>(user approves, changes lost)
    Pruned --> Worktree: session resumed, branch checked out again
    Removed --> [*]
```

### Ownership rules that constrain superlite (mermaid)

```mermaid
flowchart TD
    A[Worktree exists] --> B{Created by EnterWorktree<br/>in THIS session?}
    B -- yes --> C[ExitWorktree may keep or remove it]
    B -- no --> D{Entered via EnterWorktree path=?}
    D -- yes --> E[ExitWorktree keep only:<br/>returns to launch dir, never removes]
    D -- no --> F[Not touched by ExitWorktree:<br/>no-op if no worktree session is active]
    A --> G{Created by the desktop app?}
    G -- yes --> H[App owns lifecycle:<br/>archive, clean_up_worktrees,<br/>discard_kept_worktree, sync_with_base_branch]
    A --> I{Created by superlite git worktree add?}
    I -- yes --> J[Only superlite session close/discard<br/>may remove it; harness will not]
```

### Where superlite plugs in (mermaid)

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant CC as Claude Code harness
    participant SL as superlite-cli (registry)
    participant WT as Worktree
    U->>CC: /superlite:feature <request>
    alt harness already put the session in a worktree (desktop app, --worktree, EnterWorktree earlier)
        CC->>WT: cwd = existing worktree
        CC->>SL: session ensure --slug topic
        SL->>SL: register in place: owned=false, adopted_branch=true, base = merge-base
    else main checkout, Claude Code available
        CC->>WT: EnterWorktree(name=topic)  [allowed: project instructions name worktrees]
        CC->>SL: session ensure --slug topic
        SL->>SL: register in place (same as above)
    else other harness or EnterWorktree unavailable
        SL->>WT: git worktree add (current behaviour, fallback)
        SL->>SL: register: owned=true
    end
    CC->>CC: build waves; write agents use isolation: worktree
    CC->>SL: finish / report
    alt harness-owned
        CC->>WT: ExitWorktree(keep or remove) / desktop cleanup
        SL->>SL: mark_run finished, never git worktree remove
    else superlite-owned
        SL->>WT: session close removes worktree (existing rules)
    end
```

### Facts that change the design

1. **`EnterWorktree` is gated.** It may be used only when the user says "worktree" or project instructions (CLAUDE.md, memory) direct it. Superlite's `AGENTS.md` / `CLAUDE.md` and flow commands must therefore contain an explicit "use a worktree" instruction under Claude Code, or the agent will (correctly) refuse to call it. See D9.
2. **`ExitWorktree` only handles worktrees `EnterWorktree` made this session.** A superlite-cut or desktop-app worktree is invisible to it. Superlite must never tell the agent to `ExitWorktree` a worktree it did not enter that way; for the desktop app, cleanup is the app's job.
3. **Pinned agents are limited.** A subagent with a pinned cwd can only `EnterWorktree path=` into worktrees under `.claude/worktrees/` of the same repository. Superlite's `.superlite/worktrees/...` and `.superlite/sandboxes/...` are out of reach for such agents. See D10.
4. **`sync_with_base_branch` needs a worktree of its own** and refuses sessions without one, uncommitted changes, content-filtered files and in-progress git operations. It replaces `session sync` only for harness-owned worktrees on the desktop app.
5. **Base branch semantics differ.** `worktree.baseRef=fresh` bases on origin's default branch, not local HEAD, so a harness worktree can start behind local work. Superlite records `base` from the merge-base (D3) and must not assume local `main`.
6. **Cleanup keeps branches.** `clean_up_worktrees` removes the directory but keeps the branch and session, and a resumed session re-checks the branch out. Superlite's registry must treat a missing worktree directory with a surviving branch as "pruned, resumable", not as corruption.

Further decisions from this section:

- D9. Add an explicit Claude-Code-only instruction block in `AGENTS.md` and `CLAUDE.md` (and the flow commands) authorising `EnterWorktree` for session, feature and integration work, so the gate is satisfied by project instructions.
- D10. **Default location (decided by the user, 2026-11-07).** Every worktree superlite cuts itself (parallel session, proto sandbox, integration, merge checkout) goes under `<hub>/.claude/worktrees/<name>`, on every harness, not only as a Claude Code fallback. This makes `EnterWorktree path=` work for the lead and for pinned subagents, and puts all worktrees where the harness expects them. Naming: `sessions-<slug>`, `<topic>-proto`, `<topic>`, `merge-<base>`; branch names stay `superlite/...`. State (`sessions.json`, `session-store/`, run files) stays under `.superlite/`. Existing `.superlite/worktrees/...` and `.superlite/sandboxes/...` rows keep working: the registry stores absolute paths, so old sessions are not moved, and only new worktrees use the new location.
  - Consequences to handle: add `.claude/worktrees` to the common-dir `info/exclude` via `hide_superlite` (otherwise `git stash -u` and the untracked gate sweep it up); treat it as stationary like `.superlite/worktrees` in `STATIONARY` and `park_active`; the `owned_worktree` gate (`session_registry.py:460-465`) must also accept paths under `.claude/worktrees/` when the row says `owned:true`, because they are no longer recognisable by path alone; `hooks/_kill:84` already refuses to pattern-kill that directory.
  - Because these worktrees live where the harness looks, `ExitWorktree` still ignores them (not made by `EnterWorktree` this session), and the desktop `clean_up_worktrees` only touches worktrees the app made, so superlite's `owned:true` rows must not be assumed prunable by either. Ownership stays recorded in the registry.
- D11. Treat `Pruned` as a registry state: worktree directory missing, branch present, row kept and marked `pruned`; `ensure` on resume re-adopts instead of recreating a session.

## Session modes: stop, park, branch and stash flow (same flow, three homes)

The existing serial model stays first-class. A session is a branch plus parked state; it may live in the hub (branch switch and stash) or in a worktree. Stopping, parking, resuming and closing behave the same way from the user's side in all three homes. Source for the current behaviour: `enter_session`, `park_active`, `_close_host`, `_close_owned`, `_discard_on_host`, `split_move` (`session_ensure.py`, `session_registry.py`, `session_switch.py`); stashes are tagged `superlite-session:<slug>` and matched by recorded `stash_oid`, and git's stash is shared across all worktrees of the repo.

| Home | Created by | Stop / park | Resume | Close | Discard |
|---|---|---|---|---|---|
| **A. Hub (serial)** | `ensure`: `git switch -c superlite/sessions/<slug>` in the main checkout | `park_active`: `stash -u` tagged with the slug, run files moved to `.superlite/session-store/<slug>/`, hub switched to the next branch or base | `enter_session`: `git switch <branch>`, restore run files, `stash apply` by recorded oid | `_close_host`: switch hub back to base; branch kept unless merged | `_discard_on_host`: `reset --hard`, `clean -fd`, `switch --detach` (hub only, never a worktree) |
| **B. Adopted harness worktree** | Harness (`EnterWorktree`, desktop app, `--worktree`) | Nothing to park: the worktree stays on disk; run state is its own `.superlite/`. Row set to `parked` or `finished`. WIP stays in the worktree (no stash needed) | `EnterWorktree path=` or reopen the app session; `ensure` re-adopts (or re-adopts after `Pruned`, D11) | Mark finished; never remove the worktree or delete the branch (D2). Hand-off: `ExitWorktree` or the app | Same: unregister only; leave directory and branch |
| **C. Superlite-cut worktree** | `ensure` or `split_move` under `.claude/worktrees/` (D10) | Worktree stays; if the user wants the hub back, `split_move` stashes (`stash -u`), switches the hub to base, and cuts the worktree | `EnterWorktree path=` into it, or `session use` returns a `REDIRECT worktree=` | `_close_owned`: `git worktree remove` (no `--force`), keep a branch with unmerged commits | `_discard`: `remove --force`, `prune`, `branch -D` unless `adopted_branch` |

### Stop, park and resume (mermaid)

```mermaid
stateDiagram-v2
    [*] --> Active: ensure
    state Active {
        [*] --> InHub: home A
        [*] --> InAdopted: home B (harness worktree)
        [*] --> InCut: home C (superlite worktree)
    }
    Active --> Parked: stop or switch session
    note right of Parked
        A: stash -u tagged superlite-session:SLUG
           + run files to session-store/SLUG
        B and C: worktree stays on disk,
           WIP stays in it, no stash
    end note
    Parked --> Active: resume (A: switch + stash apply by oid;<br/>B and C: EnterWorktree path= or REDIRECT)
    Active --> Finished: finish (mark_run finished)
    Parked --> Finished: finish
    Finished --> Closed: session close
    Finished --> Discarded: session discard
    Closed --> [*]: A: hub back to base<br/>B: unregister only<br/>C: git worktree remove
    Discarded --> [*]: A: reset --hard in hub<br/>B: unregister only<br/>C: remove --force + branch -D
```

### Moving between homes (mermaid)

```mermaid
flowchart LR
    A[Home A: hub, branch + stash] -- "split_move: stash -u, switch hub to base,<br/>cut worktree, stash apply" --> C[Home C: worktree under .claude/worktrees]
    A -- "harness already isolated the session:<br/>adopt, no split" --> B[Home B: adopted harness worktree]
    C -- "close: remove worktree,<br/>keep branch" --> A2[Hub on base, branch kept]
    B -- "close: unregister only" --> B2[Harness keeps worktree and branch]
    B -- "ExitWorktree keep/remove or app cleanup" --> B3[Harness-managed end of life]
```

### Rules that keep the three homes consistent

1. One `ensure` decision tree, in this order: already registered here, then unregistered linked worktree (home B), then hub busy and no harness isolation (home C via `split_move`), else hub (home A). Today's order, plus the harness check from D1.
2. **Branch creation:** home A and C create `superlite/sessions/<slug>`; home B keeps the harness's branch and never renames it.
3. **Stash** only ever happens when a branch switch in the hub would carry WIP (homes A and the A-to-C move). Homes B and C never stash for parking. Because the stash list is shared across worktrees, every stash stays slug-tagged and is dropped only by recorded oid (existing behaviour, test-covered).
4. **Stopping a worktree** (end of the chat, `/superlite:stop`, app archive) never loses work: dirty worktrees are kept (existing keep rule in `_close_owned` and shipping), clean owned ones may be removed, harness-owned ones are left to the harness.
5. **Untracked gate** (`ASK untracked`, exit 4) still runs before any park in the hub, and `.claude/worktrees` is excluded from it (D10).
6. `discard` on the hub never runs while `row.worktree` is not the hub (existing safeguard `same(row.worktree, host)`); the new tests assert it for adopted rows.

New decision:

- D12. Do not remove or weaken the hub branch-and-stash path. Homes A, B and C ship together behind one `ensure`, and the acceptance checks below run each lifecycle (create, stop/park, resume, close, discard) in all three homes.

## Prototype scope

Smallest runnable slice, in a sandbox worktree of the superlite repo: ensure-in-harness-worktree adoption plus close/discard safety (checks 1, 2, 3, 6) driven by a temp git repo test that creates a worktree with `git worktree add .claude/worktrees/x -b worktree-x`, runs `ensure`, `status`, `close`. L1: real git, no stubs for git. No doc changes in the prototype.

## Integration build outline (one task per touched module, existing interfaces frozen)

1. `session_registry.py`: harness-worktree detection helper, merge-base `base` helper, `adopted_branch` default for adopted rows.
2. `session_ensure.py`: `_register_linked` sets adoption fields and real base; `_ensure` skips `_go_parallel` / move-out when already in an isolated harness worktree; `close` message.
3. `session_switch.py`: `_discard` / `close` guard (never delete adopted branch or worktree); message pointing to `ExitWorktree`.
4. `session_hud.py` and `session_sync.py`: row resolution and base for adopted worktrees; HUD marks harness-owned.
5. Hooks (`_preamble`, `capture-agent-*`): confirm subagent cwd resolution for isolated worktrees; add the parent link (D4) if cheap.
6. Docs/commands: replace `git worktree add` instructions and "move the agent root"; document EnterWorktree / ExitWorktree / `--worktree` behaviour in `docs/sessions.md` and `docs/feature-flow.md`.
7. Tests: new cases next to `test_session_ensure.py:181` and the files listed above, including detached and out-of-tree worktrees.
8. Seam checks: adopted-worktree session plus `proto start` plus `finish`; every existing test still passes.

## Open risks

- D10 moves superlite-cut worktrees into `.claude/worktrees/`, a directory other tools also write to. Name collisions (`sessions-<slug>` versus a harness-chosen name) and the untracked/stash sweep are the main risks; both get tests in the prototype. Old `.superlite/worktrees` paths must stay valid.

- No Claude Code worktree has been exercised yet; behaviour of `EnterWorktree` (branch name, path, cwd change, `.git` file) is assumed from the research and must be confirmed in the prototype.
- The research agents read only targeted lines; the full file contents of hooks and shipping flow need a read during the prototype.
