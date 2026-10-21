"""A finished task is a checkpoint, never a "commit": the ledger reads both words and writes `checkpoint`, the old
`commit_task` name still runs through its shim, and no model-facing text tells anyone to "commit" a task. "Commit"
stays only where it means a real git commit (/superlite:commit, sl autocommit, never-run-git-commit rules)."""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SCRIPTS = ROOT / "skills" / "subagent-driven-development" / "scripts"
PLAN_FIX = HERE / "fixtures" / "run_plan"
sys.path.insert(0, str(SCRIPTS))

ENV = {**os.environ, "SUPERLITE_SL": str(PLAN_FIX / "fake_sl"), "SL_TEST_PYTHON": sys.executable,
       "SUPERLITE_AUTOCOMMIT": "0", "SUPERLITE_PROBE_RUN": str(PLAN_FIX / "no_such_probe.py")}

MIXED = ("# Progress ledger\n\n- start abc1234\n- commit T1 abcdef1234 parser\n"
         "- checkpoint T2 1234567890 lexer\nRuling: keep it\n")


def git(repo, *args):
    r = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "commit.gpgsign", "false")
    shutil.copy(PLAN_FIX / "plan.md", repo / "plan.md")
    (repo / "README.md").write_text("base\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("X = 1\n", encoding="utf-8")
    return repo


# ------------------------------------------------------------------ the ledger

def test_ledger_reads_old_commit_and_new_checkpoint_lines(tmp_path):
    import budget
    import checkpoint
    import finish
    import plan_tools
    sl = tmp_path / ".superlite"
    sl.mkdir()
    (sl / "progress.md").write_text(MIXED, encoding="utf-8")
    assert checkpoint.checkpointed_tasks(sl / "progress.md") == {"T1", "T2"}
    assert budget.committed(sl) == {"T1", "T2"}
    assert {"T1", "T2"} <= plan_tools.dispatched_tasks(sl)
    assert [c[0] for c in finish.read_ledger(sl / "progress.md")[1]] == ["T1", "T2"]
    assert finish.CHECKPOINT is checkpoint.LEDGER_LINE  # one shared regex, not a copy


def test_new_runs_write_checkpoint_lines_and_finish_says_checkpoints(tmp_path):
    repo = make_repo(tmp_path)
    r = subprocess.run([sys.executable, str(SCRIPTS / "checkpoint.py"), "T1", "--plan", "plan.md"],
                       cwd=repo, capture_output=True, text=True, encoding="utf-8", env=ENV)
    assert r.returncode == 0, r.stdout + r.stderr
    sha = r.stdout.strip()
    ledger = (repo / ".superlite" / "progress.md").read_text(encoding="utf-8")
    assert f"- checkpoint T1 {sha[:10]} parser" in ledger and "- commit " not in ledger
    assert git(repo, "rev-list", "--count", "HEAD") == "1"  # the branch is untouched
    out = subprocess.run([sys.executable, str(SCRIPTS / "finish.py")], cwd=repo, capture_output=True,
                         text=True, encoding="utf-8", env=ENV).stdout
    assert "checkpoints: 1" in out and "commits:" not in out


# ------------------------------------------------------------------- the shim

def test_commit_task_shim_still_runs_and_keeps_the_old_names(tmp_path):
    import checkpoint
    import commit_task
    assert commit_task.commit_one is checkpoint.checkpoint_one
    assert commit_task.committed_tasks is checkpoint.checkpointed_tasks
    assert commit_task.COMMIT_LINE is checkpoint.LEDGER_LINE
    repo = make_repo(tmp_path)
    r = subprocess.run(["bash", str(SCRIPTS / "sl"), "commit_task", "T1", "--plan", "plan.md"],
                       cwd=repo, capture_output=True, text=True, encoding="utf-8",
                       env={**ENV, "SUPERLITE_PYTHON": sys.executable})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "- checkpoint T1 " in (repo / ".superlite" / "progress.md").read_text(encoding="utf-8")


# --------------------------------------------------------------- the text guard

# Model- and user-facing text: skills, agents, commands, top-level docs, README, screens, the prime and Wave 0 brief.
TEXT = sorted({*(ROOT / "skills").rglob("*.md"), *(ROOT / "agents").glob("*.md"), *(ROOT / "commands").glob("*.md"),
               *(ROOT / "docs").glob("*.md"), ROOT / "README.md", *(ROOT / "assets").glob("*.txt"),
               SCRIPTS / "build_prime.py", SCRIPTS / "wave0.py"})
WORD = re.compile(r"\bcommits?\b", re.I)
# Each allowed use is a real git commit, or names the old ledger word / the shim.
ALLOWED = [re.compile(p, re.I) for p in (
    r"commits with autocommit", r"; a commit\b", r"-> commits", r"commits decision", r"per-task commits",
    r"/superlite:commit", r"auto-?commit", r"git commit", r"`commit`", r"do not commit\b", r"^\s*- commits:",
    r"real (git )?commits?", r"into (one )?commits?", r"one commit per task", r"commit each task on",
    r"never commits on its own", r"does not commit by default", r"older commit", r"commit all work",
    r"about to commit, push", r"otherwise commit or report", r"commit, then", r"`- commit tn`",
    r"commit/push/pr", r"commit messages", r"recent commits", r"commits ahead", r"^\s*commits\s*$",
    r"files and commits", r"`commits`")]


def test_no_text_tells_a_model_to_commit_a_task():
    bad = []
    for p in TEXT:
        for n, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            rel = f"{p.relative_to(ROOT).as_posix()}:{n}"
            if "commit_task" in ln and "old name" not in ln:
                bad.append(f"{rel}: {ln.strip()[:120]}")
            rest = ln
            for a in ALLOWED:
                rest = a.sub("", rest)
            if WORD.search(rest):
                bad.append(f"{rel}: {ln.strip()[:120]}")
    assert not bad, "say checkpoint, not commit:\n" + "\n".join(bad)


@pytest.mark.parametrize("name", ["fixer-sonnet", "fixer-sonnet-high", "fixer-opus"])
def test_fixers_checkpoint_and_report_the_checkpoint_id(name):
    text = (ROOT / "agents" / f"{name}.md").read_text(encoding="utf-8")
    assert '`bash "$SL" checkpoint <Tn>`' in text and "the checkpoint id" in text
