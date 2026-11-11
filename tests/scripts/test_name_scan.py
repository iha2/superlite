"""Shipped files keep the new vocabulary: no old launcher, no removed tool, no 'commit' for what superlite does,
and no test that only greps a markdown file for a phrase."""
from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SELF = Path(__file__).resolve().relative_to(ROOT).as_posix()
SKIP_DIRS = ("docs/plans/", "docs/specs/", "bench/results/")
BINARY = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pyc", ".zip", ".woff", ".woff2", ".pdf"}


def shipped_files() -> list[Path]:
    """Everything git tracks (or would track) minus plans, specs and recorded bench results; files deleted in the tree are skipped."""
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout.splitlines()
    files = []
    for rel in sorted(set(out)):
        p = ROOT / rel
        if (rel.startswith(SKIP_DIRS) or "/fixtures/" in rel or not p.is_file() or p.suffix.lower() in BINARY
                or p.suffix == ".jsonl" or rel == SELF):
            continue
        files.append(p)
    return files


def lines_of(p: Path) -> list[tuple[int, str]]:
    try:
        return list(enumerate(p.read_text(encoding="utf-8").splitlines(), 1))
    except UnicodeDecodeError:
        return []


def scan(pattern: re.Pattern, allow: re.Pattern | None = None, skip=lambda rel: False) -> list[str]:
    hits = []
    for p in shipped_files():
        rel = p.relative_to(ROOT).as_posix()
        if skip(rel):
            continue
        for n, ln in lines_of(p):
            if pattern.search(ln) and not (allow and allow.search(ln)):
                hits.append(f"{rel}:{n}: {ln.strip()[:140]}")
    return hits


TOOLS = sorted({p.stem for d in (ROOT / "skills" / "swarm" / "scripts", ROOT / "skills" / "swarm" / "probes")
                for p in d.glob("*.py") if not p.stem.startswith("_")} | {"assign", "split", "shape"})
# A launcher call is `sl <tool>`; a variable named sl followed by "and" is not one, so the word after it must be a tool.
SL_LAUNCHER = re.compile(r"\bsl\s+(" + "|".join(TOOLS) + r")\b|\$SL\b|\$\{SL\}|scripts/sl\b")

REMOVED = r"cost_live|cost_table|host_cost|autocommit|commit_task|models_refresh"
CALLED = r"(?:superlite-cli|\$CLI|\$\{CLI\})\"?\s+"
# A removed tool is named only as a launcher call, an import, or a scripts/ path. `session split`, `dispatch assign`
# and `dispatch ready` are real subcommands; a JSON key such as report.py's `cost_table` is data.
REMOVED_TOOLS = re.compile(
    rf"{CALLED}(?:{REMOVED}|assign|split|shape)\b"
    rf"|\bimport\s+(?:{REMOVED})\b|\bfrom\s+(?:{REMOVED})\s+import\b|\bscripts/(?:{REMOVED})\.py\b"
    r"|\b(plan_tools|pt_review)\s+shape\b"
    rf"|{CALLED}\w+\s+prune\b|\b(history|metrics|session|incident|orchestrator)\.py\s+prune\b"
    r"|\bprune\s+--(older|keep|days)\b")

COMMIT = re.compile(r"\bcommit\w*", re.I)
COMMIT_OK = re.compile("|".join([
    r"git (-C \S+ )?commit", r"commit-tree", r"--no-commit", r"commit\.gpgsign", r"commit-msg", r"\^\{commit\}",
    r"[\"']commit[\"']", r"`commit`",                                    # git's own subcommand named in a list
    r"merge commit", r"root commit", r"\{commit\}", r"base commit", r"commit sha", r"newest commit", r"one commit", r"Commits the local base", r"about to commit",
    r"before committing", r"all committed", r"ahead by \{?n\}? commit", r"commits? ahead", r"no commits yet", r"recent commits", r"commits? (since|the base)",
    r"(never|not|n't|nothing is|no|unless)\s+(\w+\s+)?commit", r"commit or stash",
    r"Commit to <", r"commit to the session branch", r"Leave uncommitted",     # the one question at session finish
    r"commits? (already )?on the session branch", r"commit on `?superlite/sessions",
    r"co-?authored-?by", r"Cursor", r"attribution", r"on commits", r"a commit message", r"commit message",
    r"^\s*COMMITS\s*$", r"files and commits", r"`commands`, `commits`",     # help.txt section on not committing
    r"Shadow-commit",                                                       # checkpoint.py: git commit-tree
    r"A committed `superlite\.json`", r"version or commit", r"older commit", r"or commit to the spec", r"tag or commit",
]), re.I)
def test_no_sl_launcher_left():
    hits = scan(SL_LAUNCHER)
    assert not hits, "\n".join(hits)


def test_using_superlite_skill_size_cap():
    # the skill is delivered whole at every session start, so it stays small
    assert len((ROOT / "skills" / "using-superlite" / "SKILL.md").read_text(encoding="utf-8")) <= 2000


def test_scan_patterns_catch_what_they_name():
    assert SL_LAUNCHER.search("bash sl dispatch") and not SL_LAUNCHER.search("if run != sl and x")
    assert SL_LAUNCHER.search("${SL}/x") and SL_LAUNCHER.search("scripts/sl run_tests")
    assert not SL_LAUNCHER.search("false positives like slow tests")
    assert REMOVED_TOOLS.search("superlite-cli assign --x") and REMOVED_TOOLS.search("scripts/cost_live.py")
    assert REMOVED_TOOLS.search("import cost_live") and not REMOVED_TOOLS.search('c["cost_table"]')
    assert not REMOVED_TOOLS.search("superlite-cli session split --new S") and not REMOVED_TOOLS.search("dispatch assign --task T1")
    assert REMOVED_TOOLS.search("superlite-cli history prune") and not REMOVED_TOOLS.search("a split view")
    assert COMMIT.search("superlite commits the task") and not COMMIT_OK.search("superlite commits the task")
    assert COMMIT_OK.search("never run git commit here") and COMMIT_OK.search('ask "Commit to <branch>"')


# ---- tests that only grep a markdown file

MD_READERS = {"read_text", "read", "readlines"}
STRUCTURAL = {"cli", "run", "check_output", "check_call", "Popen", "call", "main", "loads", "load", "parse", "extract_calls"}
ALLOWED_SIZE_TESTS = ("size", "limit", "lines", "length")


def _mentions_md(node: ast.AST, md_names: set[str]) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.endswith(".md"):
            return True
        if isinstance(n, ast.Name) and n.id in md_names:
            return True
    return False


def _calls(node: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(node) if isinstance(n, ast.Call)]


def _name(call: ast.Call) -> str:
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""


def _reads_md(node: ast.AST, md_names: set[str]) -> bool:
    for c in _calls(node):
        if isinstance(c.func, ast.Attribute) and c.func.attr in MD_READERS and _mentions_md(c.func.value, md_names):
            return True
        if _name(c) == "open" and c.args and _mentions_md(c.args[0], md_names):
            return True
    return False


def phrase_only_tests(tree: ast.Module) -> list[str]:
    """Test functions that read a .md file (directly or by a module helper), assert a phrase `in` it, and run or parse nothing."""
    md_names = {t.id for a in tree.body if isinstance(a, ast.Assign) and _mentions_md(a.value, set())
                for t in a.targets if isinstance(t, ast.Name)}
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    reader = {k for k, f in funcs.items() if not k.startswith("test") and _reads_md(f, md_names)}
    structural = set(STRUCTURAL)
    changed = True
    while changed:  # a helper is structural when it runs a tool or parses, directly or through another helper
        changed = False
        for k, f in funcs.items():
            if k not in structural and any(_name(c) in structural for c in _calls(f)):
                structural.add(k)
                changed = True
    bad = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name.startswith("test")]:
        names = {_name(c) for c in _calls(fn)}
        reads = _reads_md(fn, md_names) or bool(names & reader)
        phrase = any(isinstance(c, ast.Compare) and any(isinstance(o, (ast.In, ast.NotIn)) for o in c.ops)
                     and isinstance(c.left, ast.Constant) and isinstance(c.left.value, str) for c in ast.walk(fn))
        if reads and phrase and not (names & structural) and not any(w in fn.name for w in ALLOWED_SIZE_TESTS):
            bad.append(fn.name)
    return bad


def test_no_phrase_grep_tests():
    bad = []
    for p in sorted((ROOT / "tests").rglob("test_*.py")):
        rel = p.relative_to(ROOT).as_posix()
        if rel == SELF:
            continue
        for name in phrase_only_tests(ast.parse(p.read_text(encoding="utf-8"))):
            bad.append(f"{rel}::{name}")
    assert not bad, "\n".join(bad)


def test_phrase_detector_spares_a_test_that_drives_a_tool():
    src = ("def test_drive(repo):\n    plan = repo.write('p.md', FIXTURE.read_text())\n"
           "    r = repo.cli('lint', plan)\n    assert 'ok' in r.out\n"
           "FIXTURE = Path('f.md')\n")
    assert phrase_only_tests(ast.parse(src)) == []


def test_phrase_detector_flags_a_pure_grep():
    src = (
        "from pathlib import Path\n"
        "def test_bad():\n    t = Path('x/SKILL.md').read_text()\n    assert 'never commits' in t\n"
        "def test_ok():\n    t = Path('x/SKILL.md').read_text()\n    out = run('lint', t)\n    assert 'ok' in out\n"
        "def test_skill_size_limit():\n    t = Path('x/SKILL.md').read_text()\n    assert 'a' in t\n")
    assert phrase_only_tests(ast.parse(src)) == ["test_bad"]
