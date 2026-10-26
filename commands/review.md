---
description: Review and harden an existing branch: reviewers fix their own findings, the final review reports tiers.
argument-hint: "[branch or path]"
---

Review and harden. Target: $ARGUMENTS (default: the current branch against its base).
Load `superlite:swarm`. Package reviewers fix their own findings with regression tests; then a fresh `superlite:final-reviewer` reads the review packet and reports findings in tiers 1 to 3 without editing. Use the project's own test command. Do not push.
