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


RESEARCHERS = ("superlite:researcher", "researcher")
NEAR = ("general-purpose", "superlite:implementer", "superlite:fixer-opus", "researcherX", "evil:researcher",
        "superlite:researcherX", "Researcher", "")


def call(p, st):
    return guard(p, "Agent", {"subagent_type": st, "prompt": "x"}).returncode


def test_researcher_denied_when_gated(tmp_path):
    # It has Write and Bash, so it is not read-only: the approval gate and the try-out gate cover it.
    for state in ("human", "tryout"):
        d = tmp_path / state
        d.mkdir()
        p = make(d, state)
        for st in RESEARCHERS + NEAR:
            assert call(p, st) == 2, (state, st)


def test_researcher_allowed_when_approved_or_ai(tmp_path):
    for state in ("approved", "ai"):
        d = tmp_path / state
        d.mkdir()
        p = make(d, state)
        (p / ".superlite" / "stable.json").write_text("{}")   # active run
        (p / ".superlite" / "dispatch.json").write_text('{"batches": [["T1"]]}')
        for st in RESEARCHERS:
            assert call(p, st) == 0, (state, st)
        for st in ("general-purpose", "superlite:implementer", ""):
            assert call(p, st) == 2, (state, st)


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
