---
name: feature-prototype
description: Use in feature mode after the feature plan is approved, to cut the smallest runnable container of an existing app for the prototype build
---

# Feature prototype

The prototype is the smallest thing that stands up and shows the feature working, inside just enough of the original app to run. It lives in a sandbox folder inside the checkout, `.superlite/sandboxes/<session>/<topic>/` (`host` when there is no session), made by `superlite-cli proto start`, is built by a normal superlite build scoped to that folder, and is tried by the human (`superlite-cli proto tryout`) before the integration build touches the real code. Its code is later ported, never merged, so stubs and fakes never reach the user's branch.

## Keep, stub, drop

- **Keep:** the app's entry point and how it starts, the screens, routes, commands or calls the feature touches, and their direct dependencies. Keep the project's own language, framework, toolchain and conventions; never rewrite into another stack.
- **Stub:** everything the kept code calls that the feature does not change: the database becomes a seeded fixture or an in-memory store, external services become fakes returning the plan's example values, auth becomes one fixed test user, config uses test values with no real secrets.
- **Drop:** screens, commands and modules the feature never reaches. Do not delete them in the sandbox if the build needs them to compile; leave them untouched and unused.

## Web UI component set

When the target is a web UI, the code map names the repo's UI component set: the directory or package existing screens import for buttons, fields, and layout. The prototype keeps that set, and the new screen is composed from those components. Use one-off markup only when the code map says the repo has no UI component set. The set is not stubbed and not dropped.

## By target kind

| Kind | Keep | Stub | The try-out |
|---|---|---|---|
| web | the app shell, routing, the pages the feature changes, the repo's UI component set, their API calls | the backend behind those calls (a fake handler or fixture data), auth, analytics | a URL and port; show it in a preview pane |
| cli | the entry point, argument parsing, the commands the feature adds or changes | the file system or network the command reaches (a temp folder, fixtures) | a terminal session: the commands to type and what they print |
| library | the public modules the feature extends and their callers' types | heavy dependencies the new calls do not need | example calls with their results, or a short script |
| api | the server start, the routes the feature touches, request and response models | the data store (in-memory or seeded), downstream services (fakes) | example requests (curl or an HTTP file) and the responses |
| other (pipeline, bot, job) | the stage or handler the feature changes and its input and output formats | the sources and sinks (fixture input, a local output folder) | a run command on a sample input and the expected output |

## Real code in place

The sandbox holds only the new code and the stand-ins. Reference the app's real code where it is; do not copy it in.

- **Python:** `PYTHONPATH` or a `sys.path` insert pointing at the checkout's source.
- **Node:** `tsconfig` `paths`, or a relative import out of the sandbox.
- **Go:** a `replace` directive in the sandbox's `go.mod`.
- **Rust:** a path dependency in `Cargo.toml`.
- **JVM:** a source set that includes the checkout's source folders.
- **.NET:** a project reference to the real project.

Inject stand-ins at the sandbox entry point. Copy one real file only when it hard-wires a dependency that cannot be injected, and say so in the plan.

## Sandbox levels

- **L1 stubs (default):** the sandbox reuses the checkout's installed dependencies and every service the feature does not change is stubbed. Seconds to set up.
- **L2 isolated environment:** only when the plan says the feature depends on real data shapes (a schema change, a migration, a query over real-looking data): the sandbox gets its own dependency install, a seeded fixture database with realistic shapes, a copied config with secrets replaced by test values, and offset ports. Record the level and why as a `Ruling:` and pass `--level L2` to `superlite-cli proto start`.
- Never real secrets or production data in either level.

## The prototype plan

- When `superlite-cli agent_specs status` is on, keep the feature `.spec.md` in view (acceptance from the spec); optional task-local notes stay under the sandbox's `.superlite/specs/`.
- An agent-format plan in the sandbox, `<sandbox>/docs/plans/<date>-<topic>.proto.md`, whose `Owns` paths all stay inside the sandbox, at most 8 tasks and about a quarter of the integration estimate (cost and time). A larger plan means the scope is wrong: cut it back to what the acceptance checks need.
- Wave 0 sets up the container (the stubs, fakes and fixtures, the run command); the feature tasks then run in parallel, Haiku for mechanical pieces and Sonnet for the rest.
- Its tests are written against the feature plan's acceptance checks, so they carry into the integration build as its acceptance tests.
- Tests run by path: `superlite-cli run_tests --fast <sandbox>/tests`.
- The run command goes into `superlite-cli proto start --run "<command>"` (or `superlite-cli proto tryout --run`), and how to reach it into `--reach`.
