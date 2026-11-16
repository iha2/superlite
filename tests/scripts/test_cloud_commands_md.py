"""commands/runs|watch|attach|detach|give.md: each has flat frontmatter with description and argument-hint, and each body says the
prompt hook answers the slash command first, with `superlite-cli cloud <cmd> $ARGUMENTS` as the fallback that prints only its output."""
import importlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "swarm" / "scripts"
CMDS = ["runs", "watch", "attach", "detach", "give"]


def read(cmd: str) -> str:
    return (ROOT / "commands" / f"{cmd}.md").read_text(encoding="utf-8")


def frontmatter(text: str) -> dict:
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert m, "no frontmatter"
    return dict(ln.split(": ", 1) for ln in m.group(1).splitlines() if ": " in ln)


@pytest.mark.parametrize("cmd", CMDS)
def test_files_exist(cmd):
    keys = frontmatter(read(cmd))
    assert keys.keys() >= {"description", "argument-hint"}
    assert keys["description"].strip()


def fallback_command(cmd: str) -> list[str]:
    """Parse the `superlite-cli cloud <sub> $ARGUMENTS` fallback out of the body's inline code spans."""
    body = read(cmd).split("\n---\n", 1)[1]
    spans = [s.split() for s in re.findall(r"`([^`]+)`", body)]
    found = [s for s in spans if s[:2] == ["superlite-cli", "cloud"]]
    assert len(found) == 1, found
    return found[0]


@pytest.mark.parametrize("cmd", CMDS)
def test_body_says_hook(cmd):
    assert fallback_command(cmd) == ["superlite-cli", "cloud", cmd, "$ARGUMENTS"]


@pytest.mark.parametrize("cmd", CMDS)
def test_hook_routes_the_slash_command(cmd):
    sys.path.insert(0, str(SCRIPTS))
    try:
        hook = importlib.import_module("cloud_hook")
    finally:
        sys.path.remove(str(SCRIPTS))
    assert hook._SLASH.match(f"/superlite:{cmd} x")


@pytest.mark.parametrize("cmd", ["runs", "give"])
def test_fallback_subcommand_exists_in_cloud_cli(cmd):
    sub = fallback_command(cmd)[2]
    r = subprocess.run([sys.executable, str(SCRIPTS / "cloud.py"), sub, "--help"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert f"cloud {sub}" in r.stdout
