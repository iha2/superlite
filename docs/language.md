# Language

The tools are Python, but the project can be in any language. `run_tests` uses the project's own test command (`.superlite/test-cmd.json`, else detection from its marker files). The probe stage (`probe_run`) is Python only.

## Swift / xcodebuild

- Pipe `xcodebuild` through `xcsift` (or any reporter that prints JSON with `passed_tests` / `failed_tests` arrays or integer counts). `testcmd.parse_output` reads those fields so a green package run is not reported as `passed: 1`.
- Contract checks locate `.swift` files under `Sources/` / `src/` and treat them as present; they do **not** introspect Swift signatures (Python `importlib` only). Prefer seam notes over expecting part-1 signature diffs for Swift `Produces` lines.
- Parallel implementers editing different files in one Swift target can still make `xcodebuild` exit 65 while another task's half-written file is on disk. Prefer one task per module when the target shares a compile graph, or serialize `run_tests` for that package. Superlite does not take a compile lock.

## Multi-repo sessions

- Run state follows the plan's repo (`stable_root` / `sl session`): keep each plan under that repo's tree (for example `docs/plans/` or `.superlite/` inside the repo you are building). A plan path in repo A with a shell cwd in repo B still writes FROZEN / dispatch state next to the plan.
- `sl finish` and hooks resolve the nearest project `.superlite` (and active session) from the cwd; they do not archive a second sibling checkout. One superlite session per repo checkout (or git worktree).
