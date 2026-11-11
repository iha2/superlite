"""The plan's `**Full suite:** yes|no` header line: Plan.header_full_suite() and the lint NOTE for a missing line."""
from __future__ import annotations

import sys

import pytest

from test_plan_tools import SCRIPTS, make_plan, pt

sys.path.insert(0, str(SCRIPTS))

import plan_tools  # noqa: E402

NOTE = "NOTE: no **Full suite:** header line; finish runs affected tests only (missing means no)"


@pytest.mark.parametrize("value, want", [
    ("yes", True), ("Yes", True), ("true", True),
    ("no", False), ("No", False), ("false", False),
    ("maybe", None), ("", None),
])
def test_header_values(tmp_path, value, want):
    p = make_plan(tmp_path, [{"id": "T1"}], header=f"**Full suite:** {value}", name="values.md")
    assert plan_tools.Plan.load(str(p)).header_full_suite() is want


def test_header_absent_is_none(tmp_path):
    p = make_plan(tmp_path, [{"id": "T1"}], header="**Ignore:** .env", name="absent.md")
    assert plan_tools.Plan.load(str(p)).header_full_suite() is None


def test_lint_note_missing(tmp_path):
    p = make_plan(tmp_path, [{"id": "T1", "time": 2}], header="**Ignore:** .env", name="missing.md")
    assert NOTE in pt("lint", "--plan", p).out


def test_lint_quiet_present(tmp_path):
    p = make_plan(tmp_path, [{"id": "T1", "time": 2}], header="**Ignore:** .env\n**Full suite:** no", name="present.md")
    out = pt("lint", "--plan", p).out
    assert "**Full suite:**" not in out and "finish runs affected tests only" not in out


def test_fenced_ignored(tmp_path):
    p = make_plan(tmp_path, [{"id": "T1", "time": 2}], header="```\n**Full suite:** yes\n```", name="fenced.md")
    assert plan_tools.Plan.load(str(p)).header_full_suite() is None
    assert NOTE in pt("lint", "--plan", p).out


def test_plan_lint_imports_standalone():
    import subprocess
    root = str(SCRIPTS.parents[2])
    code = ("import importlib,sys; sys.path.insert(0, %r); "
            "importlib.import_module('skills.swarm.scripts.plan_lint')" % root)
    cp = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True)
    assert cp.returncode == 0, cp.stderr
