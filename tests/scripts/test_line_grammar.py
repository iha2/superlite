"""One declared pattern per agent-facing output line. The tools' real output must match it, and so must every line the docs quote."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "swarm" / "scripts"
FIX = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(SCRIPTS))

SLUG = r"[a-z0-9][a-z0-9-]*"
PATHISH = r"\S+"
# name -> (leader, full pattern for a real output line, pattern a doc quote must match; quotes may end in "..." or use <x>)
LINES = {
    "DISPATCH NOW:": (r"DISPATCH NOW: (none|T\d+( T\d+)*)", r"DISPATCH NOW:( .*)?"),
    "STABLE:": (r"STABLE:( T\d+)*", r"STABLE:( .*)?"),
    "FIX:": (r"FIX:", r"FIX:( .*)?"),
    "TIERS:": (r"TIERS: \w+=\w+( \w+=\w+)*", r"TIERS:( .*)?"),
    "SESSION worktree=": (rf"SESSION worktree={PATHISH} slug={SLUG} branch={PATHISH}", r"SESSION worktree=\S*( slug=\S*( branch=\S*)?)?"),
    "REDIRECT": (rf"REDIRECT worktree={PATHISH} slug={SLUG} branch={PATHISH}", r"REDIRECT( worktree=\S*( slug=\S*( branch=\S*)?)?)?"),
    "ASK untracked=": (rf"ASK untracked=\d+ slug={SLUG}: .+", r"ASK untracked=\S*( slug=\S*:?.*)?"),
    "ASK move": (rf"ASK move slug={SLUG}: .+", r"ASK move( slug=\S*:?.*)?"),
    "ask:": (r"ask: .+ Options: \w+( \| \w+)*", r"ask:( .*)?"),
    "CONFLICT": (r"CONFLICT\b.*", r"CONFLICT\b.*"),
    "SUSPECT HANG": (r"SUSPECT HANG: [A-Z]+ \S.* - no output for \d+s.*; process tree: .+", r"SUSPECT HANG\b.*"),
    "Tn <sha>": (r"T\d+ [0-9a-f]{7,40}", r"T\w+ (<sha>|[0-9a-f]{7,40}|x)"),
    "Verdict:": (r"Verdict: (APPROVED|CHANGES_REQUESTED)", r"Verdict: (APPROVED|CHANGES_REQUESTED|\S*)"),
    "budget:": (r"budget: coordinator \$\d+\.\d\d \| agents \$\d+\.\d\d \| total \$\d+\.\d\d.*", r"budget:( .*)?"),
    "next:": (r"next:( .+)?", r"next:( .*)?"),
}
FULL = {k: re.compile(v[0]) for k, v in LINES.items()}
QUOTED = {k: re.compile(v[1]) for k, v in LINES.items()}
# longest leader first so "ASK untracked=" is never read as another leader
LEADERS = sorted(LINES, key=len, reverse=True)

ENV = dict(os.environ, PYTHONIOENCODING="utf-8", SUPERLITE_NO_AUTO_SESSION="")


def leader_of(line: str) -> str | None:
    for k in LEADERS:
        if line.startswith(k):
            return k
    m = re.match(r"T\d+ [0-9a-f]{7,40}$", line)
    return "Tn <sha>" if m else None


def check_lines(output: str, expect: set[str]) -> set[str]:
    """Every output line that starts with a declared leader must match its pattern; returns the leaders seen."""
    seen = set()
    for ln in output.splitlines():
        k = leader_of(ln)
        if k is None:
            continue
        assert FULL[k].fullmatch(ln), f"{k!r} line breaks its declared pattern: {ln!r}"
        seen.add(k)
    assert expect <= seen, f"missing {sorted(expect - seen)} in:\n{output}"
    return seen


def tool(name: str, *args, cwd, env=None):
    cp = subprocess.run([sys.executable, str(SCRIPTS / f"{name}.py"), *map(str, args)], capture_output=True, text=True,
                        encoding="utf-8", cwd=cwd, env=env or ENV, timeout=180)
    return cp, cp.stdout + cp.stderr


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def make_repo(tmp_path, plan):
    r = tmp_path / "app"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "t")
    git(r, "config", "commit.gpgsign", "false")
    (r / ".gitignore").write_text(".superlite/\n")
    shutil.copy(plan, r / "plan.md")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    return r


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERLITE_HOME", str(tmp_path / "_home"))
    ENV["SUPERLITE_HOME"] = str(tmp_path / "_home")


def test_lint_lines(tmp_path):
    repo = make_repo(tmp_path, FIX / "plans" / "minisheet.md")
    cp, out = tool("plan_tools", "lint", "--plan", "plan.md", "--stable", cwd=repo)
    check_lines(out, {"STABLE:", "FIX:", "TIERS:", "DISPATCH NOW:"})


def test_lint_with_a_fix_task_lists_it(tmp_path):
    plan = (FIX / "plans" / "minisheet.md").read_text(encoding="utf-8").replace("**Produces:** evaluate(formula: str, get_cell) -> Value\n", "")
    (tmp_path / "p.md").write_text(plan, encoding="utf-8")
    repo = make_repo(tmp_path, tmp_path / "p.md")
    cp, out = tool("plan_tools", "lint", "--plan", "plan.md", "--stable", cwd=repo)
    check_lines(out, {"STABLE:", "FIX:", "DISPATCH NOW:"})


def test_session_lines(tmp_path):
    repo = make_repo(tmp_path, FIX / "plans" / "minisheet.md")
    cp, out = tool("session", "ensure", "--slug", "aa", cwd=repo)
    assert cp.returncode == 0
    check_lines(out, {"SESSION worktree="})
    (repo / "u.txt").write_text("x\n")
    cp, out = tool("session", "ensure", "--slug", "bb", cwd=repo)
    assert cp.returncode == 4
    check_lines(out, {"ASK move", "ask:"})
    cp, out = tool("session", "ensure", "--slug", "bb", "--move", "yes", cwd=repo)
    assert cp.returncode == 4
    check_lines(out, {"ASK untracked=", "ask:"})
    cp, out = tool("session", "ensure", "--slug", "bb", "--move", "yes", "--untracked", "stash", cwd=repo)
    assert cp.returncode == 0, out
    check_lines(out, {"SESSION worktree="})
    cp, out = tool("session", "use", "--slug", "aa", cwd=repo)
    assert cp.returncode == 3
    check_lines(out, {"REDIRECT"})


def test_checkpoint_and_next_lines(tmp_path):
    plan_fix = FIX / "run_plan"
    repo = make_repo(tmp_path, plan_fix / "plan.md")
    for rel in ("src/a.py", "tests/test_a.py", "src/b.py"):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text("X = 1\n", encoding="utf-8")
    env = dict(ENV, SUPERLITE_CLI=str(plan_fix / "fake_cli"), SL_TEST_PYTHON=sys.executable,
               SUPERLITE_PROBE_RUN=str(plan_fix / "no_such_probe.py"))
    cp, out = tool("checkpoint", "T1", "T2", "--plan", "plan.md", "--dispatch", cwd=repo, env=env)
    assert cp.returncode == 0, out
    check_lines(out, {"Tn <sha>", "next:"})


def test_suspect_hang_line():
    import testprobe
    info = {"verdict": "spinning", "test_file": "tests/test_x.py", "flat_s": 61.0, "cpu_pct": 99.0,
            "tree": [{"pid": 7, "name": "python"}]}
    check_lines(testprobe.suspect_line(info), {"SUSPECT HANG"})


def test_budget_line():
    import budget
    b = {"coordinator_usd": 1.0, "agents_usd": 2.0, "cost_usd": 3.0, "cap_usd": 10.0, "elapsed_min": 5.0,
         "projected_min": 9.0, "target_min": 8.0, "projected_usd": 4.5, "pct_cap": 0.3, "stats": "n=0", "hint": None, "late": False, "state": "ok"}
    check_lines(budget.line(b), {"budget:"})


def test_declared_patterns_reject_drift():
    assert not FULL["DISPATCH NOW:"].fullmatch("DISPATCH NOW:")
    assert not FULL["SESSION worktree="].fullmatch("SESSION worktree=/x slug=A b")
    assert not FULL["Verdict:"].fullmatch("Verdict: MAYBE")
    assert FULL["Tn <sha>"].fullmatch("T3 abcdef1234")


def quoted_snippets():
    from test_doc_contract import doc_files
    for f in doc_files():
        try:
            text = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for n, ln in enumerate(text.splitlines(), 1):
            for m in re.finditer(r"`([^`]+)`", ln):
                snippet = m.group(1).strip()
                k = leader_of(snippet) if not snippet.startswith("T") else None
                if k is None and re.match(r"T\w+ <sha>", snippet):
                    k = "Tn <sha>"
                if k is not None:
                    yield f.relative_to(ROOT).as_posix(), n, k, snippet


def test_named_lines_are_declared():
    snippets = list(quoted_snippets())
    assert len(snippets) > 20, "found almost no quoted lines"
    bad = []
    for path, n, k, snippet in snippets:
        s = re.sub(r"<[^>]*>", "x", snippet).rstrip(".…").rstrip()
        if not (QUOTED[k].fullmatch(s) or QUOTED[k].match(s)):
            bad.append(f"{path}:{n}: {snippet!r} does not fit the declared {k!r}")
    assert not bad, "\n".join(bad)
    assert set(LINES) == set(LEADERS) and len(LINES) == 15
