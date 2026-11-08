"""superlite:researcher: flat agent file, guard allowlist, host agent lists."""
import json
import re
from pathlib import Path

from test_human_gate import guard, make

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / "agents" / "researcher.md"


def fm():
    m = re.match(r"---\n(.*?)\n---\n", AGENT.read_text(encoding="utf-8"), re.S)
    assert m
    return dict(ln.split(": ", 1) for ln in m.group(1).splitlines())


def test_frontmatter():
    k = fm()
    assert k["name"] == "researcher" and k["model"] == "sonnet"
    assert k["tools"] == "Read, Write, Grep, Glob, Bash, WebSearch, WebFetch"


def test_body_rules():
    t = AGENT.read_text(encoding="utf-8")
    for w in ("superlite-cli research", "source", "claim", "contradiction", "lint", "date", "grade"):
        assert w in t, w


def test_guard_allows_researcher_only(tmp_path):
    p = make(tmp_path, "human")
    for st in ("superlite:researcher", "researcher"):
        assert guard(p, "Agent", {"subagent_type": st, "prompt": "x"}).returncode == 0, st
    for st in ("general-purpose", "superlite:implementer", "superlite:fixer-opus", "researcherX", "evil:researcher", "superlite:researcherX", "Researcher", ""):
        assert guard(p, "Agent", {"subagent_type": st, "prompt": "x"}).returncode == 2, st


def test_tools_exclude_edit():
    tools = {x.strip() for x in fm()["tools"].split(",")}
    assert not tools & {"Edit", "MultiEdit"}


def test_host_lists_name_all_agents():
    names = [p.stem for p in (ROOT / "agents").glob("*.md")]
    assert len(names) == 8
    kimi = json.loads((ROOT / ".kimi-plugin/plugin.json").read_text())["skillInstructions"]
    oc = (ROOT / ".opencode/plugins/superlite.js").read_text(encoding="utf-8")
    for n in names:
        assert n in kimi and n in oc, n


def test_host_lists_name_researcher():
    assert "researcher" in (ROOT / ".opencode/plugins/superlite.js").read_text(encoding="utf-8")
    assert "researcher" in json.loads((ROOT / ".kimi-plugin/plugin.json").read_text())["skillInstructions"]
