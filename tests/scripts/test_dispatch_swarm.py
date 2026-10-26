"""dispatch prints SWARM only when a Cursor host is detected."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import dispatch  # noqa: E402
import host_models as hm  # noqa: E402


PLAN_CURSOR = """# Swarm fixture
**Goal:** fixture
**Architecture:** test
**Tech stack:** python
**Spec:** none
**Detail:** tests
**Target-min:** 5
**Execution:** host=cursor, parallel=multitask, swarm_by=tier

## Global constraints
- language: python

```mermaid
flowchart LR
  T1[a]
  T2[b]
  %% taskgraph
```

### T1 a · Complexity 2 · Est. context 10k
**Owns:** a.py
**Components:** 1
**LOC owned:** 10
**Tests:** fast
**Model:** sonnet
**Kind:** logic
**Depends on:** —
**Produces:** a.f() -> int
**Consumes:** —
**Verify:** true
**Done when:** ok
"""

PLAN_PLAIN = PLAN_CURSOR.replace(
    "**Execution:** host=cursor, parallel=multitask, swarm_by=tier\n", ""
)


def test_detect_host_from_execution(tmp_path):
    p = tmp_path / "p.md"
    p.write_text(PLAN_CURSOR, encoding="utf-8")
    assert hm.detect_host(str(p)) == "cursor"
    p2 = tmp_path / "q.md"
    p2.write_text(PLAN_PLAIN, encoding="utf-8")
    assert hm.detect_host(str(p2)) is None


def test_main_appends_swarm_when_host(tmp_path, monkeypatch, capsys):
    import types

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".superlite").mkdir()
    plan = tmp_path / "plan.md"
    plan.write_text(PLAN_CURSOR, encoding="utf-8")
    monkeypatch.setattr(hm, "CFG", ROOT / "skills" / "swarm" / "config" / "hosts")
    monkeypatch.setattr(
        dispatch,
        "run",
        lambda *a, **k: (
            [
                ("T1", "fresh tier=sonnet agent=superlite:implementer"),
                ("T2", "fresh tier=sonnet agent=superlite:implementer"),
            ],
            {},
        ),
    )
    monkeypatch.setitem(sys.modules, "orchestrator", types.SimpleNamespace(require=lambda *a, **k: None))
    rc = dispatch.main(["--plan", str(plan), "--cap", "10"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "T1 fresh" in out and "T2 fresh" in out
    assert "SWARM host=cursor" in out
    assert "mode=multitask" in out
    assert (tmp_path / ".superlite" / "host-models.json").is_file()


def test_main_no_swarm_without_host(tmp_path, monkeypatch, capsys):
    import types

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".superlite").mkdir()
    plan = tmp_path / "plan.md"
    plan.write_text(PLAN_PLAIN, encoding="utf-8")
    monkeypatch.setattr(
        dispatch,
        "run",
        lambda *a, **k: ([("T1", "fresh tier=sonnet agent=superlite:implementer")], {}),
    )
    monkeypatch.setitem(sys.modules, "orchestrator", types.SimpleNamespace(require=lambda *a, **k: None))
    rc = dispatch.main(["--plan", str(plan)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "T1 fresh" in out
    assert "SWARM" not in out
