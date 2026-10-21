"""MVP mode: mvp_check build/stamp/check/ai/fetch on the prototype page and mvp-feedback.json, and the texts that must agree."""
from __future__ import annotations

import json
import os
import re

from test_plan_tools import SCRIPTS, run

MVP = SCRIPTS / "mvp_check.py"
REPO = SCRIPTS.parents[2]
SKILL = REPO / "skills" / "mvp-prototype"
PLAN = """# Notes app: MVP plan

```
+--------------------------+
| My notes      [Add] [F1] |
| [search........]    [F3] |
| - Groceries  #home  [F2] |
+--------------------------+
```

**Target:** web
**Estimate:** 30-60 min, $4-8

## Features

### F1: Create a note
- **Size:** S
- **Priority:** must
- **Recommend:** in - the core

### F2: Tags
- **Size:** M
- **Priority:** could
- **Recommend:** out - later

### F3: Search
- **Size:** M
- **Priority:** should
- **Recommend:** in - needed with many notes

## Left out
- Sync across devices
"""
MOCK = """<section data-screen="S1" data-title="Note list">
  <h1 data-edit>My notes</h1>
  <button data-feature="F1" data-go="S2">Add</button>
  <input data-feature="F3" placeholder="Search">
  <ul data-sl-order><li data-feature="F1 F2">Groceries #home</li></ul>
</section>
<section data-screen="S2" data-title="New note"><textarea data-feature="F1">Buy milk</textarea></section>
"""



PLAN_REL = "docs/plans/2026-10-21-notes.mvp.md"
PAGE_REL = ".superlite/mvp/prototype.html"
FB_REL = ".superlite/mvp/mvp-feedback.json"


def setup(tmp_path, plan=PLAN, mock=MOCK):
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    (tmp_path / PLAN_REL).write_text(plan, encoding="utf-8")
    (tmp_path / ".superlite" / "mvp").mkdir(parents=True)
    (tmp_path / ".superlite" / "mvp" / "mock.html").write_text(mock, encoding="utf-8")
    return run(MVP, "build", "--plan", PLAN_REL, "--mock", ".superlite/mvp/mock.html", cwd=tmp_path)


def check(tmp_path, *extra):
    return run(MVP, "check", "--plan", PLAN_REL, *extra, cwd=tmp_path)


def page(tmp_path):
    return (tmp_path / PAGE_REL).read_text(encoding="utf-8")


def sha_of(tmp_path):
    return re.search(r'name="sl-prototype-sha" content="([0-9a-f]{64})"', page(tmp_path)).group(1)


def human_feedback(tmp_path, **over):
    fb = {"schema": 1, "source": "human", "plan": PLAN_REL, "prototype_sha": sha_of(tmp_path), "approved": True,
          "target": "web", "keep_as_frontend": False,
          "features": [{"id": "F3", "title": "Find", "in": True, "order": 1, "note": ""},
                       {"id": "F1", "title": "Create a note", "in": True, "order": 2, "note": ""},
                       {"id": "F2", "title": "Tags", "in": False, "order": 3, "note": "later"}],
          "screens": [{"id": "S1", "title": "Note list", "edits": [{"label": "Add", "to": "New note"}]}],
          "added": [{"title": "Pin a note", "note": ""}], "notes": ""}
    fb.update(over)
    (tmp_path / FB_REL).write_text(json.dumps(fb), encoding="utf-8")
    return fb


# ------------------------------------------------------------------ build and the page

def test_build_writes_a_stamped_page_that_checks_ok(tmp_path):
    cp = setup(tmp_path)
    assert cp.returncode == 0, cp.both
    text = page(tmp_path)
    assert "<!--SL:MOCK-->" not in text and "/*SL:DATA*/" not in text and 'data-feature="F3"' in text
    data = json.loads(re.search(r'id="sl-data" type="application/json">(.*?)</script>', text, re.S).group(1))
    assert [f["id"] for f in data["features"]] == ["F1", "F2", "F3"] and data["features"][1]["in"] is False
    assert data["target"] == "web" and data["estimate"] == {"min_lo": 30, "min_hi": 60, "usd_lo": 4, "usd_hi": 8}
    cp = check(tmp_path)
    assert cp.returncode == 0 and cp.out.startswith("mvp_check: ok, 3 features (2 recommended in)"), cp.both


def test_stamp_is_idempotent_and_a_hand_edit_needs_a_new_stamp(tmp_path):
    setup(tmp_path)
    before = sha_of(tmp_path)
    assert run(MVP, "stamp", cwd=tmp_path).out.strip() == before
    p = tmp_path / PAGE_REL
    p.write_text(page(tmp_path).replace("Buy milk", "Buy bread"), encoding="utf-8")
    cp = check(tmp_path)
    assert cp.returncode == 1 and "changed since it was stamped" in cp.out
    assert run(MVP, "stamp", cwd=tmp_path).out.strip() != before and check(tmp_path).returncode == 0


def test_every_plan_feature_must_be_in_the_mock_and_no_stray_ids(tmp_path):
    cp = setup(tmp_path, mock=MOCK.replace(' data-feature="F3"', ' data-feature="F9"'))
    assert cp.returncode == 1
    assert 'feature F3 is not in the mock (no data-feature="F3")' in cp.out and "data-feature F9 is not a feature" in cp.out


def test_external_urls_and_network_calls_are_refused(tmp_path):
    bad = MOCK + '<img src="https://cdn.example/x.png"><a href="//host.example/y">y</a><script>fetch("/notes")</script>'
    cp = setup(tmp_path, mock=bad)
    assert cp.returncode == 1
    for what in ("external URL", "protocol-relative URL", "network call"):
        assert f"page: {what} on line" in cp.out, what
    svg = MOCK + '<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>'
    assert setup(tmp_path / "svg", mock=svg).returncode == 0


def test_unclosed_structural_tags_are_reported(tmp_path):
    cp = setup(tmp_path, mock=MOCK + "<div><section></div>")
    assert cp.returncode == 1 and "closes <section>" in cp.out


def test_plan_needs_target_and_feature_fields(tmp_path):
    plan = PLAN.replace("**Target:** web", "**Target:** desktop app").replace("- **Size:** M\n- **Priority:** could\n", "")
    cp = setup(tmp_path, plan=plan)
    assert cp.returncode == 1 and "**Target:** must be one or more of" in cp.out
    assert "F2 needs **Size:**" in cp.out and "F2 needs **Priority:**" in cp.out
    assert not (tmp_path / PAGE_REL).exists()


def test_template_is_offline_and_has_the_panel_and_save_logic():
    t = (SKILL / "page-template.html").read_text(encoding="utf-8")
    assert "http" not in t and "fetch(" not in t
    for need in ("<!--SL:MOCK-->", "/*SL:DATA*/", 'name="sl-prototype-sha"', "showSaveFilePicker", "navigator.clipboard",
                 'id="sl-save"', 'id="sl-copy"', "data-feature", "data-go", "data-edit", "keep_as_frontend", "prototype_sha"):
        assert need in t, need


def test_build_never_opens_the_page_and_open_hands_it_to_the_browser(tmp_path, monkeypatch):
    import mvp_check
    opened = []
    monkeypatch.setattr(mvp_check.webbrowser, "open", lambda uri: opened.append(uri) or True)
    setup(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert mvp_check.main(["build", "--plan", PLAN_REL, "--mock", ".superlite/mvp/mock.html"]) == 0 and opened == []
    assert mvp_check.main(["open"]) == 0 and opened == [(tmp_path / PAGE_REL).resolve().as_uri()]


# ------------------------------------------------------------------ the feedback file

def test_human_feedback_with_reorder_rename_cut_edit_and_added_is_valid(tmp_path):
    setup(tmp_path)
    human_feedback(tmp_path)
    cp = check(tmp_path, "--require-feedback")
    assert cp.returncode == 0, cp.both


def test_feedback_from_an_older_page_is_refused(tmp_path):
    setup(tmp_path)
    human_feedback(tmp_path, prototype_sha="0" * 64)
    cp = check(tmp_path)
    assert cp.returncode == 1 and "saved from an older page" in cp.out


def test_feedback_schema_errors(tmp_path):
    setup(tmp_path)
    fb = human_feedback(tmp_path)
    fb.update(schema=2, source="robot", approved="yes", keep_as_frontend=True, target="cli", plan="docs/plans/other.mvp.md",
              features=fb["features"][:2] + [{"id": "F2", "title": "Tags", "in": "no", "order": 3}],
              added=[{"title": " "}], screens=[{"id": "S1", "edits": [{"label": "Add"}]}])
    (tmp_path / FB_REL).write_text(json.dumps(fb), encoding="utf-8")
    out = check(tmp_path).out
    for msg in ("'schema' must be 1", "'source' must be human or ai", "'approved' must be true or false",
                "keep_as_frontend is only for a web target", "'plan' is docs/plans/other.mvp.md", "features[2] needs",
                "added[0] needs a title", "screens[0] needs id and edits"):
        assert msg in out, msg
    fb = human_feedback(tmp_path, features=[{"id": "F1", "title": "x", "in": True, "order": 1, "note": ""},
                                            {"id": "F7", "title": "y", "in": True, "order": 1, "note": ""}])
    out = check(tmp_path).out
    assert "do not match the plan" in out and "orders must be 1..n" in out
    (tmp_path / FB_REL).write_text("{not json", encoding="utf-8")
    assert "feedback: not JSON" in check(tmp_path).out


def test_missing_feedback_only_fails_when_required(tmp_path):
    setup(tmp_path)
    assert check(tmp_path).returncode == 0
    cp = check(tmp_path, "--require-feedback")
    assert cp.returncode == 1 and "run sl mvp_check fetch" in cp.out


def test_ai_writes_feedback_as_the_plan_recommends(tmp_path):
    setup(tmp_path)
    cp = run(MVP, "ai", "--plan", PLAN_REL, cwd=tmp_path)
    assert cp.returncode == 0 and "source ai" in cp.out, cp.both
    fb = json.loads((tmp_path / FB_REL).read_text(encoding="utf-8"))
    assert fb["source"] == "ai" and fb["approved"] is True and fb["prototype_sha"] == sha_of(tmp_path)
    assert [(f["id"], f["in"], f["order"]) for f in fb["features"]] == [("F1", True, 1), ("F2", False, 2), ("F3", True, 3)]
    assert check(tmp_path, "--require-feedback").returncode == 0
    human_feedback(tmp_path, source="ai")
    assert "source ai carries no edits or added features" in check(tmp_path).out


def test_fetch_prefers_the_project_file_else_the_newest_download_newer_than_the_page(tmp_path):
    setup(tmp_path)
    dl = tmp_path / "dl"
    dl.mkdir()
    cp = run(MVP, "fetch", "--downloads", dl, cwd=tmp_path)
    assert cp.returncode == 1 and "paste the Copy feedback block" in cp.out
    t = (tmp_path / PAGE_REL).stat().st_mtime
    for name, age in (("mvp-feedback.json", -100), ("mvp-feedback (1).json", 10), ("mvp-feedback (2).json", 20)):
        (dl / name).write_text(json.dumps({"name": name}), encoding="utf-8")
        os.utime(dl / name, (t + age, t + age))
    cp = run(MVP, "fetch", "--downloads", dl, cwd=tmp_path)
    assert cp.returncode == 0 and cp.out.strip().replace("\\", "/") == FB_REL
    assert json.loads((tmp_path / FB_REL).read_text(encoding="utf-8")) == {"name": "mvp-feedback (2).json"}
    (tmp_path / FB_REL).write_text('{"name": "project"}', encoding="utf-8")
    os.utime(tmp_path / FB_REL, (t + 30, t + 30))
    run(MVP, "fetch", "--downloads", dl, cwd=tmp_path)
    assert json.loads((tmp_path / FB_REL).read_text(encoding="utf-8")) == {"name": "project"}


# ------------------------------------------------------------------ the texts agree

def test_skill_command_design_and_tool_docs_agree():
    skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    cmd = (REPO / "commands" / "mvp.md").read_text(encoding="utf-8")
    design = (REPO / "docs" / "mvp-flow.md").read_text(encoding="utf-8")
    approve = "sl orchestrator approve --review docs/plans/<date>-<topic>.mvp.md"
    for text in (cmd, design):
        for need in (approve, "Ruling: [scope]", "superlite:mvp-prototype", "mvp_check", "Changed from the approved plan", "choice"):
            assert need in text, need
    for need in (approve, "Ruling: [scope]", "END THE TURN", "mvp_check ai", "mvp_check fetch", "mvp_check build", "mvp_check open"):
        assert need in skill, need
    assert "Not built yet" not in design and "## Open points" not in design and "## Decided points" in design
    assert "mvp-prototype" in (REPO / "docs" / "skills-and-agents.md").read_text(encoding="utf-8")
    assert "| `mvp_check` |" in (REPO / "docs" / "tools.md").read_text(encoding="utf-8")
    assert "mvp_check" in (SCRIPTS / "sl").read_text(encoding="utf-8").split("set -u")[0]
    for f in ("SKILL.md", "page-template.html", "feedback-schema.md", "mock-patterns.md"):
        assert (SKILL / f).is_file(), f
    assert len((REPO / "skills" / "using-superlite" / "SKILL.md").read_text(encoding="utf-8")) <= 2000
    assert len((REPO / "skills" / "subagent-driven-development" / "SKILL.md").read_text(encoding="utf-8")) <= 11600
