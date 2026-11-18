# Feature plan: factory plugins (+ superlite-warp)

**Spec:** docs/plans/2026-11-18-factory-plugins.spec.md
**Full suite:** yes (once at finish)

## Goal

Make "factory" a pluggable provider in superlite so runs can go to more than superlite-cloud. Then ship the first outside provider, `superlite-warp`, as its own plugin repo for Warp Factories. It is for superlite users whose team runs work on a Warp factory (or a future factory) and who want to dispatch, follow and steer it from their session.

## What changes

CLI (`superlite-cli factory …`, new):
- `factory list`: one row per factory with name, kind, target and login state. The active one is marked `*`, the attached one `@`, and a broken plugin shows as `error: …`.
- `factory add <name> --kind <kind> [--target …]`: add another factory of a kind, for example `factory add web --kind warp --target web-app`.
- `factory use <name> [--target <factory name or uid>]`: set the active factory. For Warp, this also picks the Warp factory.
- `factory login <name> [--token …]` / `factory logout <name>`: the token comes from the flag, the manifest's env var (`WARP_API_KEY`) or the clipboard. It is stored with mode 0600 and never printed.
- `factory mcp <factory> on|off`: opt in to that factory's MCP server for Claude in this project. It writes a `.mcp.json` entry with an env-var token reference and never the token. It prints that MCP acts with the account's full permissions.
- `factory send "<prompt>" | --plan <file> [--title …] [--base …] [--superlite]`: dispatch a run and print its id, status and URL.
- `--superlite` (generic, any kind): for a factory whose environment has superlite installed, marked with `factory add|use … --superlite`. superlite adds a fixed preamble to the prompt telling the remote agent to build it with superlite (AI orchestrator, plan from the attached text) and passes `superlite=True` to the provider so a kind can trigger it natively. On a factory not marked this way it prints `<factory>: superlite is not enabled` and exits 2. Warp environments can install custom Claude Code plugins, so a Warp factory with superlite installed is a real target: the superlite-warp README covers installing superlite in the Warp environment and marking the factory `--superlite`. Tests check the request shape only. A real remote superlite run is future work, because we can't assume Warp access.
- `factory runs|watch|attach|detach|give|nudge|cancel`: the same verbs as `cloud`, sent to the active factory.

Slash commands: new `/superlite:send` and `/superlite:factory`. The existing `/superlite:runs|attach|detach|watch|give` commands and `>>` lines go to the active factory.

Addressing: a run is `<factory>:<ref>`, for example `payments:1a2b3c4d`.
- `/superlite:attach payments:1a2b3c4d` attaches to that run whichever factory is active.
- `>> web:9f8e7d6c text` sends one message to a run on another factory and leaves the attachment as it is.
- A bare ref is looked up on the attached factory first, then the active one.
- Once two or more factories are logged in, every routed message confirms where it went (`→ payments (warp) 1a2b3c4d: follow-up sent`), and the status line shows `payments(warp) 1a2b3c4d running`. With only superlite-cloud, nothing changes.

Attach works the same for every factory: `/superlite:attach <run>`, plain lines and `>>` go to the run, `/cancel`, auto-detach when the run is gone, the watch pane, the status line segment and the per-run `/superlite-attach-…` / `/superlite-watch-…` entries.

Unchanged: `superlite-cli cloud …` and how superlite-cloud runs look in all of the above.

New repo `../superlite-warp`: a Claude Code plugin with `superlite-factory.json`, a stdlib provider `factory/warp.py`, tests against a fake Warp server, and a README (setup: API key, `factory use warp --target …`, and installing superlite in the Warp environment for `--superlite`). The superlite-dev marketplace lists it as github `iha2/superlite-warp`. You create and push that repo yourself.

## Acceptance checks

1. `superlite-cli factory list` shows `* superlite-cloud (built-in)` and `warp  <path>` when superlite-warp is on `SUPERLITE_FACTORY_PATH` (or installed).
2. `superlite-cli cloud runs` prints the same output as before, and every existing test still passes.
3. `factory login warp --token wk-test…` prints `warp: logged in` and no token. `factory use warp --target payments` prints `warp: payments (<uid>)`.
4. `factory send "Fix flaky test"` prints `<run8> queued <run_url>`. `--plan docs/plans/x.md` sends the plan text.
5. `factory runs` (or `/superlite:runs`) with Warp active lists runs as `<run8> running "Fix flaky test"`, with statuses mapped.
6. `factory watch <run8>` prints `status: running`, then the agent's text and `tool: run_command pytest …` lines once each, then `status: done` (and the session link), and exits.
7. After `/superlite:attach <run8>`, `>> also add a regression test` reaches the run as a follow-up, and `/cancel` cancels it.
8. `factory give <run8> a@b.c` on Warp prints `warp: give is not supported` and exits 2.
9. A plugin folder with a broken `superlite-factory.json` shows `error: …` in `factory list`, and superlite-cloud still works.
10. While attached to a Warp run, a plain line (no `>>`) reaches the run. `/superlite:detach` stops that. If the run is deleted or access is lost (403 or 404), superlite detaches with one notice.
11. The status line shows the attached Warp run and the active factory's runs. When a Warp run finishes or blocks, it raises one notice.
12. A live Warp run shows up as `/superlite-attach-<factory>-<run8>` and `/superlite-watch-<factory>-<run8>`, and both go away when the run ends.
13. With superlite-cloud and Warp factory `payments` both logged in, `/superlite:attach payments:1a2b3c4d` attaches to the Warp run. A plain line then prints `→ payments (warp) 1a2b3c4d: follow-up sent`.
14. While attached there, `>> superlite-cloud:chris/login_fix check the tests` reaches the superlite-cloud run instead and prints `→ superlite-cloud (superlite-cloud) chris/login_fix: nudge sent`. The attachment stays on `payments`.
15. `factory mcp superlite-cloud on` adds the `superlite-cloud` entry to `.mcp.json`, which then contains `${SUPERLITE_CLOUD_TOKEN}` and not the token. `off` removes the entry and leaves the rest of the file unchanged.
16. With superlite-warp installed, Claude can call Warp's `list_factories` after a browser sign-in. The plugin's skill says to use `get_task start_working=true` to pull a task down and `send_task` to hand it back.
17. After `factory use payments --superlite`, `factory send --plan docs/plans/x.md --superlite` posts a prompt that starts with the superlite preamble followed by the plan text. On a factory without `--superlite`, the same command prints `payments: superlite is not enabled` and exits 2.
18. The hermetic e2e suite passes in both repos, and the fake servers record the expected requests.

## End-to-end tests

Every factory action is an HTTP request, so the e2e tests run the real entry points as subprocesses (`superlite-cli factory …`, `superlite-cli cloud hook` fed through `hooks/prompt`, `cloud_watch`, `cloud_status`) against stdlib fake servers: a fake Warp server and the existing `cloud_fake`. Each test checks both what the user sees and the requests the server got: method, path, `Authorization` header and JSON body (for example `POST /factory/{uid}/runs {prompt,title}`, `POST /agent/runs/{id}/followups {message}`, `POST /api/tasks {repo,prompt,agent,base}`). Plugins are found through `SUPERLITE_FACTORY_PATH`, and `SUPERLITE_HOME` is isolated. The tests also check that no token appears in stdout, stderr or any file except the 0600 store.

- **Hermetic e2e** (the only e2e, always run, in both repos): acceptance checks 1, 3–15 and 17 as subprocess flows. That covers the full attach cycle (attach, plain line, `>>` to another factory, `/cancel`, auto-detach on 404), watch until a terminal state, status line notices, per-run entries, `.mcp.json` on/off, and the `--superlite` preamble.
- **No live tests.** We can't assume Warp access, so nothing calls real Warp or superlite-cloud. A live suite is future work.

## Code map

- **Entry:** `skills/swarm/scripts/superlite-cli <tool>` runs `<tool>.py` (Python >=3.10). `cloud.py:157 main` is the `cloud` dispatcher (`_DELEGATES` :17 → `cloud_watch`, `cloud_status`, `cloud_hook`). Commands `commands/{attach,detach,runs,watch,give}.md` say "the prompt hook answers first, else run `superlite-cli cloud <verb>`". `hooks/prompt:33-45` sends `>>`, `/superlite:(attach|detach|runs|watch|give)` and lines while attached to `superlite-cli cloud hook`.
- **Touched modules and seams:**
  - `cloud_client.Client` (url, token; `me, status, runs, task, nudge, respond, cancel, give, attach`): becomes the contract for the built-in provider.
  - `cloud_stream.events(url, token, ref, after)`: the SSE reader, wrapped as `events()`.
  - `cloud_config` (`SUPERLITE_CLOUD_*`, `slc_` token, `~/.superlite/cloud.json`).
  - `cloud_hook._client()` :41 and `cloud_watch` (`render` :34 with kinds nudge, rename, given, status, result; `handle_input` :62): the seams where the active factory replaces the hard-wired Client.
  - `state.DIR_NAME` gives `~/.superlite`.
  - superlite-cloud has a create route, `POST /api/tasks {repo, prompt, agent, base, name?}`, that takes the same `slc_` token (superlite-cloud `api.py:63`).
- **Frozen:** `cloud <verb>` output, `Client` signatures, `cloud.json`, `attach.json` `{run,id}` (an optional `factory` key is added; absent means superlite-cloud), `hooks/prompt` prefixes, `project_config.KEYS` (no new superlite.json key).
- **Tests:** `tests/scripts/test_cloud_*.py`. `cloud_fake.py` is a real localhost HTTP+SSE fake. Most tests use a `FakeClient` or monkeypatch `urlopen`. `conftest.py` isolates `SUPERLITE_HOME`. `test_cloud_commands_md.py` checks the command docs. Run with `pytest` (`-n auto`).
- **Conventions:** stdlib only. Siblings are imported by putting the scripts dir on `sys.path`. Docs to update: `docs/tools.md`, `assets/help.txt`, `assets/intro.txt`, `README.md`. Versions change only through `scripts/bump-version.sh`. No code finds other plugins today. `installed_plugins.json` (`installPath`, `scope`, `projectPath`) is the source, and marketplace.json already lists a sibling GitHub plugin (`claude-agent-map`).
- **Warp API** (OpenAPI `warpdotdev/docs developers/agent-api-openapi.yaml`): `GET /factory?search=` returns `{factories:[{uid,name,alias}]}`. `POST /factory/{uid}/runs {prompt,title?}` returns `{run_id,run_url,state}`. `GET /agent/runs` and `GET /agent/runs/{id}` return a RunItem (`run_id,title,state,session_link,status_message,conversation_id`). `GET /agent/runs/{id}/conversation?scope=full` returns `steps[].messages[].content[]` blocks (`text`, `action{name,input}`). The API also has `POST …/followups {message}` and `POST …/cancel`. Errors are RFC 7807.

## Decisions

- Decided: a plugin adds a kind (superlite-warp adds `warp`), and every Warp detail lives in that plugin. superlite core knows only the generic contract, the `superlite-factory.json` manifest and the built-in `superlite-cloud` kind. It finds kinds by scanning plugins for the manifest.
- Decided: providers follow the existing `Client` methods plus `events`, `send`, `name` and `caps`, which keeps the cloud refactor small (spec "Decided").
- Decided: the active factory drives the run commands, and two new commands are added (`send`, `factory`). Warp runs target factories only. The marketplace entry uses github `iha2/superlite-warp`.
- Decided: Warp watch polls state and the conversation (your "both"). It shows status changes, text and actions, deduplicated.
- Decided: factories are named instances of a kind, and runs are addressed `<factory>:<ref>`. Messages confirm their target once two or more factories are logged in, so existing superlite-cloud output and tests stay the same.
- Decided: MCP is an optional channel for the agent. Kinds declare it in the manifest, and `factory mcp` turns it on per project. superlite-warp bundles Warp Factory MCP and a skill. Hooks and scripts stay on REST. Pulling a Warp task down to build it with superlite is a later feature.
- Decided: Warp gets the same attach mechanism as superlite-cloud, including the status line and per-run entries. Warp has no notices API, so the provider makes notices from state changes it has seen.
- Ruling: discovery reads `installed_plugins.json` (user scope, or a projectPath that contains the cwd) plus `SUPERLITE_FACTORY_PATH`. Other harnesses use the env var, because no harness-neutral plugin list exists.
- Decided: `--superlite` is a generic interface: an instance flag `superlite`, a core-owned preamble, and `send(…, superlite=True)` for kinds that can trigger it natively. Warp can install custom Claude plugins, so this works on Warp once superlite is installed in the environment. Only the request shape is tested. A real remote run is future work, because we can't assume Warp access.
- Assumption: Warp has no "whoami" endpoint, so `me()` returns `{handle: <target_name>, role: "member"}`. `run_url` / `session_link` is the run URL, and `BLOCKED` maps to `waiting`.

## Prototype scope

Level L1, in `.superlite/sandboxes/factory-plugins/factory-plugins/`. It keeps the real `cloud_client`, `cloud_stream` and `cloud_watch.render` as imports and builds new copies of `factory_api`, `factory_registry`, `cloud_factory`, the `factory` CLI and `warp.py`. A small fake Warp HTTP server (stdlib) stands in for Warp, with stub superlite-cloud via the existing `cloud_fake`. Acceptance checks 1, 3–15 and 17 are hermetic e2e tests (see End-to-end tests) run by path. Check 16 needs a real Warp browser sign-in, so the tests only check the shipped `.mcp.json` and skill text, and the sign-in itself isn't verified. Check 2 is the existing suite, which is untouched in the prototype.

## Integration outline

- superlite worktree (`superlite/factory-plugins-int`). Eight modules: `factory_api.py` (contract, `Unsupported`, `FactoryError`, status set), `factory_registry.py` (discovery, manifest load, `factories.json` instances, active, `resolve(address)` for `<factory>:<ref>`), `cloud_factory.py` (built-in adapter: Client, cloud_stream, send), `factory.py` CLI, a seam task (`cloud_hook`, `cloud_watch` and `cloud.py` use the attached or active factory, and `render` gains `text`/`tool`), a status task (`cloud_status` and `cloud_entries` read every logged-in factory, with labels `<factory>-<run8>`), `factory mcp` (manifest `mcp`, `.mcp.json` merge, superlite-cloud entry), and docs and commands (`send.md`, `factory.md`, help, intro, tools, README, marketplace entry). A seam check task runs the existing cloud tests together with the new ones. An e2e task ports the prototype's hermetic e2e suite.
- New repo `../superlite-warp` (git init, no remote). Plugin manifests plus `.mcp.json` (Warp Factory MCP) and a `warp-factory` skill, `factory/warp.py` (client, status mapping, conversation poller), a fake-server test suite and a README.
- The target is the prototype's acceptance and e2e tests, with all existing tests passing and the full suite run once at finish.
