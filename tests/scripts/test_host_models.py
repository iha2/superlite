"""Host profile resolve + Execution parse + SWARM grouping (Cursor)."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import host_models as hm  # noqa: E402


def test_parse_execution():
    text = "# P\n\n**Execution:** host=cursor, parallel=multitask, swarm_by=tier\n\n**Goal:** x\n"
    assert hm.parse_execution(text) == {
        "host": "cursor",
        "parallel": "multitask",
        "swarm_by": "tier",
    }
    assert hm.parse_execution("# no exec\n") == {}


def test_resolve_writes_once(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".superlite").mkdir()
    plan = tmp_path / "plan.md"
    plan.write_text(
        "# G\n\n**Execution:** host=cursor, parallel=multitask, swarm_by=tier\n",
        encoding="utf-8",
    )
    # point CFG at the real hosts file in the plugin tree under test
    monkeypatch.setattr(hm, "CFG", ROOT / "skills" / "swarm" / "config" / "hosts")
    d1 = hm.resolve("cursor", str(plan))
    assert d1["host"] == "cursor"
    assert d1["jobs"]["mechanical"] == "composer-2.5-fast"
    assert d1["jobs"]["build"] == "claude-sonnet-5-5-high"
    out = tmp_path / ".superlite" / "host-models.json"
    assert out.is_file()
    d1["jobs"]["mechanical"] = "SHOULD_NOT_CHANGE"
    out.write_text(json.dumps(d1), encoding="utf-8")
    d2 = hm.resolve("cursor", str(plan), force=False)
    assert d2["jobs"]["mechanical"] == "SHOULD_NOT_CHANGE"
    d3 = hm.resolve("cursor", str(plan), force=True)
    assert d3["jobs"]["mechanical"] == "composer-2.5-fast"


def test_swarm_groups_by_tier_multitask_when_multiple():
    resolved = {
        "host": "cursor",
        "parallel": "multitask",
        "swarm_by": "tier",
        "tier_to_job": {"haiku": "mechanical", "sonnet": "build"},
        "agent_to_job": {},
        "jobs": {"mechanical": "composer-2.5-fast", "build": "claude-sonnet-5-5-high"},
    }
    lines = [
        "T1,T2 fresh tier=sonnet agent=superlite:implementer",
        "T3 fresh tier=sonnet agent=superlite:implementer",
        "T4 fresh tier=haiku agent=superlite:implementer-haiku",
    ]
    swarm = hm.swarm_lines(lines, resolved)
    assert swarm[0].startswith("SWARM host=cursor")
    body = "\n".join(swarm)
    assert "tier=sonnet" in body and "mode=multitask" in body and "agents=2" in body
    assert "tier=haiku" in body and "job=mechanical" in body
    assert "slug=composer-2.5-fast" in body
    assert "slug=claude-sonnet-5-5-high" in body


def test_swarm_single_agent_mode_task():
    resolved = {
        "host": "cursor",
        "parallel": "multitask",
        "swarm_by": "tier",
        "tier_to_job": {"sonnet": "build"},
        "agent_to_job": {},
        "jobs": {"build": "claude-sonnet-5-5-high"},
    }
    swarm = hm.swarm_lines(["T1 agent=a1 tier=sonnet"], resolved)
    assert "mode=task" in "\n".join(swarm)
    assert "resume" in "\n".join(swarm) or "agent=a1" in "\n".join(swarm)


def test_parse_dispatch_resume_vs_fresh():
    r = hm.parse_dispatch_line("T1 agent=h1 tier=haiku")
    assert r["decision"] == "resume" and r["agent"] == "h1"
    f = hm.parse_dispatch_line("T2 fresh tier=sonnet agent=superlite:implementer")
    assert f["decision"] == "fresh" and f["agent"] == "superlite:implementer"
