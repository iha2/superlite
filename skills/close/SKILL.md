---
name: close
description: Use when the user wants to end, abandon or park a superlite session without a merged PR
---

# Close

End one session with no merge: its run is archived, its row leaves the registry, a worktree superlite cut is removed, a host checkout goes back to its base. The branch stays unless the user chooses to delete it. A merged PR is `/superlite:merged` instead. Never push, never touch the remote branch. Each step checks before it changes anything; a failed check stops the flow with nothing changed after it.

## 1. Find the session

Take the first that works:

- the argument: a session slug, or a branch name matched against the rows of `bash <skill-dir>/../swarm/scripts/superlite-cli session status --json`
- the session of the current checkout (its row in `session status --json`)
- otherwise list the live sessions and ask once (one AskUserQuestion) which one to close

No live session matches: say so and stop. Find the base branch the way `superlite:shipping` step 2 does. Never close a row whose branch is the base branch.

## 2. Name what is at stake

Collect, then ask at most once:

- **Open run:** its row shows `run:open`. Closing it marks the run finished and archives its files; a build in progress stops.
- **Work not on the base:** `git log --oneline <base>..<branch>` (count), and whether the branch has an upstream (`git rev-parse -q --verify <branch>@{upstream}`).
- **Uncommitted work** in its checkout (`git status --short`): tracked edits are saved by close to `refs/superlite/wip/<slug>`; untracked files get superlite's own stash/keep question in step 3.

With an open run, ask "Close it anyway" / "Keep it open"; on keep, stop. Otherwise just list the facts and go on.

## 3. Close

- If this chat is inside the session's worktree, leave it first: `ExitWorktree` with `action: "keep"` under Claude Code, else `cd` to the main checkout.
- From the main checkout: `bash <skill-dir>/../swarm/scripts/superlite-cli session close --slug <slug> --keep-branch`.
- On an `ASK` line (exit 4, nothing changed), ask exactly what its `ask:` line says and do what the chosen `on ...:` line says. Exit 2: report its message and stop.
- A Claude-created worktree is not removed by close: end it with `ExitWorktree` `remove`. If it lists uncommitted changes, ask before `discard_changes: true`.

## 4. Keep or delete the branch

Ask once (one AskUserQuestion), showing the commit count from step 2 and whether it has an upstream:

- **Keep the branch (Recommended):** stop here. `superlite-cli session use --slug <slug>` reopens it later with its parked work.
- **Delete local branch:** if it has commits not on the base and no upstream, say they will be lost. Then `bash <skill-dir>/../swarm/scripts/superlite-cli session discard --slug <slug>` (drops the archived store, the WIP ref and the branch, unless superlite adopted it). If `git rev-parse -q --verify refs/heads/<branch>` still finds it, `git branch -D <branch>`; `-D` is used only because the user chose delete.

## 5. Report

Run `bash <skill-dir>/../swarm/scripts/superlite-cli session status`. One line each: session closed, worktree removed (or left), branch kept or deleted, WIP ref kept or dropped, peers released.

## Never

- Push, delete a remote branch, or open, close or merge a PR.
- Delete a branch, or discard uncommitted or parked work, without the user's answer.
- Close the base branch's session or switch another session's checkout.
