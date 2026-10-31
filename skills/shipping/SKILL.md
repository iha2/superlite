---
name: shipping
description: Use when implementation is complete, all tests pass, and you need to decide how to integrate the work
---

# Shipping

Never push, merge or publish without the human. The human's choice is the only authorization.

## 1. Verify

Run `bash <skill-dir>/../swarm/scripts/sl run_tests --full` (it prints one line: `passed failed skipped seconds`). Skip it if the last full run was green and nothing has changed since (see `superlite:proof`). If tests fail, report the failures and stop. Do not offer to merge failing work.

## 2. Detect the base branch yourself

No question. Take the first that works:

- `git symbolic-ref --short refs/remotes/origin/HEAD` (strip `origin/`)
- `main`, then `master`, if it exists locally (`git rev-parse --verify`)
- otherwise the local branch with the closest `git merge-base` to HEAD

Also note the current branch, `git status --short` (superlite default: the build is uncommitted by design, see `finish`; otherwise commit or report uncommitted work first), and whether a remote exists.

## 3. Summarize

Prepare: branch, base, commits ahead (`git log --oneline <base>..HEAD`), `git diff --stat <base>..HEAD`, the test counts line, and the rulings list recorded during the work (including review findings left unfixed). When the work was a superlite build, also the build report summary from `finish` (or `sl report --plan <plan>`); print it before the options and name `<plan>.report.md` (next to the plan) as the full report, to show on request.

## 4. Decide

**Attended:** ask ONE question with AskUserQuestion, three options, with the summary in the question text:

1. Merge locally into the base branch
2. Push and open a pull request
3. Keep the branch as is

If `finish` printed NOTHING WAS COMMITTED (superlite default), options 1 and 2 read "Commit, then ...": after the answer run `sl autocommit apply` (or `/superlite:commit`), which refuses if HEAD moved or the tree has unrelated changes; option 3 leaves the work uncommitted. Never run `git commit` yourself.

No further menus or typed confirmations. Act on the answer.

**Unattended** (the user is away or said to decide yourself): keep the branch. Do not push, merge or open a PR. Print the summary and the rulings list as the closing message (uncommitted work stays uncommitted).

## 5. Execute the choice

- **Merge locally:** if the work ran in a worktree, run the merge from the main checkout (where the base branch lives). Check out the base branch, merge the feature branch. If the merge was not a fast-forward or had conflicts, run `bash <skill-dir>/../swarm/scripts/sl run_tests --full` again. Close the session (step 6), then remove the worktree (step 7), then delete the feature branch with `git branch -d`.
- **Push and PR:** `git push -u origin <branch>`, then `gh pr create` with a title and a body holding the summary and test evidence. Report the PR URL. Keep the branch. Close the session (step 6), then remove the worktree (step 7).
- **Keep:** report the branch name. If the worktree is clean, close the session (step 6), then remove it (step 7). If it has uncommitted work, leave it and report the path.

## 6. Close the session

Once the work is merged, has a PR, or is kept, and the agent is back in the main checkout, run `bash <skill-dir>/../swarm/scripts/sl session close --slug <slug> --keep-branch`. It marks the session finished, puts the main checkout back on the base branch when `<slug>` was its active session, and removes a parallel worktree that `ensure` cut (the branch stays). Run it before the worktree cleanup below. Skip it when the run has no session.

If it prints `ASK untracked=<N> slug=<s>: <files>` and exits 4, nothing changed. Ask in ONE AskUserQuestion: "Stash with <s>" / "Keep in the folder" / "Always stash (this repo)" / "Always keep (this repo)". For an "Always" choice run `sl session untracked stash|keep` first; then rerun the same command with `--untracked stash|keep`.

## 7. Worktree cleanup

A flow removes every worktree it created before it ends. Move the agent root back to the main checkout first, then from there run `git worktree remove <path>`. Skip a worktree that `sl session close` already removed (`session: closed <slug>; removed its worktree`), and never remove one this flow did not cut.

- Merged: remove the worktree, then `git branch -d` the feature branch.
- Pushed: remove the worktree. Leave the branch.
- Kept, and `git status --short` in that worktree is empty: remove the worktree. Leave the branch.
- Kept, with uncommitted work: leave the worktree and name its path. Do not use `--force`.

A prototype sandbox is removed with `sl proto discard` at feature finish, not with this step.

## Never

- Push or merge without the human's answer.
- Force-push, or delete a branch with unmerged work.
- Proceed with failing tests.
- Ask which base branch to use.
