# Feature: Web prototypes use the repo's UI components by default

## Goal

When feature mode builds a UI prototype, the new screen is composed from the target repo's own UI components. A try-out should look like that app, with the new screen on it.

Who it is for: someone running feature mode on an app that already has shared UI components (buttons, fields, layout, and the rest of that set).

## What changes

**Web (the only target that changes)**

Today the prototype keeps the app shell, the route, and the page being changed. A new screen may be written as one-off markup, and the shared components are kept only when that page already imports them.

After this change, research names the repo's UI component set in the code map: the directory or package existing screens import for buttons, fields, and layout. The prototype keeps that set, and the new screen is composed from those components. One-off markup is only when the code map says the repo has no UI component set. The set is not stubbed and not dropped. The API behind the screen stays a fake, as it does today.

```text
sandbox
  App shell
    /new-screen
      repo components (Page, Field, Button, ...)
    /api/...  --->  fake data
  UI component set kept on purpose
```

**cli, library, api, other**

Unchanged.

**Where the default is written**

- `skills/feature-prototype/SKILL.md` — the rule agents follow when they cut the container.
- `commands/feature.md` — step 2 (the code map names the set, or says there is none) and step 4 (keep the set; do not treat it as "the rest" that gets stubbed).
- `docs/feature-flow.md` — the container description.
- `skills/writing-plans/SKILL.md` — a web feature plan's code map and prototype scope name the set.
- `tests/scripts/test_feature_flow.py` — the phrases `UI component set` and `one-off markup` are required in those four files.

No new `superlite.json` key and no component scanner. The overlay cannot replace skills, and this default is the instruction agents already load.

## Acceptance checks

1. `skills/feature-prototype/SKILL.md` says a web prototype keeps the repo's UI component set and composes the new screen from it.
2. The same skill says one-off markup is only when the code map says there is no UI component set, and that the set is not stubbed.
3. `commands/feature.md` step 2 names the UI component set in the code map (or says there is none). Step 4 keeps that set for a web UI.
4. `docs/feature-flow.md` says the same in the prototype container section.
5. `skills/writing-plans/SKILL.md` tells a web feature plan to name the UI component set in the code map and to keep it in the prototype scope.
6. `pytest tests/scripts/test_feature_flow.py::test_skills_and_docs_name_the_feature_flow -q` passes, and that test requires the phrases `UI component set` and `one-off markup` in the four files above.
7. The skill still names `web`, `cli`, `library`, `api`, `other`, `L1`, `L2`, `stub`, and `8 tasks`. `commands/feature.md` still has its six steps and the phrases that test already requires.

## Code map

**Entry / how this repo stands up**

- Feature mode: `commands/feature.md` (diagram `assets/flows/feature.txt`). Prototype container: `skills/feature-prototype/SKILL.md`, started with `sl proto start` (`skills/subagent-driven-development/scripts/proto.py`).
- Tests: `tests/scripts/test_feature_flow.py`, helper `text(*parts)`, pytest. No server.

**Files this feature touches, and the seam**

| File | Seam |
|---|---|
| `skills/feature-prototype/SKILL.md` | The keep/stub rule. The web row and a short web-UI default. |
| `commands/feature.md` | Steps 2 and 4 only. Steps 1, 3, 5, 6 and the stored diagram stay. |
| `docs/feature-flow.md` | The container bullet under step 4. `Status: built` stays. |
| `skills/writing-plans/SKILL.md` | One sentence in the feature-plan bullet. Plan lint and the task format stay. |
| `tests/scripts/test_feature_flow.py` | `test_skills_and_docs_name_the_feature_flow` gains the two phrases. Existing assertions stay. |

**Interfaces that must not change (FROZEN)**

- `sl proto` verbs, `.superlite/feature.json` statuses, L1/L2, the 8-task prototype cap, try-out approval.
- The six numbered steps in `commands/feature.md`, and the phrases `test_feature_command_has_the_six_steps` and `test_each_mode_command_prints_its_own_stored_diagram` already require.
- `superlite.json` allowed keys (`project_config.py`). No new key.
- MVP prototypes (`skills/mvp-prototype`) stay one-file HTML.
- cli, library, api, and other rows in the prototype skill.

**Conventions / toolchain**

- LF line endings. Markdown skills. Tests in `tests/scripts/` via pytest.
- Research for this plan: three read-only Explore passes (container rules, phrase tests, overlays). `sl graphify_gate` returned `use: true` (already installed). A graph extract would not change this touch set.

## Prototype scope

**Keep:** the feature-prototype skill and `test_skills_and_docs_name_the_feature_flow`. The prototype proves the new sentences and the test pin.

**Stub:** no app server, no fixture UI app, no scanner. Command, feature-flow, and writing-plans wording are the integration port of the same sentences (they are the same rule, not a second behavior).

**Level:** L1.

**Run:** `python -m pytest tests/scripts/test_feature_flow.py::test_skills_and_docs_name_the_feature_flow -q` inside the prototype worktree.

This repo has no UI component set of its own. The code map says so. The prototype is the rule text and the test, not a screen.

## Integration outline

1. Port the skill wording and the test pin from the prototype.
2. Put the same sentences in `commands/feature.md` (steps 2 and 4), `docs/feature-flow.md` (container), and `skills/writing-plans/SKILL.md` (web code map and prototype scope).
3. Extend the test so those three files must contain `UI component set` and `one-off markup`.
4. Seam: `pytest tests/scripts/test_feature_flow.py tests/scripts/test_flow_diagrams.py -q` still passes. No migration.

## Rulings

- Ruling: orchestrator is AI (the user confirmed the default, then asked for AI orchestration).
- Ruling: the default is skill and command prose plus a test pin, not a `superlite.json` key and not a scanner (the overlay cannot replace skills; the user asked for the default agents follow when they cut a UI prototype).
- Ruling: MVP one-file prototypes stay as they are (the request is feature-mode UI prototypes).
- Ruling: this worktree was cut from `main` (the session branch had no commits of its own and was behind `main`, with another feature's uncommitted edits).
- Ruling: skip a fresh Graphify extract (the gate allows it; the touch set is the five files above).
- Ruling: the prototype build is inline in its sandbox (a few files, one chain). A task graph would be larger than the edit.
