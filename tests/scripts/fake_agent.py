"""A stand-in for an implementer: writes exactly the task's Owns files and nothing else."""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

_PLANFILE = Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts" / "planfile.py"


def _planfile():
    spec = importlib.util.spec_from_file_location("planfile_for_fake_agent", _PLANFILE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _first_example(task) -> tuple:
    """(function name, returned value) from the task's first e.g. line like `alpha() -> 1`; a default when none."""
    for ex in task.examples:
        m = re.match(r"`?([A-Za-z_]\w*)\s*\(.*?\)\s*->\s*(.+?)`?\s*$", ex)
        if m:
            return m.group(1), m.group(2)
    return f"task_{task.id.lower()}", "None"


def do(task_id: str, plan: Path, repo) -> None:
    """Write each Owns file of `task_id` into the repo: a module for src files, a passing placeholder for tests."""
    task = _planfile().read(str(plan)).tasks[task_id]
    name, value = _first_example(task)
    for rel in task.owns:
        is_test = rel.startswith("tests/") or "/test_" in rel or Path(rel).name.startswith("test_")
        if is_test:
            repo.write(rel, f"def test_{task_id.lower()}_placeholder():\n    assert True\n")
        else:
            repo.write(rel, f"def {name}():\n    return {value}\n")
