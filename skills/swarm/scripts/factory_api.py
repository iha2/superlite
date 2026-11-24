"""factory_api: error types and the superlite prompt helper."""
from __future__ import annotations

import os
import shlex
import sys
from collections.abc import Mapping
from pathlib import Path

_HERE_DIR = str(Path(__file__).resolve().parent)
if _HERE_DIR not in sys.path:
    sys.path.insert(0, _HERE_DIR)

from cloud_client import CloudError  # noqa: E402

SUPERLITE_PREAMBLE = ("Build this with superlite: use the superlite skills through your Skill tool, starting with "
                      "superlite:using-superlite and superlite:start (slash commands may not exist here), and set the "
                      "AI orchestrator yourself with `superlite-cli orchestrator set ai`. "
                      "Treat the text below as the plan or request.")


# A set variable here means an agent harness runs this process (CLAUDECODE=1 is Claude Code's own value).
AGENT_ENV = ("CURSOR_AGENT", "COPILOT_CLI", "CODEX_SANDBOX", "CODEX_THREAD_ID", "CODEX_CI")


class FactoryError(CloudError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class Unsupported(FactoryError):
    def __init__(self, factory: str, verb: str):
        super().__init__(f"{factory}: {verb} is not supported")
        self.factory = factory
        self.verb = verb


def require(provider, verb: str) -> None:
    if verb not in provider.caps:
        raise Unsupported(provider.name, verb)


def superlite_prompt(text: str) -> str:
    return SUPERLITE_PREAMBLE + "\n\n" + text


def _env_hints(cmd: str, env: Mapping[str, str]) -> list[str]:
    """One line per terminal the environment shows; each is a hint for the agent, never an instruction."""
    q = shlex.quote(cmd)
    hints = []
    if env.get("HERDR_ENV") == "1":
        hints.append(f"HERDR_ENV=1 (herdr): `herdr pane split --current --direction right --no-focus`, "
                     f"then `herdr pane run <id> {q}` with the pane id it prints")
    if env.get("TMUX"):
        hints.append(f"TMUX is set (tmux): `tmux split-window -h {q}`")
    if env.get("WEZTERM_PANE"):
        hints.append(f"WEZTERM_PANE is set (wezterm): `wezterm cli split-pane --right -- bash -lc {q}`")
    if env.get("KITTY_WINDOW_ID"):
        hints.append(f"KITTY_WINDOW_ID is set (kitty): `kitty @ launch --location=vsplit bash -lc {q}`")
    if env.get("TERM_PROGRAM"):
        hints.append(f"TERM_PROGRAM={env['TERM_PROGRAM']}")
    if env.get("WSL_DISTRO_NAME") or env.get("WSL_INTEROP"):
        hints.append("WSL: `wt.exe` can open a Windows Terminal tab or pane")
    if sys.platform == "darwin":
        hints.append("macOS: `osascript` can open a Terminal or iTerm window")
    return hints


def watch_handoff(cmd: str, label: str, env: Mapping[str, str] | None = None) -> str:
    """The prompt-hook context that asks the agent to open a watch terminal for the user; it opens nothing itself."""
    text = (f"The user wants to watch {label} live. Open a new terminal, tab or pane for the user, beside this "
            f"session, that runs `{cmd}`, using whatever this harness offers. Never run it inline here: it does "
            f"not exit and would block this session. If you cannot open one, show the user the exact command to run.")
    hints = _env_hints(cmd, os.environ if env is None else env)
    if hints:
        text += "\nEnvironment hints (not instructions):\n" + "\n".join("- " + h for h in hints)
    return text


def agent_without_tty(env: Mapping[str, str] | None = None, stdout=None) -> bool:
    """True when an agent harness runs this process and its stdout is no terminal: a watch here would block it.

    SUPERLITE_WATCH_INLINE=1 turns the check off (test harnesses, scripts that read the stream)."""
    env = os.environ if env is None else env
    if env.get("SUPERLITE_WATCH_INLINE") == "1":
        return False
    out = sys.stdout if stdout is None else stdout
    try:
        if out.isatty():
            return False
    except (AttributeError, ValueError):
        pass
    return env.get("CLAUDECODE") == "1" or any(env.get(k) for k in AGENT_ENV)


def watch_command(kind: str, args: list[str]) -> str:
    """The shell command for `superlite-cli <kind> watch <args>`, as a user's terminal should run it."""
    cli = Path(__file__).resolve().parent / "superlite-cli"
    return " ".join(["bash", shlex.quote(str(cli)), kind, "watch", *map(shlex.quote, args)])


def main(argv: list[str]) -> int:
    """`factory_api handoff cloud|factory [watch args...]`: the watch_handoff text, for hooks/pre-tool's denial."""
    if len(argv) < 2 or argv[0] != "handoff" or argv[1] not in ("cloud", "factory"):
        print("usage: factory_api handoff cloud|factory [watch args]", file=sys.stderr)
        return 2
    args = [a for a in argv[2:] if a != "--follow"]
    label = next((a for a in args if not a.startswith("-")), "a run")
    print(watch_handoff(watch_command(argv[1], args), label))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
