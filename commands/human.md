---
description: Switch this project to the human orchestrator: you approve a review plan before any build.
argument-hint: ""
---

Run `sl orchestrator set human`, then `sl orchestrator status`, and confirm in one line. From now on no build is orchestrated (linted or dispatched) until the user approves the review plan; small inline work needs only their yes in chat. After the approval the run decides on its own and lists any scope changes at finish.
