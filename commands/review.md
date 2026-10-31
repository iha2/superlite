---
description: Review and harden an existing branch: reviewers fix their own findings, the final review reports tiers.
argument-hint: "[branch or path]"
---

Review and harden. Target: $ARGUMENTS (default: the current branch against its base).
Once the request is known, and BEFORE loading the skill, run `sl session ensure --slug <topic>` (`<topic>`: a short slug of the request, a-z, 0-9, `-`; reuse it for plan file names). On a `SESSION worktree=<path>` line, move the agent root to `<path>` when it differs from the current checkout; on `REDIRECT worktree=…`, open that worktree instead.
If ensure (or `use`/`close`) prints `ASK untracked=<N> slug=<s>: <files>` and exits 4, nothing changed. Ask in ONE AskUserQuestion: "Stash with <s>" / "Keep in the folder" / "Always stash (this repo)" / "Always keep (this repo)".
For an "Always" choice run `sl session untracked stash|keep` first; then rerun the same command with `--untracked stash|keep`.
Load `superlite:swarm`. Package reviewers fix their own findings with regression tests; then a fresh `superlite:final-reviewer` reads the review packet and reports findings in tiers 1 to 3 without editing. Use the project's own test command. Do not push.
