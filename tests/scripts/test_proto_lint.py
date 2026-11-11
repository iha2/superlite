"""Stay-inside lint: a .proto.md plan owns only paths in the open prototype sandbox."""
import argparse
import os
import sys
from pathlib import Path

import pytest

from test_plan_tools import make_plan

SCRIPTS = str(Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts")
sys.path.insert(0, SCRIPTS)
import plan_lint  # noqa: E402
import proto_lint  # noqa: E402
import sandbox  # noqa: E402

def _args(p):
    return argparse.Namespace(plan=str(p), spec=None, cap=plan_lint.pt.DEFAULT_CAP, target_min=None, tail_min=None, max_task_min=None, max_chars=plan_lint.DEFAULT_MAX_CHARS,
                              stable=False)


SB = ".superlite/sandboxes/host/x"


@pytest.fixture
def fake_sandbox(monkeypatch):
    """Fake T1 (open_sandbox returns SB, inside is a prefix check) unless SUPERLITE_REAL=1."""
    if os.environ.get("SUPERLITE_REAL") == "1":
        return
    monkeypatch.setattr(sandbox, "open_sandbox", lambda sl: SB)
    monkeypatch.setattr(sandbox, "inside", lambda sb, p: p == sb or p.startswith(sb + "/"))


@pytest.fixture
def no_sandbox(monkeypatch):
    monkeypatch.setattr(sandbox, "open_sandbox", lambda sl: None)


def test_outside_path_named(fake_sandbox, tmp_path):
    out = proto_lint.check("docs/plans/x.proto.md", {"T2": ["src/app.py"]}, tmp_path / ".superlite")
    assert out == [f"SANDBOX: T2 writes src/app.py outside {SB}"]


def test_non_proto_plan_not_checked(fake_sandbox, tmp_path):
    assert proto_lint.check("docs/plans/x.md", {"T2": ["src/app.py"]}, tmp_path / ".superlite") == []


def test_no_open_sandbox_no_check(no_sandbox, tmp_path):
    assert proto_lint.check("docs/plans/x.proto.md", {"T2": ["src/app.py"]}, tmp_path / ".superlite") == []


def test_inside_paths_ok(fake_sandbox, tmp_path):
    assert proto_lint.check("docs/plans/x.proto.md", {"T1": [f"{SB}/a.py", f"{SB}/t/b.py"]},
                            tmp_path / ".superlite") == []


@pytest.mark.parametrize("bad", [f"{SB}/../../../../src/a.py", f"{SB}/../y/a.py", "/etc/passwd", "../x.py",
                                 f"{SB}\\..\\..\\a.py", "C:/win/a.py"])
def test_traversal_and_absolute_rejected(fake_sandbox, tmp_path, bad):
    out = proto_lint.check("docs/plans/x.proto.md", {"T1": [bad]}, tmp_path / ".superlite")
    assert len(out) == 1 and "SANDBOX: T1 writes" in out[0]


def test_relative_to_sandbox_ok(fake_sandbox, tmp_path):
    sl = tmp_path / ".superlite"
    plan = tmp_path / SB / "docs" / "plans" / "x.proto.md"
    assert proto_lint.check(str(plan), {"T1": ["src/app.py", "tests/t.py"]}, sl) == []
    out = proto_lint.check(str(plan), {"T1": ["../../../../../src/app.py"]}, sl)
    assert len(out) == 1


def test_proto_plan_outside_fails_lint(fake_sandbox, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    p = make_plan(tmp_path, [{"id": "T1", "time": 2, "owns": "src/app.py"}], header="**Ignore:** .env",
                  name="x.proto.md")
    rc = plan_lint.cmd_lint(_args(p))
    out = capsys.readouterr().out
    assert rc != 0 and f"SANDBOX: T1 writes src/app.py outside {SB}" in out


def test_non_proto_plan_lint_unaffected(fake_sandbox, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    p = make_plan(tmp_path, [{"id": "T1", "time": 2, "owns": "src/app.py"}], header="**Ignore:** .env",
                  name="x.md")
    plan_lint.cmd_lint(_args(p))
    assert "SANDBOX" not in capsys.readouterr().out
