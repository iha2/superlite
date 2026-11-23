# Feature: /superlite:close

**Spec:** docs/plans/2026-11-23-close-session.spec.md (wins if this drifts)

## Goal

Superlite users who end a session without a merged PR (abandoned idea, parked work, local-only change). Today only `/superlite:merged` closes a session from chat, and it stops unless `gh` reports a merge. They need one command that ends a session safely and asks before anything is lost.

## What changes

Commands (Claude Code slash command; plain words "close this session" in other harnesses):

- `/superlite:close [slug | branch]` (empty: the current session).
  1. Resolves the session; if its run is open, names it and asks "Close it anyway" / "Keep it open".
  2. Names uncommitted, untracked and parked work. Tracked edits are saved to `refs/superlite/wip/<slug>` by close; untracked files get superlite's stash/keep question.
  3. Leaves the worktree if the chat is in it, runs `superlite-cli session close --slug <s> --keep-branch`, relays any `ASK` line.
  4. Asks once: "Keep the branch (Recommended)" / "Delete local branch", showing commits not on the base and whether the branch has an upstream. Delete runs `session discard --slug <s>`, then `git branch -D` if the branch remains.
  5. Prints a short report (session closed, worktree removed, branch kept/deleted, WIP ref).
- `superlite-cli session close` also releases the peer-plugin suspension when the run it closes was open.
- `/superlite:help` and README list the command.

## Acceptance checks

1. Inside a session worktree, `/superlite:close`: the session is gone from `superlite-cli session status`, its folder under `.claude/worktrees/` is gone, `git branch --list` still shows the branch.
2. From the main checkout, `/superlite:close <slug>` closes that session the same way.
3. With an edited tracked file in the session, you are told about it first; afterwards `git rev-parse refs/superlite/wip/<slug>` finds it.
4. With an untracked file, you get the stash/keep question and nothing changes before you answer.
5. You are asked once to keep or delete the branch, with its unmerged commit count and upstream status.
6. Answer "Delete local branch": the branch and WIP ref are gone; `git ls-remote origin <branch>` is unchanged.
7. Answer "Keep the branch": branch and WIP ref remain.
8. On a session with an open run you are asked before closing; after close, `superlite-cli exclusive status` says peers are not suspended.
9. `/superlite:help` and README show `/superlite:close`.

## Code map

- **Entry points:** slash commands are `commands/*.md` (front-matter `description`, `argument-hint`; body loads a skill). Skills are `skills/<name>/SKILL.md`, picked up by folder in every manifest (only `.muse-plugin/plugin.json` lists skills by hand, and already omits `merged`). The prompt hook (`hooks/prompt:36`) intercepts only cloud commands: no change.
- **Closest analogue:** `commands/merged.md` + `skills/merged/SKILL.md` (step 3 "Leave the branch", step 5 "Delete the branch").
- **CLI touched:** `skills/swarm/scripts/session_close.py` `cmd_close` (`:56-67` routing: host, owned, adopted, stored). Owned close uses `git worktree remove` without force and saves tracked edits to the WIP ref; adopted (Claude-created) worktrees are left for `ExitWorktree`. No `exclusive.release` today; `finish.py:327-337` is the only caller. The lock lives in `.superlite/peers.json` + `<checkout>/.claude/settings.local.json` (`exclusive.py:36-60`).
- **Delete path:** `session discard --slug` (`session_switch.py:190`) removes store, WIP ref and branch (`-D`) unless adopted.
- **Docs to touch:** `assets/help.txt:101` (next to merged), `README.md:58` row, `docs/sessions.md:64,96`, `skills/shipping/SKILL.md:45` pointer.
- **Frozen interfaces:** `session close` / `session discard` flags, output lines (`SESSION`, `ASK`, `closed`) and exit codes (2 refuse, 4 ask); `using-superlite/SKILL.md` (1,883/2,000 chars, untouched).
- **Tests:** `tests/scripts/test_session_ensure.py` (close tests :138-:594, `repo` and `owned_b` fixtures), `test_session_close_archive.py`, `test_session_close_sandbox.py`. `test_doc_contract.py` validates every `superlite-cli` call in docs; `test_name_scan.py` forbids markdown-grep-only tests and "commit" outside its allowlist. Run: `pytest` (`-n auto`, `testpaths=tests/scripts`).
- **Toolchain:** Python 3 scripts under `skills/swarm/scripts`, uv, pytest; LF endings.

## Decisions

- Decided: separate command, not a mode of `merged` (merged's guards depend on `gh`).
- Decided: close always runs with `--keep-branch`; deletion is a second, asked step through `session discard`, so the WIP ref and store go with the branch.
- Decided: `session close` calls `exclusive.release` when the closed run was open (gap found in research: a host close leaves peers disabled).
- Decided: Claude-created worktrees end with `ExitWorktree remove` after close, as `merged` does; a dirty one needs the user's yes before `discard_changes`.
- Assumption: no command-inventory test exists, so no test change for registration.
- Prototype decides: question wording and the report format.

## Prototype scope

Level L1. Sandbox keeps: draft `commands/close.md`, `skills/close/SKILL.md`, a copy of `session_close.py` with the release change, and a `tryout.sh` that builds a throwaway git repo with two sessions (one dirty owned worktree with an unpushed commit, one host session with an open run) and walks the close sequence the skill prescribes. Stubs: no harness, no `gh`. Tests: the release behaviour and the close-then-discard sequence (acceptance 1, 3, 6, 7, 8).

## Integration outline

1. `session_close.py`: release the peer lock on open-run close; regression test in `tests/scripts/test_session_close_archive.py` style.
2. `skills/close/SKILL.md` + `commands/close.md`, ported from the prototype.
3. Docs: help.txt, README, docs/sessions.md, shipping pointer.
4. Seam test: close-then-discard on an owned worktree with unmerged commits deletes the branch and WIP ref; close alone keeps both. `test_doc_contract.py` passes over the new files.
