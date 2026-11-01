import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
T4_FILES = {"planfile.py", "base.py", "state.py", "ident.py", "journal.py", "test_base.py"}
GLOBS = (
    "skills/swarm/scripts/**/*.py",
    "skills/swarm/probes/**/*.py",
    "tests/**/*.py",
)
DEF_LINE = re.compile(r"^\s*(async\s+)?def\s")
COMPLEXITY = re.compile(r"#.*\bO\(")


def strip_note(line):
    """Return the line as the strip rules leave it; def lines keep a non-O(1) note."""
    m = re.search(r"\s*\|\s*O\(.*$", line)
    if m:
        return line[:m.start()]
    if DEF_LINE.match(line) and not re.search(r"#\s*O\(1\)", line):
        return line
    return re.sub(r"\s*#\s*O\(.*$", "", line)


def owned_files():
    for pattern in GLOBS:
        for path in ROOT.glob(pattern):
            if path.name in T4_FILES or path.name == "test_code_shape.py" or "fixtures" in path.parts:
                continue
            yield path


def test_no_complexity_comments():
    bad = []
    for path in owned_files():
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if COMPLEXITY.search(line) and not DEF_LINE.match(line):
                bad.append(f"{path.relative_to(ROOT)}:{n}")
    hooks = [p for p in (ROOT / "hooks").iterdir() if p.is_file() and p.suffix != ".json"]
    hooks.append(ROOT / "skills/swarm/scripts/superlite-cli")
    for path in hooks:
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if COMPLEXITY.search(line):
                bad.append(f"{path.relative_to(ROOT)}:{n}")
    assert not bad, bad[:10]


def test_def_line_note_kept():
    assert strip_note("def f():  # O(n)") == "def f():  # O(n)"


def test_text_before_pipe_kept():
    got = strip_note("x = 1  # Review Focus: rounding | O(1)")
    assert got == "x = 1  # Review Focus: rounding"
