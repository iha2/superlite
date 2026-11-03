---
description: Review and harden an existing branch: reviewers fix their own findings, the final review reports tiers.
argument-hint: "[branch or path]"
---

Review and harden. Target: $ARGUMENTS (default: the current branch against its base).
First resolve the review target and its base: a branch named in the arguments, else the current branch. A path argument (a file or folder) is not a branch: the review stays limited to that path and `<target>` is the current branch. Then, BEFORE loading the skill, run `superlite-cli session ensure --slug <topic> --branch <target>` (`<topic>`: a short slug of the request, a-z, 0-9, `-`; reuse it for plan file names) so the session works on that branch (in place when already on it, its worktree otherwise). On a `SESSION worktree=<path>` line, move the agent root to `<path>` when it differs from the current checkout; on `REDIRECT worktree=…`, open that worktree instead.
If ensure prints an `ASK` line (`ASK untracked=...` or `ASK move ...`, exit 4, nothing changed), ask exactly what the `ask:` line after it says, then do exactly what the chosen answer's `on ...:` line says (on a refusal, stop; nothing changed). If the hook printed `superlite sync:`, ask the same way; sync is fast-forward only.
Once the target and its diff are read, run the `superlite:inventing` **Grill** (one round: intent, the risk that matters most, what is out of bounds) when something material is open. Load `superlite:swarm`. Package reviewers fix their own findings with regression tests; then a fresh `superlite:final-reviewer` reads the review packet and reports findings in tiers 1 to 3 without editing. Use the project's own test command. Do not push.
