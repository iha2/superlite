# Superlite for OpenCode

Complete guide for using Superlite with [OpenCode.ai](https://opencode.ai). The step-by-step install, update, Windows and troubleshooting instructions live in [.opencode/INSTALL.md](../.opencode/INSTALL.md); this page is the short version.

## Installation

OpenCode V2 requires version 2.0.4 or later.

**OpenCode V1:**

```json
{
  "plugin": ["superlite@git+https://github.com/iha2/superlite.git"]
}
```

**OpenCode V2 (2.0.4 or later):**

```json
{
  "plugins": ["superlite@git+https://github.com/iha2/superlite.git"]
}
```

For a local V2 installation, configure the repository directory containing `index.js` (a direct JavaScript-file path is rejected). Restart OpenCode and verify by asking: "Tell me about your superlite skills".

## How it works

The plugin (`.opencode/plugins/superlite.js`, re-exported by the root `index.js`) does two things:

1. Registers the repository's `skills/` directory with OpenCode (V1 via the `config` hook, V2 via `ctx.skill.transform`).
2. Injects the `using-superlite` skill plus a tool mapping into the first user message of each top-level session. Task subagent (child) sessions are skipped, so workers do not restart the design cycle.

## Usage

```
use skill tool to list skills
use skill tool to load inventing
```

## Agents

OpenCode has no plugin agents. To run superlite's `implementer`, `implementer-haiku`, `package-reviewer`, `final-reviewer` or a fixer (`fixer-sonnet`, `fixer-sonnet-high`, `fixer-opus`) (tests run through `sl run_tests`, not an agent), dispatch the generic subagent (`task` on V1, `subagent` on V2) with the full text of `agents/<name>.md` (minus frontmatter) followed by the brief as its prompt, on the model tier named in that file.

## Updating

Restart OpenCode; if the update does not appear, clear OpenCode's package cache or reinstall. Pin a version by appending `#v0.1.0` to the git spec.
