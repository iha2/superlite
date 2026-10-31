# Session auto-start (feature plan)

## Goal

Every superlite flow (start, regular, mvp, feature, debug, small, review) runs inside a session, without anyone typing `sl session create`.

**The main checkout is the host.** A run that starts there gets its own branch, `superlite/sessions/<topic>`, in the main checkout itself. `sl session use` switches between sessions by branch, right there. A parallel worktree is cut only when a second run starts while the main checkout's session is still open.

**Each session records whether its run is open or finished.** So "run in progress" means a run really is open, never just leftover files.

**Today:**
- A first run writes into the main checkout with no session.
- A finished run keeps showing "run in progress".
- `split` can move a finished run, and unrelated untracked files, into a worktree. That happened on 2026-10-31.

**For:** superlite users who switch between several lines of work in one repo, and the flows themselves.

## What changes

- **New command: `sl session ensure --slug <topic>`.** It's idempotent: run it at the start of any flow, from any checkout.

  | Where it runs | What it does |
  |---|---|
  | A checkout whose session is open (parallel, or the host's active one) | Nothing; prints `session: <slug> already open here` |
  | Main checkout, no active session, or the active one is finished | Same as `create --slug <topic>` with the **main checkout as host**: it creates `superlite/sessions/<topic>` from the current branch and switches the main checkout to it. A finished active session is parked first, the way `create` already parks. Uncommitted files go along with the switch. |
  | Main checkout, active session still open | Same as `create --slug <topic> --worktree`: cuts `.superlite/worktrees/sessions/<topic>`. The open session is not touched. |
  | Main checkout with a run from before this change (`orchestrator.json`, no registry row) | Records that run as the active host session `<feature.json topic, or the branch slug>`, open. Moves `main` onto its branch only when the main checkout is on its base branch; otherwise it keeps the branch it's on. Then it follows the "still open" row. |
  | An unregistered linked worktree (Claude Code's, a feature integration tree) | Registers it as a session where it is (`owned: false`, its current branch), open |
  | A registry that already has a separate host worktree (`.superlite/worktrees/sessions/host`) | That host stays the host. `ensure` creates the session there and prints `REDIRECT` to it (exit 3). |

  - **Slug taken:** adds `-2`, `-3`, and so on, and prints the slug it settled on.
  - **Last line, always:** `SESSION worktree=<path> slug=<slug> branch=<branch>`. The agent moves its root to `<path>` if that isn't the folder it's in.
  - **Opt out:** with `SUPERLITE_NO_AUTO_SESSION=1` it prints `session: auto-start off` and exits 0.

- **Run status on each session row.**
  - **The field:** `sessions.json` rows get `run: "open" | "finished"` and `finished_at`. `ensure` and `create` set `open`.
  - **`sl finish` marks it finished.** That's the session row for the checkout it runs in, done next to its existing peer release.
  - **New `sl session close --slug S [--keep-branch]`, the end-of-flow step, does three things:**
    1. marks `S` finished;
    2. if `S` is active on the host, parks its run files in the store and switches the host back to the registry's `base` branch (`stash -u` first if the tree is dirty, as with `use`);
    3. if `S` is a parallel worktree that `ensure` cut, removes that worktree. The branch is kept with `--keep-branch`.
  - **`sl session use` and `sl session status`** show `run:open` or `run:finished`.

- **Untracked files are stashed only with your OK.** This applies to `ensure`, `close`, `use`, and `create` when it parks an active session.
  - **The setting:** `sl session untracked [ask|stash|keep]` stores a per-repo choice in the main checkout's `sessions.json`. With nothing set, the default is `ask`. An `--untracked stash|keep` flag on the command overrides it.
  - **`ask`:** before anything changes, the command prints `ASK untracked=<N> slug=<s>: <files>` and exits with code 4.
    - The agent then asks you in one question: stash them with the session, keep them in the folder, always stash (this repo), or always keep (this repo).
    - An "always" answer runs `sl session untracked …` first. The agent then runs the command again with `--untracked …`.
  - **`stash`:** the files go into that session's stash, and a `session: stashed N untracked file(s) with <slug>: …` line names them. `use` prints this line too.
  - **`keep`:** only tracked changes are stashed. Untracked files stay in the folder and a line names them. If one of them would block the branch switch, the command undoes everything and exits 2.
- **"Run in progress" reads the session.**
  - **The status line** gains a last field, ` | run:open` or ` | run:finished`.
  - **`hooks/session-start`** shows `run in progress` only when this checkout's session is `run:open`. A checkout with no session row keeps today's `orchestrator.json` check.
  - **`suggest_flow.py` `project_state`** follows the same rule.
  - **In the main checkout with no active session,** the status line reads `superlite session: none here (main checkout) | <N> sessions`. Today it names the first row instead.

- **Hooks find the run in the working folder first.** Five hooks find the run starting from `CLAUDE_PROJECT_DIR` (the main checkout) and walk up from there. A session worktree sits below the main checkout, so they miss its run. On 2026-10-31 this lost the human's "Approve" and left the approval gate off inside the worktree.
  - **Affected hooks:**
    - `record-human-reply`
    - `block-coordinator-edits` (the approval gate)
    - `capture-agent-launch`
    - `capture-agent-stop`
    - `compact-nudge`
  - **Fix:** each hook takes the `cwd` from the hook input, walks up from it, and uses the first `.superlite/` it finds with a run. Only if that finds nothing does it fall back to today's lookup.
  - **Shell rules:** bash builtins only, and it must work under bash 3.2.
- **`sl orchestrator set` warns** on stderr when this checkout has no session: `session: none for this checkout; run sl session ensure --slug <topic> first`. It still writes the file and exits 0, so existing callers and tests keep working.

- **Flows call `ensure` first.** It runs once the topic is known and before `sl orchestrator set`, or before the first other `sl` call.
  - **Where:** `commands/start.md`, `mvp.md`, `feature.md`, `debug.md`, `small.md`, `review.md`, and the regular flow in `skills/inventing/SKILL.md`.
  - **`start.md`'s "Start something new" block** becomes: run `ensure`, then follow its `SESSION` or `REDIRECT` line.
  - **`split` and serial `use`** stay as manual tools.

- **Flows end with `close`.** `skills/shipping/SKILL.md`: once the work is merged, has a PR, or is kept, the agent moves back to the main checkout and runs `sl session close --slug <slug> --keep-branch`. The flow then leaves behind no worktree it cut, as AGENTS.md requires.

- **Docs:**
  - `docs/sessions.md`: the main checkout as host, auto-start, run status, `ensure` and `close`.
  - `docs/tools.md`: the session row.
  - `README.md`: one line.

## Acceptance checks

1. **New session in place.** In a scratch repo on `main` with no `.superlite/`, run `sl session ensure --slug demo`.
   - The main checkout is now on `superlite/sessions/demo`.
   - The output ends with `SESSION worktree=<repo> slug=demo branch=superlite/sessions/demo`.
   - `sl session status` lists `demo` as active and `run:open`.
2. **Idempotent.** Run the same command again. It prints `already open here` and changes nothing.
3. **Parallel while open.** Now run `ensure --slug next` in the main checkout.
   - It cuts `.superlite/worktrees/sessions/next` on `superlite/sessions/next`.
   - The main checkout stays on `demo`.
   - An untracked `docs/plans/other.md` in the main checkout stays there.
4. **Reuse once finished.** Run `sl finish` in the main checkout (status: `demo run:finished`), then `ensure --slug after`. `demo` is parked, the main checkout switches to `superlite/sessions/after`, and no worktree is cut.
4b. **Untracked files.** Repeat step 4 with an untracked `docs/plans/other.md`.
    - With no setting, `ensure` prints `ASK untracked=1 slug=demo: docs/plans/other.md`, exits 4, and changes nothing.
    - `--untracked stash` stashes the file with `demo` and names it.
    - `--untracked keep` leaves it in the folder on `after`.
    - After `sl session untracked stash`, there is no ASK.
    - `use` names the files it stashes.
5. **Switch by branch.** Run `sl session use --slug demo`. The main checkout switches back to `superlite/sessions/demo` and its run files are restored. Then run `use --slug after` to switch back.
6. **Close.**
   - `sl session close --slug after --keep-branch` marks it finished, parks its files, and puts the main checkout back on `main`. The branch still exists.
   - `close` on `next` removes its worktree and keeps its branch.
7. **Old run adopted.** In a repo with `.superlite/orchestrator.json` and no registry, run `ensure --slug new`.
   - The old run becomes the active host session, `run:open`.
   - `new` gets a parallel worktree.
   - No run file moves.
8. **Unregistered worktree.** In an unregistered linked worktree, `ensure --slug x` registers it where it is (`owned: false`) and cuts nothing.
9. **Opt out.** `SUPERLITE_NO_AUTO_SESSION=1 sl session ensure --slug demo` changes nothing.
10. **Status line and project state.**
    - The session-start hook in a checkout whose session is `run:finished` says `Project state: existing code`, not `run in progress`.
    - With no active session in the main checkout, the status line says `none here (main checkout)`.
11. **Orchestrator warning.** `sl orchestrator set human` in a checkout with no session prints the warning and still exits 0.
11b. **Hooks find the session's run.**
    - With `CLAUDE_PROJECT_DIR` set to the main checkout and `cwd` set to a session worktree that has an unapproved human run:
      - `record-human-reply` writes the reply into the worktree's `.superlite/` and nothing into the main checkout's.
      - `block-coordinator-edits` blocks a build command, the way it does in the main checkout.
      - The capture hooks write their ledger lines into the worktree's `.superlite/`.
    - A run in the main checkout is found exactly as it is today.
12. **Flows wired.** Every flow command file listed above runs `sl session ensure` before its first other `sl` call.
13. **Nothing else breaks.** `uv run pytest tests/scripts -q` passes, with every existing session, split and orchestrator test unchanged.

## Code map

- **How the plugin runs**
  - It's markdown commands and skills, plus stdlib Python scripts run through `bash skills/swarm/scripts/sl <tool>`.
  - The dispatcher, `sl`, only routes calls.
  - Tests: `uv run pytest tests/scripts -q`.
- **Sessions core: `skills/swarm/scripts/session.py`**
  - **Helpers to reuse:**
    - `hub_root` :68, `toplevel` :64, `branch_owner` :248
    - `registry_lock` :144, `read_reg`/`write_reg` :171/:199, `upsert_row` :221
    - `park_active` :391, `enter_session` :404
  - **`ensure_host` :372:** today it only cuts `.superlite/worktrees/sessions/host`. It needs a path that adopts the hub itself as the host (`reg["host_worktree"] = root`). Its check `same(toplevel(host), host)` already passes for the hub.
  - **`cmd_create` :455:** its host path switches the host's branch and parks any active session, which is what `ensure` reuses. When `ensure` uses the main checkout as host, `base` must default to the main checkout's current branch.
  - **`run_entries`/`STATIONARY` :47:** they already keep `sessions.json`, the lock, `session-store/`, `worktrees/` and `sandboxes/` out of parking. That's why the hub can be the host.
  - **`cmd_discard` :590, `cmd_split` :642:** unchanged. The `main` subparsers are at :800.
- **Status line: `skills/swarm/scripts/session_hud.py`**
  - `line_for` :67. Its fallback at :92-96 picks `active` or `sessions[0]`.
  - It's called from `hooks/session-start:103-107` and `hooks/cost-report:55`.
- **Project state**
  - `hooks/session-start:23-35` uses bash builtins, then the HUD call at :106. The hook can reuse the HUD output's `run:` field, so it doesn't spawn another process.
  - `skills/swarm/scripts/suggest_flow.py:54-62`.
- **Finish and orchestrator**
  - `skills/swarm/scripts/finish.py` `main` :205: add the session mark next to `exclusive.release`.
  - `skills/swarm/scripts/orchestrator.py` `set` :178-187: the warning.
- **Flow text**
  - `commands/start.md`, `mvp.md`, `feature.md` (each has an `orchestrator set` line)
  - `debug.md`, `small.md`, `review.md` (no `sl` call today)
  - `skills/inventing/SKILL.md` (the `orchestrator set` sentence)
  - `skills/shipping/SKILL.md`
  - Not touched: `ai.md` and `human.md`, which switch modes rather than start flows.
- **Tests**
  - `tests/scripts/test_session.py` (fixtures `repo`, `run`, `reg_of`, `row_of`; status-line test :140)
  - `test_session_split.py`, `test_session_start.py`, `test_orchestrator.py`, `test_suggest_flow.py`
  - New: `tests/scripts/test_session_ensure.py`
- **Interfaces that don't change**
  - The existing `session` subcommands, flags and output lines.
  - `REDIRECT` and its exit code 3.
  - Existing `sessions.json` fields (new ones only).
  - The status line's existing fields, in order. `| run:…` is only appended.
  - The `orchestrator set` exit code and the file it writes.
  - A separate host worktree in an existing registry keeps working.
  - `STATIONARY`.
- **Conventions**
  - Stdlib only.
  - A `# O(...)` comment on every script line.
  - LF line endings.
  - Keep `using-superlite/SKILL.md` under 1,500 characters (not touched here).
  - Don't change the version (`scripts/bump-version.sh` is not part of this work).

## Prototype scope

- **Level and location:** L1, in `.superlite/sandboxes/session-autostart-proto`.
- **Keep:**
  - `session.py`, `session_hud.py`, `finish.py`
  - `orchestrator.py`, `hooks/session-start`, `suggest_flow.py`
  - The test fixtures
- **Build:**
  - `ensure`, `close`, the `run` field, the status-line field, project state, and the `orchestrator set` warning.
  - Tests written against checks 1-11.
- **Stub:** command and skill text. Only `start.md` and `small.md` get the `ensure` line, enough to check the wording.
- **Size:** about 6 tasks.

## Integration outline

**Worktree:** this session's checkout, `.superlite/worktrees/sessions/session-autostart`, on `superlite/sessions/session-autostart` from `main` (at `8912243`).

1. **`session.py`:** hub as host in `ensure_host`, `cmd_ensure`, `cmd_close`, the `run` field in `create`/`use`/`status`, and the subparsers.
2. **`finish.py`:** mark the session finished.
3. **`orchestrator.py`:** the warning.
4. **Status line and project state:** `session_hud.py`, `hooks/session-start` and `suggest_flow.py`.
4b. **Hooks:** the cwd-first run lookup in `record-human-reply`, `block-coordinator-edits`, `capture-agent-launch`, `capture-agent-stop` and `compact-nudge`. Tests go in `test_human_gate.py`, `test_capture_hooks.py` and `test_hooks_bash32.py`. `record-human-reply` is already patched in the installed plugin, and the test port matches that patch.
5. **Tests:** `test_session_ensure.py`, plus additions to the status-line, project-state, suggest-flow and orchestrator tests.
6. **Flow text:** the six command files and `inventing`.
7. **End step:** `shipping`.
8. **Docs:** `sessions.md`, `tools.md`, `README.md`.
9. **Seam check:** a test that greps each flow command file and confirms `ensure` comes first (check 12).

Every existing test must still pass.
