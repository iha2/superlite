# Installing Superlite for OpenCode

## Prerequisites

- [OpenCode.ai](https://opencode.ai) installed

## Installation

OpenCode V2 requires version 2.0.4 or later.

### OpenCode V1

Use the existing V1 plugin configuration:

```json
{
  "plugin": ["superlite@git+https://github.com/iha2/superlite.git"]
}
```

### OpenCode V2 (2.0.4 or later)

Use the V2 plugin configuration:

```json
{
  "plugins": ["superlite@git+https://github.com/iha2/superlite.git"]
}
```

For a local V2 installation, configure the repository directory containing
`index.js`. OpenCode 2.0.4 and 2.0.7 reject a configured direct JavaScript-file
path. Discovered plugin symlinks remain supported.

Restart OpenCode. V2 uses the `opencode` command; `opencode2` may be available
as an alias. The plugin installs through OpenCode's plugin manager and
registers all skills.

Verify by asking: "Tell me about your superlite skills"

OpenCode uses its own plugin install. If you also use Claude Code, Codex, or
another harness, install Superlite separately for each one. A superlite run
suspends the plugin it was derived from until `sl finish`; they share skill names.

## Usage

Use OpenCode's native `skill` tool:

```
use skill tool to list skills
use skill tool to load brainstorming
```

## Updating

OpenCode installs Superlite through a git-backed package spec. Some OpenCode
and Bun versions pin that resolved git dependency in a lockfile or cache, so a
restart may not pick up the newest commit. If updates do not appear, clear
OpenCode's package cache or reinstall the plugin.

To pin a specific version, add a tag or commit to the spec (same form for the
V1 `plugin` key and the V2 `plugins` key):

```json
{
  "plugin": ["superlite@git+https://github.com/iha2/superlite.git#v0.1.0"]
}
```

## Troubleshooting

### Plugin not loading

1. Check logs. V1: `opencode run --print-logs "hello" 2>&1 | grep -i superlite`.
   V2 loads plugins in the background server, so add `--standalone`:
   `opencode run --standalone --print-logs "hello" 2>&1 | grep -i superlite`,
   or inspect `~/.local/share/opencode/log/opencode.log` filtering for `role=server`.
2. Verify the plugin line in your `opencode.json`
3. Make sure you're running a recent version of OpenCode

### Windows install issues

Some Windows OpenCode builds have upstream installer issues with git-backed
plugin specs. If OpenCode cannot install the plugin, install with system npm
and point OpenCode at the local package:

```powershell
npm install superlite@git+https://github.com/iha2/superlite.git --prefix "$HOME\.config\opencode"
```

Then use the absolute path of the installed package in `opencode.json`.
OpenCode does not expand `~`; a `~/...` entry is treated as a package name,
not a local directory.

**V1:**

```json
{
  "plugin": ["C:\\Users\\<you>\\.config\\opencode\\node_modules\\superlite"]
}
```

**V2 (2.0.4 or later):**

```json
{
  "plugins": ["C:\\Users\\<you>\\.config\\opencode\\node_modules\\superlite"]
}
```

### Skills not found

1. Use the `skill` tool to list what's discovered
2. Check that the plugin is loading (see above)

### Tool mapping

Skills speak in Claude Code's tool names. The plugin injects a flavor-specific mapping:

**V1 (`opencode` 1.x):**

- Todo or plan list -> `todowrite`
- `Agent` dispatch -> `task` tool with `subagent_type: "general"` (or `"explore"` for codebase exploration)
- Load a skill -> OpenCode's native `skill` tool
- Read a file -> `read`
- Create, edit or delete a file -> `apply_patch`
- Run a shell command -> `bash`
- Search file contents or find files -> `grep`, `glob`
- Fetch a URL -> `webfetch`

**V2 (`opencode` 2.0.4 or later):**

- Todo or plan list -> V2 has no todo tool; track the plan in a markdown file instead
- `Agent` dispatch -> `subagent` tool with `agent: "general"` (or `"explore"`); pass `sessionID` to continue a previous subagent (this is `SendMessage`)
- Load a skill -> OpenCode's native `skill` tool
- Read a file -> `read`
- Create, edit or delete files -> `patch` with `patchText` when available; otherwise `write`, `edit` and `shell`
- Run a shell command -> `shell`
- Search file contents or find files -> `grep`, `glob`
- Fetch a URL -> `webfetch`; search the web -> `websearch`

OpenCode has no plugin agents. To run superlite's `implementer`, `implementer-haiku`, `package-reviewer`, `final-reviewer` or a fixer (`fixer-sonnet`, `fixer-sonnet-high`, `fixer-opus`) (tests run through `sl run_tests`, not an agent), dispatch the generic subagent with the full text of `agents/<name>.md` (minus frontmatter) followed by the brief as its prompt, on the model tier named in that file.

## Getting Help

- Report issues: https://github.com/iha2/superlite/issues
- Full documentation: https://github.com/iha2/superlite/blob/main/docs/README.opencode.md
