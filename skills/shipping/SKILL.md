---
name: shipping
description: Use when implementation is complete, all tests pass, and you need to decide how to integrate the work
---

# Shipping

Never push, merge or publish without the human. The human's choice is the only authorization.

## 1. Verify

Read `**Full suite:**` in the plan header. `yes`: run `bash <skill-dir>/../swarm/scripts/superlite-cli run_tests --full --reuse`. `no` or missing: run `bash <skill-dir>/../swarm/scripts/superlite-cli run_tests --affected --base <start sha>` (the sha is the `- start <sha>` line in `.superlite/progress.md`). After `finish`, the run's files live in the folder its `run: archived to <dir>` line names: read the start sha from `<dir>/progress.md`, and pass `--summary <dir>/test-summary.json` with `--full --reuse` so it can see the last green run. It prints one line: `passed failed skipped seconds`. `--reuse` skips a rerun when nothing has changed since a green run (see `superlite:proof`). Red blocks merge and PR: report the failures and stop. Do not offer to merge failing work.

## 2. Detect the base branch yourself

No question. Take the first that works:

- `git symbolic-ref --short refs/remotes/origin/HEAD` (strip `origin/`)
- `main`, then `master`, if it exists locally (`git rev-parse --verify`)
- otherwise the local branch with the closest `git merge-base` to HEAD

Also note the current branch, `git status --short` (a superlite build is uncommitted by design, see `finish`; step 4 asks about it), and whether a remote exists.

## 3. Summarize

Prepare: branch, base, commits ahead (`git log --oneline <base>..HEAD`), `git diff --stat <base>..HEAD`, the test counts line, and the rulings list recorded during the work (including review findings left unfixed). When the work was a superlite build, also the build report summary from `finish` (or `superlite-cli report --plan <plan>`); print it before the options and name `<plan>.report.md` (next to the plan) as the full report, to show on request.

## 4. Decide

**Attended:** ask ONE question with AskUserQuestion, three options, with the summary in the question text:

1. Merge locally into the base branch
2. Push and open a pull request
3. Keep the branch as is

Merge and PR options apply to commits already on the session branch. A superlite build leaves its work uncommitted in the folder, so first ask once at session end: "Commit to <session branch>" / "Leave uncommitted". On "Commit": make one commit of the finished work on `superlite/sessions/<slug>` only, with the message = the session topic plus the first line of the report summary. Never commit on main or on another branch: if the current branch is not the session branch, skip the commit and say so. Without a session branch there is no commit offer. On "Leave uncommitted" only option 3 applies.

No further menus or typed confirmations. Act on the answer.

**Unattended** (the user is away or said to decide yourself): keep the branch. Do not push, merge or open a PR. Print the summary and the rulings list as the closing message. Do not commit: uncommitted work stays uncommitted.

## 5. Execute the choice

- **Merge locally:** from the main checkout, inspect `bash <skill-dir>/../swarm/scripts/superlite-cli session status`, `git status --short`, and `git worktree list --porcelain` before choosing the merge location. Do not switch or merge in a checkout hosting another session, even if its run is finished, or one with uncommitted work. If the base branch is already checked out in such a checkout, stop and ask the user to finish or park that work before continuing; never force a second checkout. Otherwise use the base branch's existing clean, unoccupied checkout, or create a temporary checkout when the main checkout cannot be used (`.claude/worktrees/merge-<base>`; enter it with `EnterWorktree path=` under Claude Code, else `cd`; the plain `git worktree add` form is under "Other harnesses (fallback)" below). If the main checkout is safe to use and the base is not checked out elsewhere, check out the base there. Record the base branch's HEAD SHA before merging, then merge the feature branch in the chosen checkout. If the merge was not a fast-forward or had conflicts, read `**Full suite:**` in the plan header: for `yes`, run `bash <skill-dir>/../swarm/scripts/superlite-cli run_tests --full --reuse`; otherwise run `bash <skill-dir>/../swarm/scripts/superlite-cli run_tests --affected --base <pre-merge base sha>` there again. Remove any temporary merge checkout created by this step. Close the session (step 6), then remove the worktree (step 7), then delete the feature branch with `git branch -d`. Finish by running `bash <skill-dir>/../swarm/scripts/superlite-cli session status`: each session now behind main shows `main:+N`, and is offered the update in its own chat. Nothing is synced automatically.
- **Push and PR:** `git push -u origin <branch>`, then `gh pr create` with a title and a body holding the summary and test evidence. Report the PR URL. Keep the branch. Close the session (step 6), then remove the worktree (step 7).
- **Keep:** report the branch name. If the worktree is clean, close the session (step 6), then remove it (step 7). If it has uncommitted work, leave it and report the path.

## 6. Close the session

Once the work is merged, has a PR, or is kept, and the agent is back in the main checkout, run `bash <skill-dir>/../swarm/scripts/superlite-cli session close --slug <slug> --keep-branch`. It marks the session finished, puts the main checkout back on the base branch when `<slug>` was its active session, and removes a parallel worktree that `ensure` cut (the branch stays). Run it before the worktree cleanup below. Skip it when the run has no session.

If it prints an `ASK` line (exit 4, nothing changed), ask exactly what its `ask:` line says, then do exactly what the chosen answer's `on ...:` line says (on a refusal, stop; nothing changed).

## 7. Worktree cleanup

A flow removes every worktree superlite cut before it ends: `superlite-cli session close` / `proto discard` remove them (`session: closed <slug>; removed its worktree`). Leave the worktree first (`ExitWorktree` with `action: "keep"` under Claude Code, else `cd` to the main checkout); `ExitWorktree` never removes a superlite-cut worktree. Claude-created worktrees (`session: create` / `proto: create` then `EnterWorktree name=`, the desktop app, `claude --worktree`) end with `ExitWorktree` `remove` (or `keep`) or the app's cleanup; `session close` and `proto discard` only archive and unregister them, and superlite never removes them or their branch. Never remove one this flow did not cut.

### Other harnesses (fallback)

Without `EnterWorktree`: `cd` back to the main checkout, then `git worktree remove <path>`; a temporary merge checkout is `git worktree add .claude/worktrees/merge-<base> <base>`.

- Merged: remove the worktree, then `git branch -d` the feature branch.
- Pushed: remove the worktree. Leave the branch.
- Kept, and `git status --short` in that worktree is empty: remove the worktree. Leave the branch.
- Kept, with uncommitted work: leave the worktree and name its path. Do not use `--force`.

A prototype sandbox is removed with `superlite-cli proto discard` at feature finish, not with this step.

## Never

- Push or merge without the human's answer.
- Force-push, or delete a branch with unmerged work.
- Proceed with failing tests.
- Ask which base branch to use.
