# Session move-out and sync with main (feature plan)

Round 2 of session auto-start, on the same branch and PR (iha2/superlite#25).

## Goal

1. **Keep the main checkout free for merging.** The main checkout hosts at most one session. When a second session starts while that one is open, both sessions move into their own worktrees and the main checkout goes back to `main`. That keeps `main` checked out where `git merge` happens.
2. **Keep sessions current with `main`.** When local `main` moves ahead, through your own `git pull` or a local merge, each session's chat is offered the new `main` and you decide. superlite never fetches from origin.

This is for anyone running more than one superlite session in a repo who merges finished work back into `main` locally.

## What changes

### 1. A second session moves the first one out

**Today:** `ensure` cuts a parallel worktree for the new session and leaves the open session in the main checkout, on its session branch.

**New:** when the main checkout's active session `S` has its run open and `sl session ensure --slug N` runs anywhere:

1. **Confirm the move.** `ensure` stops first, changes nothing, prints `ASK move slug=S: S moves from the main checkout to <path>; its chat must be idle` and exits 4. The agent asks you, then runs the command again with `--move yes`.
2. **Ask about untracked files.** The existing untracked gate runs first: `ASK untracked …`, then `--untracked stash|keep`.
3. **Move `S` out:**
   - It moves to `.superlite/worktrees/sessions/S` on its own branch, carrying its run files, its tracked work in progress, and its untracked files if you chose `stash`.
   - It reuses the `split` move code, so a failure rolls everything back.
   - Checkpoints are rebound to the new path.
4. **Reset the main checkout.** It switches back to the registry's `base` branch.
5. **Start `N` in a worktree.** `N` gets `.superlite/worktrees/sessions/N`, cut from the current `base`.
6. **Output.** `ensure` prints `session: moved S -> <path>`, and its last line is still `SESSION worktree=<N's path> …`.

Rows: `S` becomes `parallel` with `owned: true`, and the registry's `active` is cleared. The main checkout then hosts nothing until a later `ensure` runs there with no session open.

**Unchanged:**
- **Finished session:** if the main checkout's session is finished, `ensure` still parks it and reuses the main checkout. Nothing moves.
- **Empty main checkout:** with no session there, the new one starts in place, as today.

**`S`'s own chat is told where `S` went.** In a checkout whose session has moved out, the status line reads `superlite session: S moved to <path> — move the agent root there`. The hook also adds one line telling the agent to move its root.

### 2. Sessions are offered the new `main`

**New per-row field:** `base_sha`, the commit of local `base` when the session was created or last synced. Rows that don't have it get it filled in from `git merge-base <branch> <base>` the first time they're read.

**Detection.** Each turn, `session_hud.py` makes one extra `git rev-parse refs/heads/<base>` call, which is local only. When `base` has moved past this session's `base_sha`:

| Addition | When it appears | What it says |
|---|---|---|
| Status-line field | Every turn | ` \| main:+<N>` (commits on `base` that aren't in the branch) |
| One hook line | Once per new `main` commit | "local main moved ahead by N commits since this session last synced; ask the user whether to bring it in with `sl session sync`" |

Once you choose, `sl session sync --later` records your choice, so the hook line stops. The `main:+N` field stays.

**New command: `sl session sync --slug S [--untracked stash|keep] [--continue | --abort | --later]`.** It runs in `S`'s checkout.

- **Before merging:**
  - Tracked work in progress is stashed.
  - Untracked files go through the usual gate.
  - It refuses if a merge or rebase is already in progress.
- **The merge:**
  - **No commits of its own:** `git merge --ff-only <base>`.
  - **Has commits:** `git merge --no-edit <base>`.
  - **Afterwards:** the stash is restored and `base_sha` is recorded. It prints `session: S synced with main (fast-forward|merge)`.
- **On a conflict:**
  - It leaves the merge in progress, sets the row's `sync` to `"conflict"`, prints `CONFLICT S: <files>` and exits 5.
  - The agent hands the conflict to a fixer agent: resolve, then run the session's tests through `sl run_tests`.
  - **Fixer succeeds:** `sync --continue` checks nothing is unmerged, commits the merge, restores the stash, records `base_sha` and clears `sync`.
  - **Fixer fails, or tests fail:** `sync --abort` runs `git merge --abort`, restores the stash, and sets `sync: "needs-sync"`. The status line shows `| needs-sync` and the agent tells you.
- **Never:** no fetch, no push, no rebase, no force-push. A pushed PR branch gets an ordinary merge commit, pushed only when you push.

**After a local merge.** Shipping's "merge locally" step ends with `sl session status`, which now shows `main:+N` for each session that's behind. Each session's chat then gets its own offer through its status line. No session is changed automatically.

### 3. Flow text and docs

- `skills/shipping/SKILL.md`:
  - after a local merge, show `sl session status` and say each session will be offered the update in its own chat;
  - handle `ASK move` the same way as `ASK untracked`.
- `commands/start.md` and the other flow commands: handle `ASK move` the same way as `ASK untracked`.
- **The sync prompt:** when the hook says `main` moved, ask once with "Bring main in now" or "Later". "Now" runs `sync`, handles `CONFLICT` with a fixer, and runs `--continue` or `--abort`. This goes in one short shared paragraph in `docs/sessions.md`, which `start.md` points to.
- `docs/sessions.md`:
  - a "Move-out" section and a "Sync with main" section, with `sync` and the exit codes 4 and 5;
  - the `ensure` table updated for the move-out.

## Acceptance checks

1. **The move needs confirmation.** In a repo on `main`, run `ensure --slug a`; the main checkout is now on `superlite/sessions/a`. Then run `ensure --slug b`. It prints `ASK move slug=a: …`, exits 4, and changes nothing: no branch switch, no new worktree, registry unchanged.
2. **Both sessions move out.** Run `ensure --slug b --move yes` with a tracked edit and run files in `a`:
   - `a`'s worktree exists on `superlite/sessions/a` and holds the edit and `a`'s run files;
   - `b`'s worktree exists on `superlite/sessions/b`;
   - the main checkout is on `main` with a clean tree;
   - the output includes `session: moved a -> …`, and the last line is `b`'s `SESSION` line.
3. **Untracked files.** Same as 2, with an untracked file and no setting. You get `ASK untracked` first. `--untracked stash` carries the file into `a`'s worktree; `--untracked keep` leaves it in the main checkout.
4. **The moved session's chat is redirected.** The status line run from the main checkout with `a`'s chat's identity (`cwd` in the main checkout, registry showing `a` moved) prints `a moved to <path> — move the agent root there`.
5. **A finished session still reuses the main checkout.** After `sl finish` in `a` while `a` is still in the main checkout, `ensure --slug c` parks `a` and starts `c` in place. Nothing moves and there's no `ASK move`.
6. **Behind, then fast-forward.** Advance local `main` by 2 commits with a plain commit on `main` (no fetch).
   - In `b` (no commits of its own), the status line ends with `| main:+2` and the hook adds its one sync line.
   - `sync --later` stops the hook line, and `main:+2` stays.
   - `sync --slug b` fast-forwards. `main:+N` disappears and `base_sha` equals `main`.
7. **Merge with no conflict.** `b` with its own commit, `main` advanced on a different file: `sync` makes one merge commit, and the work in progress stashed before the merge is restored afterwards.
8. **Conflict, resolved.** `b` and `main` both change the same line.
   - `sync` prints `CONFLICT b: <file>`, exits 5, and leaves the merge in progress, with the row's `sync` set to `"conflict"`.
   - After the conflict is resolved and `git add`-ed, `sync --continue` commits the merge, clears `sync` and restores the stash.
9. **Conflict, given up.** In the same situation as 8, `sync --abort` restores the branch and the stash exactly as they were and sets `needs-sync`. The status line shows `| needs-sync`.
10. **Nothing touches origin.** `sync` and the status line never run `git fetch`. A test using a fake `origin` remote that moves ahead shows no change, and the only update comes from local `main`.
11. **Existing behaviour holds.**
    - All earlier session, split and ensure checks still pass.
    - The two `ensure` tests that pin the old "stay in the main checkout" behaviour (`test_ensure_goes_parallel_while_active_is_open`, `test_legacy_run_is_adopted_then_new_slug_goes_parallel`) are updated to the move-out behaviour. No other existing test changes.

## Code map

- **Move-out (`skills/swarm/scripts/session.py`):**
  - Change `cmd_ensure`'s branch at :880 (`if is_open(active_row)`), which today cuts a parallel worktree at :886.
  - Reuse `_split_move` (:1076–1129) for the move. It takes `root` and `sl`, and handles undo, the run-file move, the work-in-progress stash and `rebind_checkpoints` (:963). Afterwards, clear `reg["active"]` and keep `host_worktree` = the main checkout.
  - The untracked gate is `untracked_gate` (:784). `park_active` (:412) and `start_in_host` (:804) stay as they are for the finished-session path.
- **Registry fields.** Today `base` is stored once for the whole registry (`read_reg` :199 / `write_reg` :207), and no row records a commit. Add per-row `base_sha`, `sync` and `sync_offered`. Old rows get `base_sha` filled in from the merge base when first read.
- **Sync (new in `session.py`):** `cmd_sync` with `--continue`, `--abort` and `--later`. Reuse `in_progress` (:266), `unmerged` (:375), `stash_push` and `restore_stash`, and the `continue_apply` pattern (:599). Nothing merges branches today; this is new.
- **Detection (`skills/swarm/scripts/session_hud.py`):** add one `rev-parse refs/heads/<base>` alongside its existing calls (:18, :36, :91). Use `merge-base --is-ancestor` and `rev-list --count` only when the shas differ.
  - **Hook wiring:** `hooks/session-start` and `hooks/cost-report` already run the status-line script once per turn. They add the one sync line when the script reports the offer flag, the moved-out line in the same way, and no other process.
- **Shipping and flow text:** `skills/shipping/SKILL.md` step 5, "Merge locally" (:44), followed by steps 6–7; `commands/start.md` and the other flow command files.
- **Tests:** `tests/scripts/test_session_ensure.py` gets new tests, and the two tests named in check 11 are updated. Add a new `tests/scripts/test_session_sync.py`. `test_session_split.py` is unchanged.
- **Must not change:**
  - `split`'s behaviour and output;
  - `REDIRECT` and its exit 3, exit 2 for errors, and exit 4 for `ASK`;
  - the `SESSION` line;
  - the status line's existing fields (only `| main:+N`, `| needs-sync` and the moved-out variant are added);
  - the fact that no command fetches or pushes.
- **Conventions:** stdlib only, a `# O(...)` comment on every line, hooks in bash builtins that work under bash 3.2, and LF line endings.

## Prototype scope

- **Level:** L1, in `.superlite/sandboxes/session-sync-proto`.
- **Keep:** `session.py`, `session_hud.py`, both hooks, and the test fixtures.
- **Build:** move-out, `sync` (fast-forward, merge, conflict, `--continue`, `--abort`, `--later`), and the status line and hook output, with tests against checks 1–10.
- **Leave for integration:** the fixer-agent step in the conflict flow is instruction text, so the prototype checks only the `CONFLICT`, `--continue` and `--abort` mechanics. The rest of the flow text and docs are integration work.
- **Size:** about 5 tasks.

## Integration outline

Same worktree and branch (`superlite/sessions/session-autostart`). The new commits are added to PR #25.

1. `session.py`: move-out, `sync`, and the registry fields.
2. `session_hud.py` and the two hooks.
3. Tests: `test_session_sync.py`, the new `ensure` tests, and the two updated `ensure` tests.
4. Flow text: `shipping` and the flow commands (`ASK move`, the sync prompt).
5. Docs: `docs/sessions.md` and `docs/tools.md`.
6. Seam check: the wiring test is extended so that every flow file handles `ASK move` and shipping shows session status after a local merge.

Every existing test must still pass, apart from the two updates named in check 11.
