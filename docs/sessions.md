# Sessions (worktrees + branches)

A **session** is one line of work: a branch `superlite/sessions/<slug>`, parked run state under `.superlite/session-store/<slug>/`, and optional WIP stash. By default many sessions share one **host** (the main checkout; saves space). A second worktree is only for **parallel** chats.

| Concept | Path / rule |
|---|---|
| Hub | Primary (main) checkout; holds `sessions.json` + `session-store/`. Every `sl session` command finds it from any worktree of the repo |
| Host | The main checkout itself: its active session's branch is checked out right there. (A registry that already has a separate host worktree, `.superlite/worktrees/sessions/host`, keeps it.) |
| Parallel worktree | `.superlite/worktrees/sessions/<slug>` |
| Branch | `superlite/sessions/<slug>` (or the branch recorded for the slug) — one checkout only |
| Slugs | `a-z`, `0-9`, `-`; `host` is reserved |
| Live run | Host’s `.superlite/` while that session is active |

## Why

Two chats in the same folder overwrite `.superlite/`. Sessions isolate via branches (serial) and worktrees (parallel).

```mermaid
flowchart LR
  subgraph serial["Serial — one host worktree"]
    A[session export] -->|use| B[session auth]
    B -->|use| A
  end
  subgraph parallel["Parallel — two worktrees"]
    C[chat 1 / export] --> W1[worktrees/sessions/export]
    D[chat 2 / auth] --> W2[worktrees/sessions/auth]
  end
```

## Switch rules (`sl session use`)

```mermaid
sequenceDiagram
  participant Use as session use --slug B
  participant Host as host worktree
  participant Store as session-store
  participant Git

  Use->>Git: branch B already live elsewhere?
  Git-->>Use: yes → REDIRECT path (exit 3)
  Use->>Store: park host .superlite → store/A
  Use->>Git: stash -u if dirty (superlite-session:A)
  Use->>Git: switch superlite/sessions/B
  Use->>Store: restore store/B → host .superlite
  Use->>Git: stash apply for B if any
```

1. Stash dirty project files (`superlite-session:<slug>`); the registry records the stash's object id, so `auth` never picks up `auth-api`'s stash.
2. Park run files (stash does **not** hold ignored `.superlite/`). Checkouts under `.superlite/sandboxes/` and `.superlite/worktrees/` never move.
3. Switch branch; restore store; apply the stash with `--index` (staged changes survive).
4. On a conflict the session stays active with `stash:yes`. Resolve the files, `git add` them, then `sl session use --slug S --continue`: it checks nothing is unmerged and drops the stash without re-applying it.
5. If the branch is already checked out in another worktree → **REDIRECT** (do not check it out here).

All registry writes take `.superlite/sessions.lock` and replace `sessions.json` atomically, so concurrent chats never lose each other's rows.

## Commands

| Command | Effect |
|---|---|
| `sl session ensure --slug S` | Idempotent flow entry; see [Auto-start](#auto-start). Last line `SESSION worktree=<path> slug=<s> branch=<b>`; exit 3 `REDIRECT`, exit 4 `ASK untracked` or `ASK move` |
| `sl session close --slug S [--keep-branch]` | End of flow: mark `S` finished, park it and put the host back on the base branch, remove a parallel worktree `ensure` cut |
| `sl session untracked [ask\|stash\|keep]` | Per-repo choice for untracked files (default `ask`); stored in the main checkout's `sessions.json` |
| `sl session create --slug S` | Branch on the host (the main checkout, or an older registry's host worktree); make active (an existing slug: use `use`; asks before stashing untracked files) |
| `sl session create --slug S --worktree` | Parallel worktree + new branch |
| `sl session use --slug S` | Park/stash/switch or REDIRECT |
| `sl session split --new S [--keep K]` | Parallel: move open hub run + cut new worktree. From a linked worktree (or when the base is busy elsewhere) the run stays where it is. Any failure rolls back run files, WIP and branches |
| `sl session sync --slug S [--untracked stash\|keep] [--continue\|--abort\|--later]` | Bring local main into `S`'s branch; see [Sync with main](#sync-with-main). Exit 5 `CONFLICT` |
| `sl session status [--json]` | List sessions, each with `run:open` or `run:finished` |
| `sl session discard --slug S [--keep-branch]` | Drop store/stash/branch; remove a parallel tree superlite cut. The active host session's uncommitted changes are discarded and the host is detached |

## Auto-start

Every flow (start, regular, mvp, feature, debug, small, review) runs `sl session ensure --slug <topic>` once the topic is known, before its first other `sl` call. Nobody types `sl session create`. Exit codes: 0 ok, 2 error, 3 `REDIRECT`, 4 `ASK`. `SUPERLITE_NO_AUTO_SESSION=1` prints `session: auto-start off` and changes nothing.

| Where it runs | What `ensure` does |
|---|---|
| A checkout whose session is open | Nothing; prints `session: <slug> already open here` |
| Main checkout, no active session or the active one is finished | `create` with the main checkout as host: branch `superlite/sessions/<topic>` from the current branch; a finished active session is parked first |
| Main checkout, active session still open | Moves the open session out (see [Move-out](#move-out)): asks `ASK move`, then it continues in `.superlite/worktrees/sessions/<old>` and the new topic takes the main checkout. Without a move the open session is untouched |
| Main checkout with a pre-existing run (`orchestrator.json`, no row) | Records it as the active open host session, then follows the row above |
| Unregistered linked worktree | Registers it where it is (`owned: false`) |
| Registry with a separate host worktree | Creates the session there and prints `REDIRECT` (exit 3) |

A taken slug gets `-2`, `-3`, and the slug it settled on is printed. The agent moves its root to the `SESSION worktree=<path>` line when that is not its folder.

**Run status.** Each `sessions.json` row has `run: "open" | "finished"` and `finished_at`. `ensure` and `create` set `open`; `sl finish` and `sl session close` mark `finished`. `status`, `use` and the HUD show `run:open` or `run:finished`; "run in progress" is shown only for an open run.

**Close.** `sl session close --slug S [--keep-branch]` ends a flow (shipping runs it from the main checkout): it marks `S` finished, parks its run files and switches the host back to the registry's base branch, and removes a parallel worktree that `ensure` cut.

**Untracked files** are stashed only with your OK, by `ensure`, `close`, `use` and `create` when it parks. `sl session untracked ask|stash|keep` sets the repo default; `--untracked stash|keep` overrides it per command. With `ask` the command changes nothing, prints `ASK untracked=<N> slug=<s>: <files>` and exits 4; the agent asks once (stash with the session, keep in the folder, always stash, always keep), runs `sl session untracked …` for an "always" answer, and reruns with `--untracked …`. `stash` names the files stashed. `keep` leaves them in the folder; if one would block the branch switch the command undoes everything and exits 2.

`sl orchestrator set` warns on stderr (`session: none for this checkout; run sl session ensure --slug <topic> first`) when the checkout has no session; it still exits 0.

## Move-out

When the main checkout holds an open session and a new flow starts, `ensure` does not touch it silently. It prints `ASK move slug=<s>: …` and exits 4; nothing changed. The agent asks "Move it" / "Not now"; on "Move it" it reruns `ensure` with `--move yes`. The open session moves to `.superlite/worktrees/sessions/<s>` (`session: moved <s> -> <path>`), its run files and WIP go with it, and any failure rolls back. The row records `base_sha` (the base branch tip when the session started), `moved_from` and `moved_at`. The main checkout's HUD then reads `superlite session: <s> moved to <path> — move the agent root there`.

## Sync with main

The HUD adds `| main:+N` when local main is N commits ahead of the session's `base_sha`, plus an offer line: `superlite sync: local <base> moved ahead by N commits since this …`. The agent offers the update in that session's own chat; nothing is synced automatically. `sl session sync --slug S` fast-forwards when it can and otherwise merges local main in (`session: <s> synced with main (fast-forward|merge)`). `--later` records the offer as declined (`needs-sync` stays in the HUD).

On a conflict it prints `CONFLICT <s>: <files>` and exits 5, HUD shows `| conflict`. Resolve the files and `git add` them, then `sl session sync --slug S --continue`; or `--abort` to restore the branch (`needs-sync`). Sync never runs `git fetch`, `push`, `pull` or `rebase`: it only uses the local main.

## Session HUD

Each session-start / user turn injects:

```text
superlite session: export | branch superlite/sessions/export | worktree …/host | active | stash:no | run:open
```

In the main checkout with no active session it reads `superlite session: none here (main checkout) | <N> sessions`.

```mermaid
flowchart LR
  turn[User turn] --> hook[cost-report / session-start]
  hook --> hud[session_hud.py]
  hud --> line["Inject superlite session: …"]
```

Opt out: `SUPERLITE_NO_SESSION_HUD=1` (independent of `SUPERLITE_NO_COST_REPORT`). Wired through `hooks/cost-report` and `hooks/session-start` via `session_hud.py`; it shows in a fresh session worktree before it has a `.superlite/`.

## Start menu

**Start something new**: `sl session ensure --slug <topic>` runs first (see [Auto-start](#auto-start)); follow its `SESSION` or `REDIRECT` line, and handle `ASK untracked` (exit 4). `split` and serial `use` stay manual tools for moving a run or switching by hand. On `REDIRECT`, move the agent root to the printed path before inventing.

## Feature flow

Prototype sandboxes stay `.superlite/sandboxes/<topic>-proto`. Feature integration stays `.superlite/worktrees/<topic>` (not under `sessions/`). See [Feature flow](feature-flow.md).

## Tests

`tests/scripts/test_session.py`, `tests/scripts/test_session_split.py`, `tests/scripts/test_session_moveout.py`, `tests/scripts/test_session_sync.py`.
