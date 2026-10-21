"""suggest_flow.py: one deterministic line per request and project state; the state is computed like session-start."""
import json
import sys

import pytest

from test_plan_tools import SCRIPTS, run

sys.path.insert(0, str(SCRIPTS))
import suggest_flow as sf  # noqa: E402

SUGGEST = SCRIPTS / "suggest_flow.py"
E, X, R = sf.EMPTY, sf.EXISTING, sf.RUNNING


@pytest.mark.parametrize("text,state,flow", [
    ("add CSV export to the report page", X, "feature"),
    ("extend the export so it supports a date range", X, "feature"),
    ("add error messages to the signup form", X, "feature"),  # "error" as a thing to add, not a fault
    ("add a settings screen backed by the database and the auth service", X, "feature"),
    ("test_parse_dates fails on Windows", X, "debug"),
    ("the upload page is broken since yesterday", X, "debug"),
    ("Traceback (most recent call last):\n  File \"a\", line 1\nKeyError: 'x'", X, "debug"),
    ("rename build to render in src/cli.py", X, "small"),
    ("fix the typo in the README", X, "small"),
    ("bump the version", X, "small"),
    ("change parse_row() to skip blank lines", X, "small"),
    ("review my branch before I merge it", X, "review"),
    ("build a new app from scratch beside this one", X, "regular"),
    ("an mvp of a mobile companion app", X, "mvp"),
    ("a habit tracker", E, "mvp"),
    ("build a todo app with a login page and a database", E, "regular"),
    ("prototype a recipe planner", E, "mvp"),
    ("where are we", R, "status"),
    ("resume the build", R, "resume"),
    ("add CSV export to the report page", R, "resume"),  # a run in progress comes first
])
def test_suggestion_table(text, state, flow):
    s = sf.suggest(text, state)
    assert s["flow"] == flow, s
    assert s["reason"].startswith(state + ", ") and flow not in s["also"]
    assert set(s["also"]) <= set(sf.FLOWS) and len(s["also"]) == len(set(s["also"]))


def test_same_input_same_line_and_the_line_shape(tmp_path):
    (tmp_path / "app.txt").write_text("x")
    a = run(SUGGEST, "--text", "add CSV export to the report page", cwd=tmp_path)
    b = run(SUGGEST, "--text", "add CSV export to the report page", cwd=tmp_path)
    assert a.returncode == 0 and a.out == b.out and a.out.count("\n") == 1
    assert a.out.startswith("suggest: feature - existing code, ") and " | also fits: " in a.out


def test_project_state_like_session_start(tmp_path):
    assert sf.project_state(tmp_path) == E
    (tmp_path / "x.go").write_text("package x")
    assert sf.project_state(tmp_path) == X
    (tmp_path / ".superlite").mkdir()
    (tmp_path / ".superlite" / "orchestrator.json").write_text("{}")
    assert sf.project_state(tmp_path) == X  # needs docs/plans too, as in the hook
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    assert sf.project_state(tmp_path) == R
    cp = run(SUGGEST, "--text", "hello", "--root", tmp_path, "--json", cwd=tmp_path)
    assert json.loads(cp.out)["flow"] == "resume"


def test_state_override_and_tech_names_are_not_files(tmp_path):
    cp = run(SUGGEST, "--text", "a todo list", "--state", E, cwd=tmp_path)
    assert cp.out.startswith("suggest: mvp - empty folder")
    assert sf.size("port the dashboard to next.js and react.js")["files"] == []
    assert sf.size("edit lib/x.rs")["files"] == ["lib/x.rs"]
