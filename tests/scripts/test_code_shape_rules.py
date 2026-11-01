"""Code shape rules, checked on the AST of every runtime script (skills/swarm/scripts and skills/swarm/probes).

The rules (plan, "Global constraints"): nesting at most 3, catch-everything handlers only at a tool's or hook's entry
point, no script over 600 lines, and no `.superlite` string outside state.py.

The ALLOW_* constants below are strict: only the four ruled broad-except boundaries remain. Never grow them.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SWARM = ROOT / "skills" / "swarm"
FILES = sorted(p for d in (SWARM / "scripts", SWARM / "probes") for p in d.rglob("*.py") if "__pycache__" not in p.parts)

MAX_NESTING = 3
MAX_LINES = 600
BROAD = {"Exception", "BaseException"}

# No exceptions to the nesting rule or the `.superlite` rule: both sets stay empty.
ALLOW_NESTING: set[str] = set()
# The four ruled boundaries where a catch-everything handler is allowed away from an entry point.
ALLOW_BROAD_EXCEPT: set[str] = {
    "probes/probe_common.py::untrusted",  # runs untrusted project code; the failure is recorded as probe data
    "probes/probe_worker.py::_untrusted",  # runs untrusted project code; the failure is recorded as probe data
    "scripts/testprobe.py::_check",  # the hang watcher never breaks a test run; it logs to stderr
    "scripts/journal.py::Journal.__exit__",  # every undo runs; failures are reported; the original error is re-raised
}
# Over 600 lines: none today.
ALLOW_LONG_FILES: set[str] = set()

# Only state.py (its DIR_NAME constant) spells `.superlite`: everything else asks state.path / state.run_dir.
ALLOW_LEGACY_PATH_STRINGS: set[str] = set()


def rel(path: Path) -> str:
    return path.relative_to(SWARM).as_posix()


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def functions(tree: ast.AST):
    """Every function and method, with its qualified name."""
    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield f"{prefix}{child.name}", child
                yield from walk(child, f"{prefix}{child.name}.")
            elif isinstance(child, ast.ClassDef):
                yield from walk(child, f"{prefix}{child.name}.")
            else:
                yield from walk(child, prefix)
    yield from walk(tree, "")


def blocks(stmt: ast.stmt):
    """The statement lists directly under a compound statement."""
    if isinstance(stmt, ast.Try) or type(stmt).__name__ == "TryStar":
        yield stmt.body
        for h in stmt.handlers:
            yield h.body
        yield stmt.orelse
        yield stmt.finalbody
    elif isinstance(stmt, ast.Match):
        for case in stmt.cases:
            yield case.body
    elif isinstance(stmt, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith)):
        yield stmt.body
        yield getattr(stmt, "orelse", [])


def depth(stmts: list) -> int:
    """Deepest nesting of control structures in a statement list; an `elif` is not a deeper level."""
    best = 0
    for s in stmts:
        if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue  # a nested function is measured as its own function
        inner = 0
        for block in blocks(s):
            elif_chain = isinstance(s, ast.If) and block is s.orelse and len(block) == 1 and isinstance(block[0], ast.If)
            inner = max(inner, depth(block) - (1 if elif_chain else 0))
        is_compound = any(True for _ in blocks(s))
        best = max(best, inner + 1 if is_compound else 0)
    return best


def is_broad(handler: ast.ExceptHandler) -> bool:
    t = handler.type
    if t is None:
        return True
    names = t.elts if isinstance(t, ast.Tuple) else [t]
    return any(isinstance(n, ast.Name) and n.id in BROAD for n in names)


def is_entry(qualname: str) -> bool:
    """A tool's or hook's entry point: `main`, a `*_main` helper for a subcommand, or a `cmd_*` subcommand."""
    name = qualname.rsplit(".", 1)[-1]
    return name == "main" or name.endswith("_main") or name.startswith("cmd_")


def docstring_nodes(tree: ast.AST) -> set[int]:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                out.add(id(first.value))
    return out


def test_the_rules_see_the_files():
    assert len(FILES) > 20
    assert (SWARM / "scripts" / "state.py") in FILES


def test_nesting_at_most_3():
    found = set()
    for path in FILES:
        for qual, fn in functions(parse(path)):
            if depth(fn.body) > MAX_NESTING:
                found.add(f"{rel(path)}::{qual}")
    new = sorted(found - ALLOW_NESTING)
    assert not new, f"nesting deeper than {MAX_NESTING}: {new}"


def test_broad_except_only_at_entry():
    found = set()
    for path in FILES:
        tree = parse(path)
        in_function: set[int] = set()
        for qual, fn in functions(tree):
            for node in ast.walk(fn):
                if isinstance(node, ast.ExceptHandler):
                    in_function.add(id(node))
                    if is_broad(node) and not is_entry(qual):
                        found.add(f"{rel(path)}::{qual}")
        for node in ast.walk(tree):  # a handler at module level is never an entry point
            if isinstance(node, ast.ExceptHandler) and id(node) not in in_function and is_broad(node):
                found.add(f"{rel(path)}::<module>")
    new = sorted(found - ALLOW_BROAD_EXCEPT)
    assert not new, f"catch-everything handler outside an entry point: {new}"


def test_no_file_over_600_lines():
    found = {rel(p) for p in FILES if len(p.read_text(encoding="utf-8").splitlines()) > MAX_LINES}
    new = sorted(found - ALLOW_LONG_FILES)
    assert not new, f"over {MAX_LINES} lines: {new}"


def test_only_state_py_spells_the_run_folder():
    found = set()
    for path in FILES:
        if path.name == "state.py":
            continue
        tree = parse(path)
        skip = docstring_nodes(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip \
                    and ".superlite" in node.value:
                found.add(rel(path))
    new = sorted(found - ALLOW_LEGACY_PATH_STRINGS)
    assert not new, f"`.superlite` spelled outside state.py: {new}"


# The depth counter itself, on code written for the purpose.
@pytest.mark.parametrize("src, want", [
    ("def f():\n    return 1\n", 0),
    ("def f(x):\n    if x:\n        return 1\n", 1),
    ("def f(x):\n    if x:\n        pass\n    elif x > 1:\n        pass\n    elif x > 2:\n        pass\n", 1),
    ("def f(x):\n    for a in x:\n        if a:\n            while a:\n                a -= 1\n", 3),
    ("def f(x):\n    try:\n        for a in x:\n            with a:\n                if a:\n                    pass\n    finally:\n        pass\n", 4),
    ("def f(x):\n    def g():\n        for a in x:\n            for b in a:\n                for c in b:\n                    for d in c:\n                        pass\n    return g\n", 0),
])
def test_depth_counter(src, want):
    fn = ast.parse(src).body[0]
    assert depth(fn.body) == want


def test_broad_handler_detector():
    tree = ast.parse("try:\n    pass\nexcept (OSError, Exception):\n    pass\nexcept:\n    pass\nexcept OSError:\n    pass\n")
    flags = [is_broad(h) for h in tree.body[0].handlers]
    assert flags == [True, True, False]
