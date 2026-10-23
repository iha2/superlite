# Feature: project extendability

## Goal

A team using Superlite on an existing repo can tell Superlite how that repo builds, tests, and reviews, without a fork of the plugin and without a plugin loader.

Who it is for: someone running Superlite on a project whose language, agent cap, ignore paths, or host models differ from the shipped defaults. Superlite's own maintainers use the same mechanism in fixture repos. The seven agents, the plan schema, and the flows stay the product.

This is one feature with two parts, built in one integration worktree (`superlite/project-extendability`). Part 1 is language neutrality. Part 2 is the project overlay. They do not share a loader.

## What changes

**Commands and files (library / CLI)**

1. **Language neutrality for the project under build.** `.superlite/test-cmd.json` keeps its current fields. There is no `check` command. `sl plan_tools stubs` keeps writing Python stubs when the language is Python. For any other language it prints a stub spec and writes no `.py` files. A seam check creates a seam in the project's language: Python still writes `.superlite/probes/adapter.py`. Any other language gets a seam file in that language, and a missing Python adapter does not fail the run.
2. **Project overlay.** A committed `superlite.json` at the root of the project being built. Missing file means today's defaults. It may set the dispatch cap, extra gitignore lines, host job candidates, test-probe thresholds, and model prices. Shipped JSON under `skills/subagent-driven-development/config/` stays the default. An explicit CLI flag or the existing `SUPERLITE_TESTPROBE_CONFIG` env var wins over the file. It does not name skills.

**What a person sees differently**

- On a TypeScript (or other non-Python) repo, stubs are a spec, and the seam check writes a seam in that language instead of `adapter.py`.
- With `superlite.json` present, `sl dispatch` starts at most `cap` tasks, ignore paths include the extra lines, and host resolve uses the project's candidate slugs.

**Not in this feature**

- A mod loader, new agent roles, new flows, or new plan fields.
- Replacing `routing.json` rules. Critical-path tasks still never use the haiku tier; that stays in code.
- Per-language stub templates. The stub spec is the contract; the implementer writes the file in the project's language.
- Editing the body of `skills/using-superlite/SKILL.md` (it is already 2007 bytes; tests cap it at 2000).
- Collapsing the copied agent lists in harness tool maps.
- Putting the overlay inside `.superlite/`. That directory is run state and is gitignored, so the team would not share it.
- Reading project skills. Session start, the prime, and the review packet do not name them, and implementers are not told to read them. A project skill that says to ask or wait would sit in the same context as the autonomy rule, and nothing in Superlite overrides that text.

## Acceptance checks

A human can try these on fixture repos after the prototype, and again after integration:

1. **Python path unchanged.** A Python fixture with no `superlite.json`: `sl run_tests` still uses pytest, `sl plan_tools stubs` still emits `raise NotImplementedError`, and the seam check still writes `.superlite/probes/adapter.py` and compares signatures with Python `ast`. `tests/scripts` that cover those paths still pass.
2. **Non-Python tests.** A fixture whose `.superlite/test-cmd.json` sets `language` to `typescript` and a `test` command: `sl run_tests --fast` does not invoke pytest. `test-summary.json` still has `passed`, `failed`, `skipped`, `seconds`, `mode`, `failures`.
3. **Stub spec.** On that same fixture, `sl plan_tools stubs` prints one `stub:` line per `Produces` signature (`name(params) -> ret`) and a one-line contract, and creates no `.py` file.
4. **Language-specific seam.** On a non-Python fixture, the seam check writes a seam file in that language and exits 0 with no `adapter.py`. On a Python fixture it still writes `adapter.py`. A missing `adapter.py` fails the run only when the language is Python.
5. **Consumer rerun.** The consumer's tests still rerun with `SUPERLITE_REAL=1` through `test-cmd.json` for every language.
6. **Overlay defaults.** A fixture with no `superlite.json`: dispatch cap stays 10, gitignore entries match `config/gitignore.json` plus the plan's `**Ignore:**` line, host candidates match `config/hosts/<host>.json`.
7. **Overlay applied.** A fixture `superlite.json` with `"cap": 2`, an extra gitignore line `generated/`, a Cursor `build` candidate list of one slug, a testprobe threshold, and a model price: dispatch starts at most 2 tasks when `--cap` is omitted; `generated/` is ignored; host resolve writes that slug; the price used for the run is tagged `source=project`. Passing `--cap 10` uses 10.
8. **Bad overlay.** A `superlite.json` that is not valid JSON: `sl dispatch` and `sl plan_tools lint` exit 2 with one line naming the file. They do not silently use defaults.
9. **Routing product stays.** A `superlite.json` cannot assign the haiku tier to a critical-path task. Shipped `routing.json` rules still decide tiers. A `skills` key is an unknown key and exits 2.

## Code map

**How a run is stood up today**

- Session start (`hooks/session-start`) injects `skills/using-superlite/SKILL.md` plus `Project state: …`. Cursor receives `additional_context`. Claude Code and Muse receive `hookSpecificOutput.additionalContext`. Other hosts receive top-level `additionalContext`. Pi (`.pi/extensions/superlite.ts`) and OpenCode (`.opencode/plugins/superlite.js`) do not run that hook; they read the same skill file into their own bootstrap.
- `lint --stable` writes `.superlite/stable.json` (tiers from `routing.load_cfg()` → `config/routing.json`).
- `sl dispatch` defaults the cap to 10 (`dispatch.py`, `next_tasks.py`, `plan_tools.DEFAULT_CAP`).
- Wave 0 calls `gitignore.ensure` from `config/gitignore.json` (keys must match `wave0.LANGS`) plus the plan header `**Ignore:**`.
- Tests: `testcmd.resolve` prefers `.superlite/test-cmd.json`, else `detect()` (package.json, go.mod, Cargo.toml, sln/csproj, then Python). Python sets `command` to null and `run_tests.run` calls pytest. Anything else uses `run_other`.
- Stubs: `plan_tools.parse_signature` and `build_stub_defs` emit Python and `compile()` it.
- Seams: `pt_review.cmd_adapter` writes `.superlite/probes/adapter.py` and `compile()`s it. `parse_sig`, `compare_sig`, and `check_interfaces` use Python `ast`. The consumer rerun sets `SUPERLITE_REAL=1`.
- Probes: `probe_run` exits 2 when `.superlite/probes/adapter.py` is missing. `probes/adapter_template.py` is Python-only.
- Review packet: `sl plan_tools packet` → `pt_review.cmd_packet` → `.superlite/review-packet.md` (sections 1–8, default 20000 token cap). `agents/final-reviewer.md` reads that file only.
- Finish: `finish.py` prints the run summary. It has no skill list.
- `SUPERLITE_HOME` is history and the intro marker, not config. The only config env override today is `SUPERLITE_TESTPROBE_CONFIG`.

**Entry points**

| Area | Path |
|---|---|
| Test command | `scripts/testcmd.py` (`detect`, `resolve`), `scripts/run_tests.py` (`run`, `run_other`) |
| Stubs | `scripts/plan_tools.py` (`parse_signature`, `build_stub_defs`, `cmd_stubs`) |
| Seams and packet | `scripts/pt_review.py` (`parse_sig`, `compare_sig`, `check_interfaces`, `cmd_packet`) |
| Probes | `probes/probe_run.py`, `probes/adapter_template.py` |
| Ignore and languages | `scripts/wave0.py` (`LANGS`), `scripts/gitignore.py` (`load`, `ensure`), `config/gitignore.json` |
| Cap and tiers | `scripts/dispatch.py`, `scripts/next_tasks.py`, `scripts/routing.py` (`load_cfg`) |
| Hosts and prices | `scripts/host_models.py` (`load_host_cfg`, `resolve`), `scripts/predict.py` (`load_models`), `config/hosts/cursor.json`, `config/models.json` |
| Probe thresholds | `scripts/testprobe.py` (`load_config`), `config/testprobe.json` |
| Session inject | `hooks/session-start`, `.pi/extensions/superlite.ts`, `.opencode/plugins/superlite.js` |
| Prime and finish | `scripts/build_prime.py`, `scripts/finish.py` |
| Known-runner rewrite | `hooks/rewrite-test-runs` (`load_project_runner` already treats `test-cmd.json` as a runner) |

**Touched files (expected)**

- New: `scripts/project_config.py` (load and merge `superlite.json`), tests `tests/scripts/test_project_config.py`.
- Edit: `plan_tools.py`, `pt_review.py` (language-specific seam from `cmd_adapter`), `probe_run.py`, `gitignore.py`, `dispatch.py`, `next_tasks.py`, `host_models.py`, `predict.py` (and the metrics price read), `testprobe.py`, `docs/config.md`.
- Leave: `skills/using-superlite/SKILL.md`, `hooks/session-start`, the Pi and OpenCode bootstraps, `build_prime.py`, `finish.py`, `config/routing.json` rules, `hooks/rewrite-test-runs` regex, `agents/final-reviewer.md`, `bench/` results.

**Interfaces that must not change**

- `test-summary.json` keys listed in acceptance check 2, and the `sl run_tests` flags (`--fast`, `--full`, `--background`, `--wait`) and the `passed failed skipped seconds` stdout line.
- Existing `test-cmd.json` fields (`language`, `test`, `test_file`, `junit`, `setup`, optional `related` and `env`). No new field.
- Dispatch line grammar (`fresh` / `agent=` / `tier=`).
- `stable.json` tier keys. Critical path never haiku.
- Hook JSON field names per host.
- Final reviewer input is still only the packet. The packet gains no project-skill section.
- Python behavior in acceptance check 1.

**Conventions and toolchain**

- Tools run as `bash …/scripts/sl <tool>`. Tests are pytest in `tests/scripts`.
- LF line endings. Hooks stay bash. PreToolUse hooks stay builtin-only. Session start is unchanged.
- New config does not go through `.version-bump.json` until a release.

## Decisions

- **One project file, `superlite.json`, at the repository root of the project being built.** Version field `1`. Keys: `cap` (number), `gitignore` (array of strings, appended), `hosts` (host name → job name → `candidates` array), `testprobe` (object of threshold overrides), `models` (price overrides). Unknown keys, including `skills`, are an error (exit 2), so a typo does not vanish.
- **Merge direction.** Shipped file, then overlay, then an explicit CLI flag or `SUPERLITE_TESTPROBE_CONFIG`. Gitignore lines append; they never remove a shipped entry. Host `candidates` replace that job's list when the overlay names the job. Model prices from the overlay are used and tagged `source=project` in the cost line.
- **Malformed file fails closed** for `dispatch`, `lint`, and `stubs`. A missing file is not an error.
- **A seam is a file in the project's language.** Python keeps `adapter.py`. Another language gets its own seam file from the same seam step. The seam check does not run a compiler command.
- **Project skills are not read.** Autonomy stays the stop rule already in `using-superlite`: the AI stops only for destructive, outward, or security-sensitive actions. No overlay key changes that.
- **Prototype level L1.** The non-Python fixture's seam is a file the seam step writes. No real toolchain install and no secrets.

## Prototype scope

**Keep**

- `sl` entry point, `testcmd.resolve`, `run_tests`, `plan_tools stubs`, `pt_review` seam creation and packet, `probe_run`, `gitignore.ensure`, `dispatch` cap, `host_models.resolve`.
- Fixture repos inside the sandbox: one Python, one non-Python with a shell `test`.

**Stub**

- Pi and OpenCode bootstraps. Session start is unchanged.
- A real compiler. The seam is the file the seam step writes.
- Live host model APIs. Candidates are the strings in the fixture `superlite.json`.

**Drop**

- Harness manifests, `bench/`, agent definitions, routing rule edits.

**Level:** L1. Acceptance is CLI output, fixture files, and unit tests. At most 8 prototype tasks, about a quarter of the integration work.

## Integration outline

Port from the prototype. No prototype fixture script ships as production code.

1. `project_config.py` and its tests (missing, valid, malformed, unknown key, flag wins).
2. Non-Python stub spec in `plan_tools.py`; Python emission unchanged.
3. Language-specific seam from the seam step. Python still writes `adapter.py` and still uses `ast`. Another language writes its own seam file. A missing `adapter.py` fails the run only for Python.
4. Consumer rerun stays `SUPERLITE_REAL=1` through `test-cmd.json`.
5. Wire the overlay into gitignore append, dispatch cap, host candidates, testprobe thresholds, and tagged model prices. `skills` is rejected.
6. `docs/config.md` documents `superlite.json`. It does not document a skill list or a `check` field.
7. A fixture run with no `superlite.json` matches current dispatch, session-start, finish, and Python stub tests. Existing `tests/scripts` pass.
