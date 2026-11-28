"""commands/send.md and commands/factory.md run `superlite-cli factory ...`; marketplace lists superlite-warp;
help, intro, tools and README mention the factory commands."""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "swarm" / "scripts"


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def spans(cmd: str) -> list[list[str]]:
    body = read(f"commands/{cmd}.md").split("\n---\n", 1)[1]
    return [s.split() for s in re.findall(r"`([^`]+)`", body)]


def frontmatter(cmd: str) -> dict:
    """Strict-enough YAML-ish parse: a plain scalar may not contain ': ' or ' #'; quoted ones must be JSON."""
    m = re.match(r"---\n(.*?)\n---\n", read(f"commands/{cmd}.md"), re.S)
    keys = {}
    for ln in m.group(1).splitlines():
        key, sep, val = ln.partition(": ")
        assert sep and re.fullmatch(r"[A-Za-z][\w-]*", key), f"{cmd}: bad frontmatter line {ln!r}"
        val = val.strip()
        if val[:1] in ('"', "'"):
            assert val[-1] == val[0] and len(val) > 1, f"{cmd}: unterminated quote in {ln!r}"
            if val[0] == '"':
                val = json.loads(val)
            else:
                val = val[1:-1]
        else:
            assert ": " not in val and " #" not in val, f"{cmd}: unquoted ': ' in {ln!r}"
        keys[key] = val
    return keys


def test_frontmatter_parser_rejects_unquoted_colon(tmp_path):
    for cmd in ("send", "factory", "attach", "runs", "watch", "give", "detach"):
        assert frontmatter(cmd)["description"]


def test_warp_is_optional_in_docs():
    for rel in ("README.md", "docs/tools.md", "commands/factory.md"):
        t = read(rel)
        assert "optional" in t and "superlite-warp" in t and "built in" in t, rel


def test_send_and_factory_commands():
    for cmd in ("send", "factory"):
        keys = frontmatter(cmd)
        assert keys.keys() >= {"description", "argument-hint"} and keys["description"].strip()
    assert ["superlite-cli", "factory", "send", "$ARGUMENTS"] in spans("send")
    assert ["superlite-cli", "factory", "$ARGUMENTS"] in spans("factory")
    assert "--plan" in read("commands/send.md") and "--superlite" in read("commands/send.md")
    for verb in ("list", "add", "use", "login", "logout", "mcp"):
        assert f"`{verb}" in read("commands/factory.md")


def test_orchestrator_flag_documented_and_parsed():
    hints = {c: frontmatter(c)["argument-hint"] for c in ("send", "factory")}
    assert "--orchestrator human|ai" in hints["send"] and "--orchestrator human|ai" in hints["factory"]
    assert "--orchestrator" in read("commands/send.md") and "Reply to answer" in read("commands/send.md")
    assert "use <name> [--target T] [--superlite|--no-superlite] [--orchestrator human|ai]" in read("commands/factory.md")
    assert "Cloud Human runs" in read("skills/swarm/orchestrator.md")
    for sub in ("send", "use"):
        r = subprocess.run([sys.executable, str(SCRIPTS / "factory.py"), sub, "--help"], capture_output=True,
                           text=True, timeout=30)
        assert r.returncode == 0 and "--orchestrator {human,ai}" in r.stdout, sub


def test_run_commands_mention_factory_addresses():
    for cmd in ("attach", "runs", "watch", "give", "detach"):
        text = read(f"commands/{cmd}.md")
        assert ["superlite-cli", "cloud", cmd, "$ARGUMENTS"] in spans(cmd)
        if cmd != "runs":
            assert "<factory>:<ref>" in text or cmd == "detach"
        assert "factory" in text


def test_factory_subcommands_exist():
    for sub in ("send", "list", "mcp"):
        r = subprocess.run([sys.executable, str(SCRIPTS / "factory.py"), sub, "--help"], capture_output=True,
                           text=True, timeout=30)
        assert r.returncode == 0, r.stderr


def test_marketplace_lists_warp():
    plugins = {p["name"]: p for p in json.loads(read(".claude-plugin/marketplace.json"))["plugins"]}
    assert plugins["superlite-warp"]["source"] == {"source": "github", "repo": "iha2/superlite-warp"}
    assert plugins["claude-agent-map"]["source"]["source"] == "github"
    assert "superlite" in plugins


def test_help_mentions_factory():
    assert "/superlite:send" in read("assets/help.txt") and "/superlite:factory" in read("assets/help.txt")
    assert "/superlite:factory" in read("assets/intro.txt")
    assert "superlite-cli factory" in read("docs/tools.md")
    assert "/superlite:factory" in read("README.md") and "superlite-warp" in read("README.md")
    assert max(len(ln) for ln in read("assets/help.txt").splitlines()) <= 80
    assert max(len(ln) for ln in read("assets/intro.txt").splitlines()) <= 80
