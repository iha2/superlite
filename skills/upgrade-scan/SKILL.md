---
name: upgrade-scan
description: Review project changes and suggest adoption steps after a superlite upgrade
---

Review this repository against the changes in superlite since the previously installed version. Use the current version in `.claude-plugin/plugin.json` and any other `.git/info/superlite-upgrade-scan-*` markers to identify the previous version. Read the release notes and relevant commit messages between those versions when available. If no previous version is known, review the current release notes and recent superlite changes.

Suggest relevant improvements only:

- Find legacy prototype worktrees or branches ending in `-proto`, including worktrees directly under `.superlite/sandboxes/<topic>-proto/`.
- Find worktrees under the old `.superlite/worktrees/` home.
- Suggest any settings, plans, or habits this version handles better.

For each legacy layout, explain how the user could move wanted changes into the current layout or remove the old worktree/branch. Report findings and suggestions only. Do not move, edit, or delete anything; wait for the user's yes before making any change.
