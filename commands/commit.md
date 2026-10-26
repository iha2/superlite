---
description: Turn superlite's hidden checkpoints into real commits on the current branch (one per task), after you confirm.
---

superlite does not commit by default: each task is a hidden checkpoint under `refs/superlite/*` and the work is uncommitted in the tree.
1. Run `bash "$SL" autocommit status` (`SL` = `<superlite skill dir>/swarm/scripts/sl`) and show the user the checkpoint list (task, short SHA, title, file count) and the current branch.
2. Ask for a yes. Do not run anything else until it comes.
3. On yes, run `bash "$SL" autocommit apply` and print its output. It makes one commit per task in order with the same titles and no trailer, then deletes the checkpoints. If it refuses (HEAD moved or unrelated changes in the tree), print why and stop; the user can run `git add -A && git commit` instead.
`bash "$SL" autocommit clean` discards the checkpoints; `bash "$SL" autocommit on` makes later runs commit each task on the branch.
