---
name: merged
description: Use when the user says a pull request was merged, to clean up its session, branch and local main
---

# Merged

The PR is merged on the remote. Bring the local repo up to date and remove what the PR left behind. Only existing tools, in this order; never push, never touch the remote branch. Nothing here is guessed: each step checks before it changes anything, and any check that fails stops the flow with nothing changed after it.

## 1. Find the branch

Take the first that works:

- the argument: a session slug (branch `superlite/sessions/<slug>`), a branch name, or a PR number (`gh pr view <n> --json headRefName`)
- the current branch, when it is not the base branch
- otherwise run `bash <skill-dir>/../swarm/scripts/superlite-cli session status` and `git branch --list 'superlite/sessions/*'`, and ask once (one AskUserQuestion) which one was merged

Find the base branch the way `superlite:shipping` step 2 does. The slug is the branch name after `superlite/sessions/`; a branch without that prefix has no slug unless `session status` lists it.

## 2. Confirm the merge

`gh pr list --head <branch> --state all --json number,state,url,headRefOid,headRepositoryOwner,mergeCommit,baseRefName`

Keep only PRs whose `headRepositoryOwner` is this repo's owner (`gh repo view --json owner`): `--head` also matches same-named fork branches. The PR with the highest `number` decides.

- No `gh`, not signed in, or no PR for the branch: say so and stop. Do not fall back to `git branch --merged`; a squash merge never shows there.
- `OPEN`: say it is still open and stop.
- `CLOSED`, not merged: ask once, "Keep the branch" / "Discard it". On keep, stop. On discard, skip the head check below and step 4: run steps 3, 5 and 6.
- `MERGED`: use its `baseRefName` as the base and go on.

Then check the local branch holds nothing the PR did not. If `git cat-file -e <headRefOid>` fails, run `git fetch origin pull/<number>/head` first; if it still fails, stop. Then `git rev-parse --verify refs/heads/<branch>` must equal `headRefOid`, or `git log --oneline <headRefOid>..refs/heads/<branch>` must exit 0 and print nothing. If it lists commits or exits non-zero, name them (or the error) and stop: they were never in the PR.

## 3. Leave the branch

Look up the branch's checkout in `git worktree list --porcelain`.

- Its checkout has uncommitted work (`git status --short` there is not empty), or `git rev-parse -q --verify refs/superlite/wip/<slug>` finds parked work: that work is not in the PR. Name it and ask once, "Stop and keep it" / "Discard it". On stop, stop.
- The session is still listed in `session status`: first leave its worktree if you are in one (`ExitWorktree` with `action: "keep"` under Claude Code, else `cd` to the main checkout), then from the main checkout run `bash <skill-dir>/../swarm/scripts/superlite-cli session close --slug <slug> --keep-branch`. It puts the host back on the base branch and removes a worktree superlite cut. On an `ASK` line (exit 4, nothing changed), ask exactly what its `ask:` line says and do what the chosen `on ...:` line says. A Claude-created worktree is not removed by close: end it with `ExitWorktree` `remove`, as `superlite:shipping` step 7 says.
- No session, but the branch is checked out: switch that checkout to the base branch (`git switch <base>`), or leave a linked worktree the way step 7 of `superlite:shipping` does.

## 4. Update the base branch

`git fetch --prune origin`, then fast-forward only:

- The base is checked out in a clean checkout: `git merge --ff-only origin/<base>` there.
- The base is not checked out anywhere: `git fetch origin <base>:<base>`.
- The base is checked out with uncommitted work, or the fast-forward fails (local base has commits origin does not): report it and skip this step. Never merge, rebase or reset the base.

## 5. Delete the branch

- With a slug (a listed or archived superlite session): `bash <skill-dir>/../swarm/scripts/superlite-cli session discard --slug <slug>`. It drops the session's store and parked work ref, and the branch unless superlite adopted it.
- Then, or without a slug: if `git rev-parse -q --verify refs/heads/<branch>` still finds it, `git branch -D <branch>`. `-D` is safe here only because step 2 proved the branch is in the merged PR, or the user chose "Discard it"; never use it otherwise.

The remote branch is not touched. GitHub's "delete branch on merge" or the user removes it.

## 6. Report

Run `bash <skill-dir>/../swarm/scripts/superlite-cli session status`. Print one line per step: PR number and URL, merge commit, the session closed, base `<old sha>..<new sha>` (or why it was skipped), the branch deleted. Name each session now behind the base (`main:+N`); each one is offered the update in its own chat, nothing is synced here.

## Never

- Push, delete a remote branch, or open, close or merge a PR.
- Delete a branch `gh` has not reported as merged, or one with commits the PR does not hold, unless the user chose "Discard it".
- Merge, rebase or reset the base branch.
- Discard uncommitted or parked work without the user's answer.
