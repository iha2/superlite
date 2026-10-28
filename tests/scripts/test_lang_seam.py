"""Non-Python stub spec and language-specific seam. Python still writes adapter.py."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "swarm" / "scripts"
PROBES = ROOT / "skills" / "swarm" / "probes"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(PROBES))

import plan_tools  # noqa: E402
import probe_run  # noqa: E402
import pt_review  # noqa: E402


PLAN = """# Plan

### T1 count
**Produces:** mod.count(text: str) -> int
**Contract:** returns the count
"""


def _ts(root: Path) -> Path:
    sl = root / ".superlite"
    sl.mkdir(parents=True)
    (sl / "test-cmd.json").write_text(json.dumps({
        "language": "typescript",
        "test": "npm test",
        "test_file": "npm test -- {files}",
        "junit": "",
        "setup": "",
    }), encoding="utf-8", newline="\n")
    plan = root / "plan.md"
    plan.write_text(PLAN, encoding="utf-8", newline="\n")
    return plan


def test_swift_produces_finds_file_and_skips_introspection(tmp_path):
    """Issue #4: Swift Produces must not report 'no module found (file missing)' when the .swift exists."""
    sources = tmp_path / "Sources" / "MaxKit"
    sources.mkdir(parents=True)
    (sources / "Counter.swift").write_text("struct Counter {}\n", encoding="utf-8")
    f = pt_review.find_module_file(tmp_path, "MaxKit.Counter")
    assert f is not None and f.name == "Counter.swift"
    reals = pt_review.read_real(tmp_path, [("MaxKit.Counter", ["init"])])
    assert reals[0].get("skip") is True and reals[0].get("ok") is True
    assert pt_review.compare_sig(type("P", (), {"qual": "MaxKit.Counter.init", "args_src": "()", "ret": None, "raw": "x"})(),
                                 reals[0]) == []


def test_typescript_stub_spec_writes_no_py(tmp_path, capsys):
    plan = _ts(tmp_path)
    assert plan_tools.main(["stubs", "--plan", str(plan), "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "stub: mod.count(text: str) -> int" in out
    assert "contract: returns the count" in out
    assert list(tmp_path.rglob("*.py")) == []


def test_typescript_seam_and_python_adapter(tmp_path, capsys):
    plan = _ts(tmp_path)
    assert pt_review.main(["adapter", "--plan", str(plan), "--root", str(tmp_path)]) == 0
    seam = tmp_path / ".superlite" / "probes" / "seam.ts"
    assert seam.read_text(encoding="utf-8").splitlines()[0] == "// superlite seam: typescript"
    assert not (tmp_path / ".superlite" / "probes" / "adapter.py").exists()

    py = tmp_path / "py"
    py.mkdir()
    py_plan = py / "plan.md"
    py_plan.write_text("**Probe entry:** call=pkg.mod.fn\n", encoding="utf-8")
    assert pt_review.main(["adapter", "--plan", str(py_plan), "--root", str(py)]) == 0
    adapter = py / ".superlite" / "probes" / "adapter.py"
    assert adapter.is_file()
    compile(adapter.read_text(encoding="utf-8"), str(adapter), "exec")


def test_missing_adapter_fails_only_for_python(tmp_path):
    ts = tmp_path / "ts"
    _ts(ts)
    assert probe_run.run_main(argparse.Namespace(root=str(ts), adapter=None)) == 0
    py = tmp_path / "py"
    py.mkdir()
    assert probe_run.run_main(argparse.Namespace(root=str(py), adapter=None)) == 2
