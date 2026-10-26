"""Checkpoint mode (the default) and `autocommit apply|clean|on|off|status`: superlite never commits unless asked."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parents[1] / "skills" / "swarm" / "scripts"
PLAN_FIX = HERE / "fixtures" / "run_plan"
ENV = {**os.environ, "SUPERLITE_SL": str(PLAN_FIX / "fake_sl"), "SL_TEST_PYTHON": sys.executable,
       "SUPERLITE_PROBE_RUN": str(PLAN_FIX / "no_such_probe.py"), "SUPERLITE_AUTOCOMMIT": "0"}


def run(script, *args, cwd, env=None):
    return subprocess.run([sys.executable, str(SCRIPTS / f"{script}.py"), *args], capture_output=True,
                          text=True, cwd=cwd, env=env or ENV, encoding="utf-8")


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
    return repo


def write(repo, rel, text):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def checkpoint(repo, *tasks):
    return run("checkpoint", *tasks, "--plan", "plan.md", cwd=repo)


def snapshot(repo):
    return {"head": git(repo, "rev-parse", "HEAD"), "branch": git(repo, "symbolic-ref", "HEAD"),
            "status": git(repo, "status", "--porcelain", "--untracked-files=all"),
            "index": git(repo, "ls-files", "--stage")}


def two_tasks(repo):
    write(repo, "src/a.py", "def parse(t):\n    return t.split()\n")
    write(repo, "tests/test_a.py", "def test_p():\n    assert 1\n")
    r = checkpoint(repo, "T1")
    assert r.returncode == 0, r.stdout + r.stderr
    write(repo, "src/b.py", "def lex(t):\n    return list(t)\n")
    r2 = checkpoint(repo, "T2")
    assert r2.returncode == 0, r2.stdout + r2.stderr
    return r.stdout.strip(), r2.stdout.strip()


def test_checkpoint_leaves_branch_index_and_tree_alone(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "src/a.py", "def parse(t):\n    return t.split()\n")
    write(repo, "tests/test_a.py", "def test_p():\n    assert 1\n")
    write(repo, "docs/staged.md", "x\n")
    git(repo, "add", "docs/staged.md")  # user's own staged file stays staged
    before = snapshot(repo)
    r = checkpoint(repo, "T1")
    assert r.returncode == 0, r.stdout + r.stderr
    sha = r.stdout.strip()
    assert snapshot(repo) == before
    assert git(repo, "cat-file", "-t", sha) == "commit"
    assert git(repo, "log", "-1", "--format=%s", sha) == "T1: parser"
    assert git(repo, "log", "-1", "--format=%an <%ae>", sha) == "Test <test@example.com>"
    assert "Co-Authored" not in git(repo, "log", "-1", "--format=%B", sha)
    assert git(repo, "rev-parse", f"{sha}^") == before["head"]
    assert sorted(git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", sha).split()) == [
        "src/a.py", "tests/test_a.py"]
    refs = git(repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs/superlite").split("\n")
    assert len(refs) == 1 and refs[0].endswith(f"/T1 {sha}")
    ledger = (repo / ".superlite" / "progress.md").read_text(encoding="utf-8")
    assert f"- checkpoint T1 {sha[:10]} parser" in ledger
    assert git(repo, "rev-list", "--count", "HEAD") == "1"


def test_second_checkpoint_chains_on_the_first(tmp_path):
    repo = make_repo(tmp_path)
    s1, s2 = two_tasks(repo)
    assert git(repo, "rev-parse", f"{s2}^") == s1
    assert git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", s2) == "src/b.py"


def test_apply_makes_one_commit_per_task_and_cleans_up(tmp_path):
    repo = make_repo(tmp_path)
    base = git(repo, "rev-parse", "HEAD")
    two_tasks(repo)
    r = run("autocommit", "apply", cwd=repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert git(repo, "rev-list", "--count", "HEAD") == "3"
    assert git(repo, "log", "--format=%s", f"{base}..HEAD").split("\n") == ["T2: lexer", "T1: parser"]
    assert "Co-Authored" not in git(repo, "log", "--format=%B", f"{base}..HEAD")
    assert git(repo, "status", "--porcelain") == ""
    assert git(repo, "for-each-ref", "refs/superlite") == ""


def test_apply_refuses_when_head_moved(tmp_path):
    repo = make_repo(tmp_path)
    two_tasks(repo)
    write(repo, "other.txt", "o\n")
    git(repo, "add", "other.txt")
    git(repo, "commit", "-q", "-m", "user commit")
    head = git(repo, "rev-parse", "HEAD")
    r = run("autocommit", "apply", cwd=repo)
    assert r.returncode != 0 and "HEAD moved" in r.stderr
    assert git(repo, "rev-parse", "HEAD") == head
    assert git(repo, "for-each-ref", "refs/superlite") != ""


def test_apply_refuses_unrelated_changes(tmp_path):
    repo = make_repo(tmp_path)
    two_tasks(repo)
    write(repo, "notes.txt", "mine\n")
    r = run("autocommit", "apply", cwd=repo)
    assert r.returncode != 0 and "unrelated" in r.stderr and "notes.txt" in r.stderr
    assert git(repo, "rev-list", "--count", "HEAD") == "1"


def test_clean_removes_refs(tmp_path):
    repo = make_repo(tmp_path)
    two_tasks(repo)
    assert run("autocommit", "clean", cwd=repo).returncode == 0
    assert git(repo, "for-each-ref", "refs/superlite") == ""
    assert not (repo / ".superlite" / "checkpoints.json").exists()


def test_switch_file_and_env_override(tmp_path):
    repo = make_repo(tmp_path)
    env = {k: v for k, v in ENV.items() if k != "SUPERLITE_AUTOCOMMIT"}
    assert "autocommit: off" in run("autocommit", "status", cwd=repo, env=env).stdout
    assert run("autocommit", "on", cwd=repo, env=env).returncode == 0
    assert json.loads((repo / ".superlite" / "autocommit.json").read_text()) == {"autocommit": True}
    assert "autocommit: on" in run("autocommit", "status", cwd=repo, env=env).stdout
    assert "autocommit: off" in run("autocommit", "status", cwd=repo, env={**env, "SUPERLITE_AUTOCOMMIT": "0"}).stdout
    run("autocommit", "off", cwd=repo, env=env)
    assert "autocommit: on" in run("autocommit", "status", cwd=repo, env={**env, "SUPERLITE_AUTOCOMMIT": "1"}).stdout
    assert "autocommit: off" in run("autocommit", "status", cwd=repo, env=env).stdout


def test_autocommit_on_makes_a_real_commit_and_no_refs(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "src/a.py", "def parse(t):\n    return t.split()\n")
    write(repo, "tests/test_a.py", "def test_p():\n    assert 1\n")
    r = run("checkpoint", "T1", "--plan", "plan.md", cwd=repo, env={**ENV, "SUPERLITE_AUTOCOMMIT": "1"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.strip() == git(repo, "rev-parse", "HEAD")
    assert git(repo, "for-each-ref", "refs/superlite") == ""


def test_finish_says_nothing_was_committed(tmp_path):
    repo = make_repo(tmp_path)
    two_tasks(repo)
    r = run("finish", cwd=repo)
    assert r.returncode == 0, r.stderr
    assert "NOTHING WAS COMMITTED" in r.stdout and "changed files: 3" in r.stdout
    assert "sl autocommit apply" in r.stdout and "refs/superlite/" in r.stdout
    assert "sl autocommit clean" in r.stdout
    r = run("finish", cwd=repo, env={**ENV, "SUPERLITE_AUTOCOMMIT": "1"})
    assert "NOTHING WAS COMMITTED" not in r.stdout
