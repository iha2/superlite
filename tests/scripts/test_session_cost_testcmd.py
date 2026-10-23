"""Session-cost: resolve_related maps source paths to tests for --fast."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "subagent-driven-development" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import testcmd  # noqa: E402
from test_plan_tools import run  # noqa: E402

RUN_TESTS = SCRIPTS / "run_tests.py"


def _py_project(tmp_path: Path, src: str, test: str | None):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='t'\n", encoding="utf-8")
    sp = tmp_path / src
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text("x = 1\n", encoding="utf-8")
    if test is not None:
        tp = tmp_path / test
        tp.parent.mkdir(parents=True, exist_ok=True)
        tp.write_text("def test_ok():\n    assert True\n", encoding="utf-8")


def test_related_name_convention_py(tmp_path):
    _py_project(tmp_path, "src/foo.py", "tests/test_foo.py")
    assert testcmd.resolve_related(str(tmp_path), "src/foo.py") == ["tests/test_foo.py"]


def test_related_path_with_spaces(tmp_path):
    """Review Focus: encoding — paths with spaces."""
    _py_project(tmp_path, "src/my module.py", "tests/test_my module.py")
    assert testcmd.resolve_related(str(tmp_path), "src/my module.py") == [
        "tests/test_my module.py"
    ]


def test_related_missing_raises(tmp_path):
    """Review Focus: file — missing related tests."""
    _py_project(tmp_path, "src/missing.py", None)
    with pytest.raises(FileNotFoundError, match=r"no tests found for src/missing\.py"):
        testcmd.resolve_related(str(tmp_path), "src/missing.py")


def test_fast_source_path_fails_loud(tmp_path):
    _py_project(tmp_path, "src/gone.py", None)
    cp = run(RUN_TESTS, "--fast", "src/gone.py", cwd=tmp_path)
    assert cp.returncode != 0
    assert "no tests found" in (cp.err + cp.out)


def test_related_template_from_config(tmp_path):
    _py_project(tmp_path, "src/foo.py", "spec/unit_foo.py")
    sl = tmp_path / ".superlite"
    sl.mkdir(exist_ok=True)
    (sl / "test-cmd.json").write_text(
        json.dumps({
            "language": "python",
            "test": "pytest",
            "related": "spec/unit_{src}.py",
        }),
        encoding="utf-8",
    )
    # name-convention file must NOT win when related template is set
    (tmp_path / "tests").mkdir(exist_ok=True)
    (tmp_path / "tests" / "test_foo.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    assert testcmd.resolve_related(str(tmp_path), "src/foo.py") == ["spec/unit_foo.py"]
