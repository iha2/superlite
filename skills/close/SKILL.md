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
- **Uncommitted work** in its checkout (`git status --short`). In the host checkout or a worktree superlite cut, close saves tracked edits to `refs/superlite/wip/<slug>`, and untracked files get superlite's own stash/keep question in step 3. A Claude-created worktree (`harness` in its `session status` row) is never saved by close: its edits exist only in that folder.
- **Prototype sandbox:** a `sandbox <topic> <state> <path>` line under its row in `session status`. Close deletes that folder (only its run files are archived); its code is on neither the branch nor the WIP ref.
- **Parked work:** the row shows `stash:yes`, or `git rev-parse -q --verify refs/superlite/wip/<slug>` finds a ref. Close carries it to that ref; it is not on the branch.

With an open run or an open sandbox, ask "Close it anyway" / "Keep it open", naming the sandbox path as deleted; on keep, stop. Otherwise just list the facts and go on.

## 3. Close

- If this chat is inside the session's worktree, leave it first: `ExitWorktree` with `action: "keep"` under Claude Code, else `cd` to the main checkout.
- From the main checkout: `bash <skill-dir>/../swarm/scripts/superlite-cli session close --slug <slug> --keep-branch`.
- On an `ASK` line (exit 4, nothing changed), ask exactly what its `ask:` line says and do what the chosen `on ...:` line says. Exit 2: report its message and stop.
- A Claude-created worktree is not removed by close. Clean: end it with `ExitWorktree` `remove`. With uncommitted changes, ask "Keep the worktree" / "Discard the edits": on keep, leave it in place (its edits stay there, unsaved); only on discard use `discard_changes: true`.
- Then check `session status --json` again. If the row is still listed, close kept the checkout (`--untracked keep` or the repo default `keep`): report where it is and stop. Step 4 would force-remove it.

## 4. Keep or delete the branch

If `git worktree list --porcelain` still shows the branch checked out (a kept Claude-created worktree), it cannot be deleted: report where it is checked out, keep everything, and go to step 5.

Otherwise ask once (one AskUserQuestion), showing the commit count from step 2, whether it has an upstream, and any parked work (`refs/superlite/wip/<slug>`, with `git show --stat` of it):

- **Keep the branch (Recommended):** stop here. `superlite-cli session use --slug <slug>` reopens it later with its parked work.
- **Delete local branch:** name what is lost: commits not on the base when there is no upstream, and the parked work on `refs/superlite/wip/<slug>`, which discard deletes too. Then `bash <skill-dir>/../swarm/scripts/superlite-cli session discard --slug <slug>` (drops the archived store, the WIP ref and the branch, unless superlite adopted it). If `git rev-parse -q --verify refs/heads/<branch>` still finds it, `git branch -D <branch>`; `-D` is used only because the user chose delete.

## 5. Report

Run `bash <skill-dir>/../swarm/scripts/superlite-cli session status`. One line each: session closed, worktree removed (or left), branch kept or deleted, WIP ref kept or dropped, peers released.

## Never

- Push, delete a remote branch, or open, close or merge a PR.
- Delete a branch, or discard uncommitted or parked work, without the user's answer.
- Close the base branch's session or switch another session's checkout.
