---
description: "Manage factories: list, add, use, login, logout, mcp. superlite-cloud is built in; plugin kinds (for example the optional superlite-warp, only if you use Warp Factories) come from installed plugins."
argument-hint: "list | add <name> --kind K | use <name> | login <name> | logout <name> | mcp <name> on|off"
---

Run `superlite-cli factory $ARGUMENTS` and print its output only. Change nothing else.

- `list` shows the factories and which is active (`*`) or attached (`@`).
- `add <name> --kind K [--target T] [--superlite]` registers an instance of a plugin kind.
- `use <name> [--target T] [--superlite|--no-superlite]` makes it the active factory.
- `login <name> [--token TOKEN]` and `logout <name>` store or clear its token.
- `mcp <name> on|off` turns the factory's MCP server on or off for this project.

superlite-cloud is built in. `superlite-warp` is an optional plugin: install it only if you use Warp Factories.
