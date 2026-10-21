# superlite: language-agnostic design

Status: partly built (updated 2026-10-18, repo 950bd9a). Written first as a draft with no code; the build status is in section 0 and each design section below carries a status line. Date: 2026-10-18.

## 0. Build status

| Piece | State |
|---|---|
| Test command resolution (`testcmd.py`): `.superlite/test-cmd.json` (`language`, `test`, `test_file` with `{files}`, `junit`, `setup`), else detection from `package.json` `scripts.test` (JUnit flag for vitest, jest-junit for jest), `go.mod`, `Cargo.toml`, `*.sln` / `*.csproj`, then Python (`pyproject.toml`, `pytest.ini`, `setup.cfg`, `tox.ini`, `conftest.py`, `test_*.py`); otherwise exit 2 with a `no test command found` summary | **Built** (494f423). The file is JSON, not `test-cmd.yaml` as first proposed |
| `run_tests` for any language: parses JUnit XML when the runner wrote one, else exit code plus the last 40 output lines (`test-output.log`) with failing names extracted best effort; `test-summary.json` keeps its shape; background and `--wait` unchanged | **Built** (`run_other`, `finish_run`) |
| Wave 0's T0 writes and verifies `test-cmd.json` on the stub tree; the Wave 0 brief carries a language-neutral YAML stub spec limited to the Produces other tasks consume | **Built** (`wave0.py`); the brief is hand-built text, not a converted data file |
| Server probe adapter defines `start_server` (the missing function caused 90 of 116 `exception` incidents in w3) | **Built**, Python adapter only |
| Hooks and tools that are language neutral already: guard, test-run rewrite (pytest, npm, pnpm, yarn, bun, vitest, jest, go, cargo, dotnet), capture hooks, `dispatch`, `commit_task`, lint's graph and size checks | **Built** |
| Agent views through `to_yaml` and `config/views.yaml`; `test-summary.yaml` | **Not built, dropped**: measured, see 3.3 |
| Stub spec instead of Python stubs: `plan_tools stubs` | **Not built**: it still emits Python (`NotImplementedError`) |
| Seam signature check with the project's own compiler or type-checker | **Not built**: `pt_review.py` still compares signatures with Python `ast`; the consumer rerun with `SUPERLITE_REAL=1` is neutral and exists |
| Probes written in the project's language, a neutral probe catalog, process-level probes | **Not built**: probes are Python only (the adapter skips other languages) |
| Neutral wording in skills and agents | **Partly**: `swappable fake` is neutral; some text still names `pytest` |
| Proof on a non-Python task | **Done** for TypeScript with vitest (section 5); Go, Rust and .NET detection exists with tests but no benchmark run |

## 1. Problem

superlite's tools are written in Python; that is only their runtime. But several tools also assume the **project being built** is Python. A grep of the current code found:

| Assumption | Where | Effect on a non-Python project |
|---|---|---|
| `pytest` hard-coded | `run_tests.py`, `pt_review.py`, `_pyrun.py` (27 hits in tools, 7 in skills and agents) | no test results, no seam reruns |
| Python `ast` / `inspect` for signatures | `pt_review.py` (19 hits) | seam signature checks fail or are skipped |
| Stubs emitted as Python (`NotImplementedError`) | `plan_tools.py` (3) | wrong stub files |
| `uv`, `.venv`, `pyproject` detection | tools (5) | wrong environment |
| Pytest fixture wording for fakes | implementer agent, skills (2) | the rule is unusable |
| Function-level probes call Python functions | `probe_run`, probe pack | not runnable |
| Benchmarks | every task is Python | no evidence it works elsewhere |

Status of this table (2026-10-18): the `pytest` hard-coding in `run_tests` and the missing `start_server` in the probe adapter are fixed (section 0); the rest of the rows still hold.

Language-neutral already: `dispatch`, `commit_task` (scope rules), lint's graph and size checks, incidents, fix notes, the predictor formula, the Graphify gate.

## 2. Principles

1. **Use the project's own language and tooling first; agents handle the rest.** superlite's tools may stay Python, but anything the project's own test runner, compiler, type-checker, package manager or declared scripts can answer is answered there, not re-implemented in superlite. Agents write the remaining glue on the fly. No per-language template library to maintain.
2. **One test-result format in: JUnit XML.** pytest, Jest, Vitest, Go (gotestsum), cargo-nextest and dotnet all emit it.
3. **Agents read compact, purpose-built views, never raw XML or pretty JSON.** (Revised 2026-10-18 after measuring: the view is a short whitelisted line format or compact JSON, not YAML; see 3.3.) Files on disk may be any format; what is sent to an agent is converted first by one deterministic tool.
4. **Neutral by default, language-specific as an optional extra.** A check that cannot run for a language is skipped and logged, never faked.
5. Skill and agent text uses neutral terms and, where it needs an example, more than one language.

## 3. Design

### 3.1 `test-cmd.yaml`, written once per project
**Status: built as `.superlite/test-cmd.json`** (same fields; `test_file` is a template with `{files}`, and `{junit}` in a command is replaced by the path `run_tests` wants). The YAML below is the original proposal.

A Wave 0 step has an agent work out how to run the project's tests and write `.superlite/test-cmd.yaml`:
```yaml
language: typescript
test: npx vitest run --reporter=junit --outputFile=.superlite/junit.xml
test_file: npx vitest run {file} --reporter=junit --outputFile=.superlite/junit.xml
junit: .superlite/junit.xml
slow_markers: [e2e]
```
**Prefer what the project already declares.** The agent reads the project's own entry points before inventing anything, in this order: `package.json` `scripts.test`, a `Makefile` or `justfile` `test` target, `Cargo.toml` (`cargo test`), `go.mod` (`go test ./...`), `*.sln` / `*.csproj` (`dotnet test`), `pyproject.toml` / `pytest.ini` / `tox.ini`, then the CI workflow files. It only adds the missing piece (a JUnit reporter flag) and records the result.

The agent **validates it** by running it on the stub tree: tests must run and fail on `not implemented`, with a JUnit file produced. If validation fails the task is BLOCKED, not guessed. Auto-detection from marker files (`package.json`, `go.mod`, `Cargo.toml`, `*.csproj`, `pyproject.toml`) only suggests the starting point.

### 3.2 `run_tests`
**Status: built, except the agent view.** There is no `test-summary.yaml`; reviewers and fixers read the short summary line and `test-summary.json` as before.

Runs `test` or `test_file` from `test-cmd.yaml`, reads the JUnit XML (fallback: exit code plus the last 40 output lines), and writes:
- `.superlite/test-summary.json`: the machine file tools read (unchanged name and shape).
- `.superlite/test-summary.yaml`: the agent view, via the converter. Reviewers, fixers and the review packet read this one.

The `--background` / `--wait` behaviour is unchanged. The pytest fallback in `_pyrun.py` is used only when `language: python` and no command is given.

### 3.3 Agent views (revised: measured, YAML dropped)
**Measured 2026-10-18** on real run files, real input tokens (same data sent to a model, difference from an empty prompt, 4 datasets, about $0.03):

| Data | JSON pretty | JSON compact | YAML | YAML vs pretty | YAML vs compact |
|---|---|---|---|---|---|
| stable.json | 2736 | 2272 | 2523 | -8% | **+11%** |
| pool.json | 512 | 350 | 357 | -31% | +2% |
| 12 test summaries | 1394 | 1106 | 1150 | -18% | +3% |
| v2 per-task records | 3920 | 4024 | 4110 | +4% | +2% |

**Conclusion: YAML views were dropped.** On three of the four datasets YAML is 8 to 31% smaller than pretty-printed JSON (on the fourth, the per-task records, it is 4% larger), and it is 2 to 11% **larger** than compact JSON on all four. Its win over pretty JSON comes only from dropping indentation and braces, and compact JSON gets the same saving. The earlier "20-30% saving" estimate was wrong. The absolute amounts are also small (a few hundred tokens per file read), so the format is not where tokens are saved. What does save tokens: (1) show agents short purpose-built lines instead of files (lint's `DISPATCH NOW:` and the `sl budget` line already do), (2) whitelist the fields an agent needs (the per-task record has 35 fields, an agent needs about 8), (3) write machine files compactly. So: no `to_yaml`, no YAML dependency; `config/views.yaml` becomes a list of file -> fields to keep, rendered as one compact line per item.

(Original proposal kept below for the record.)

### 3.3 (superseded) The converter: `sl to_yaml <file>`
Not built and not planned: see the measurement above. Decision D43 in the architecture spec.

Deterministic, no model. Inputs: JUnit XML, JSON, JSONL. Keeps a whitelist of fields agents use and prints a restricted YAML subset (scalars, lists, maps; no anchors), written by hand so there is no PyYAML dependency. A JSON file becomes an agent view by being **registered** in `config/views.yaml` (file, fields to keep); whatever a tool sends to an agent from a registered file goes through the converter and arrives as YAML. Disk stays JSON, so tools need no YAML parser. Registered: test summary, `stable.json`, incident hot spots, pool and dispatch status.
```yaml
passed: 412
failed: 2
time_s: 34.1
failures:
  - id: tests/sheet.test.ts > range sum > huge range
    error: "Timeout: exceeded 2000 ms"
```
Expected saving: about 20-30% over JSON for this shape, much more than that over JUnit XML (tags repeat per test case). To be measured, not assumed.

### 3.4 Stubs on the fly
**Status: partly.** The Wave 0 brief already carries a language-neutral stub spec (name, params, return from the Produces lines) for the `Kind: mechanical` T0 agent; `plan_tools stubs` itself still emits Python.

`plan_tools stubs` stops emitting Python. It emits a language-neutral **stub spec** (name, params, return, one-line contract, from the `Produces` lines). Wave 0 holds a `Kind: mechanical` task, run by `implementer-haiku`, that writes the stub files in the project's language from that spec. The task is done only when the project's own compile or type-check passes on the stub tree. Lost: a script can no longer check stubs match the plan exactly. Covered by: the seam check compiles or reruns every consumer against the real producer, so a stub that drifted fails there.

### 3.5 Seam checks
**Status: the neutral part exists; the compiler-based signature check is not built.** The seam check should use the project's own compiler or type-checker (`tsc --noEmit`, `go build`, `cargo check`, `dotnet build`, `mypy` or `pyright`); today `pt_review.py` parses signatures with Python `ast`.

- **Language-neutral part, for every language:** rerun the consumer's tests against real code. The existing convention `SUPERLITE_REAL=1` is an environment variable, which every language can read. Run through `test-cmd.yaml`.
- **Signature comparison uses the project's own compiler or type-checker, not a parser of ours.** For typed or compiled languages, compile or type-check the consumer's code against the real producer: `tsc --noEmit`, `go build ./...` / `go vet`, `cargo check`, `dotnet build`, and `mypy` / `pyright` when a Python project already uses one. A mismatch then shows up as a compile error with the language's own precise message, and superlite carries no signature parser. For untyped languages the rerun of consumer tests is the check. The Python `ast` reader stays only as a fallback for a Python project with no type-checker.
- Plan notation stays abstract: `name(params) -> ret`, compared by parameter names and count.

### 3.6 Probes: written in the project's own language
**Status: not built.** Probes and the probe adapter are Python only; for other languages the probe stage is skipped and logged.

How superpowers does it: it has **no probes**. It stays language-agnostic only by wording: its skills name the project's test command with several examples (`npm test` / `cargo test` / `pytest` / `go test`, "whatever the repo uses") and leave everything else to the agent. There is no mechanism to copy, but the lesson holds: name things neutrally and let the agent work in the project's language.

Design:
- **A language-neutral probe catalog** (YAML) replaces the Python probe pack as the source of knowledge: per category (deep, numeric, encoding, protocol, file, state) a list of cases, each with an input description and parameters (nesting depth 5000, a 400-digit literal, a lone surrogate, a path with `..`) and the **invariant** to check (no crash, returns within 2 s, typed error, no partial write).
- The plan's Review Focus picks categories and risks per package. The implementer writes them as **tests in the project's own framework** during TDD (already lever 1), or a `Kind: mechanical` task does it for existing code. The catalog gives the cases; the agent translates them.
- `probe_run` runs those tests through `test-cmd.yaml` in parallel processes with timeouts, and adds **process-level probes** for programs (run the CLI or server, send input, check exit code, status and output), which need no language support at all.
- The Python probe pack and adapter stay as the reference implementation and fallback for Python projects.

### 3.7 Skills and agents
**Status: partly.** The implementer rule is already neutral (a swappable fake, switched by `SUPERLITE_REAL=1`); `pytest` wording remains in places.

- Implementer rule: "swappable fake": a fake by default and the real producer when `SUPERLITE_REAL=1`, implemented with whatever the language's test framework offers (fixture, environment switch, build tag).
- Replace `pytest`, `.py` and `uv` wording with "the project's test command (`test-cmd.yaml`)" and neutral examples.
- Fixer and reviewer agents read the YAML summary.

## 4. Rollout
Status: step 1 is done without `to_yaml` (JSON instead of YAML, section 3.3); step 2 is partly done; steps 3 and 4 are not done; step 5 is done for TypeScript.

1. `test-cmd.yaml` plus `run_tests` (JUnit in) plus `to_yaml`: removes the pytest hard-coding and the biggest token cost. Python projects keep working through `language: python`.
2. Neutral wording in skills and agents.
3. Stub spec plus the Wave 0 stub task.
4. Seam checks and probes: neutral parts for all, Python extras optional.
5. A non-Python benchmark task (see 5).

## 5. Proof: a tiny TypeScript benchmark
Decided: TypeScript, with a deliberately tiny project. **Pass line: the whole build, tested, costs under $4 and takes under 5 minutes.** Proposed shape: `wordstat`, a CLI that reads text on stdin and prints the top N words: tokenize, count, topN, CLI. About 4 tasks, 150 lines, vitest, about 20 hidden tests, a few probes (huge input, unicode, empty input). Run with the one-rep protocol and `BENCH_BUDGET=4` as the hard cap.

Notes: 5 minutes is tight for planning (about 1.5 min) plus build plus review; it is only plausible because the project is tiny. It also tests whether superlite's fixed overhead (plan, prime, wave 0, final review) is small enough on small jobs.

**Result (single runs, vitest, one superlite rep each; all hidden tests passed):** wordstat 186 s and $0.54 (23/23), textkit 261 s and $0.87 (31/31), logflow 444.6 s and $1.72 (101/101), logflow-plus 593 s and $2.33 (166/166), sitegen 613 s and $2.36 inline (132/132), 582 s and $3.75 with planning forced (131/132), 511 s and $3.39 with planning forced and the fixed plugin (132/132). The pass line (under $4 and 5 minutes) holds for wordstat and textkit, the two tasks it was set for; every later task stayed under $4 but is larger than the tiny project and took longer than 5 minutes. wordstat and textkit were built inline. Details and caveats: architecture spec §12.5.4.

**Prerequisite (historical): npm is broken on this machine.** `npm --version` fails with `EPERM: lstat 'C:\Users\Admin\AppData'`: the nvm4w Node install points into another user's profile. Node itself runs (v24), npm does not. A working Node and npm is needed first, for example a user-local install. That is an install, so it needs your OK. The TypeScript runs above completed, so a working Node and npm was in place by then; how it was fixed is not recorded in the files this update read.

## 6. Risks
- An agent-written `test-cmd.yaml` or stub could be wrong. Mitigation: validation on the stub tree, BLOCKED on failure.
- Some runners cannot emit JUnit. Mitigation: exit code plus output tail, with a one-line failure list.
- Windows paths and quoting in the stored commands. Mitigation: commands run through `bash`, as everywhere else.
- JUnit quirks differ by runner (nested suites, skipped counted as pass). Mitigation: tests per runner using real sample files.

## 7. Decisions (2026-10-18) and what is open
Decided:
1. ~~Disk stays JSON; registered JSON files are converted to YAML when sent to an agent (3.3).~~ **Superseded (2026-10-18, measured):** disk stays JSON and so does what agents read, as short whitelisted lines; no converter (3.3).
2. First proof language: TypeScript, tiny project, under $4 and 5 minutes (5).
3. Probes consider the project's language: catalog, tests written in that language, and process-level probes (3.6).
4. Seam signature check uses the project's own compiler or type-checker, with Python `ast` only as a fallback (3.5).

Open:
- ~~Fix npm before the TypeScript benchmark.~~ Done in effect: the TypeScript runs completed (section 5).
- Build the compiler-based seam check (3.5), the stub spec in `plan_tools stubs` (3.4) and probes in the project's language (3.6), or decide to leave them Python only.
- Benchmark a second language other than TypeScript (Go, Rust or .NET detection is tested but unused in a run).
- Scope of the first probe catalog: port the six existing categories, or start with three (deep, numeric, protocol)?

## Related
- Earlier: [[2026-10-17-superlite-architecture-design]]
- Project: [[Superlite]]

Hub: [[Specs index]]
