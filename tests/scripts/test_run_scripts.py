"""Tests for the D17 run scripts. plan_tools, incident and probe_run are replaced
by the small fakes in fixtures/run_plan, reached through a fake `sl` launcher
(SUPERLITE_SL), as the real tools are reached through `bash sl <tool>` (D18),
so these tests do not depend on the scripts other tasks build."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

import pytest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = (HERE.parents[1] / "skills" / "swarm" / "scripts")
FIX = HERE / "fixtures"
PLAN_FIX = FIX / "run_plan"
sys.path.insert(0, str(SCRIPTS))

ENV = {
    **os.environ,
    "SUPERLITE_SL": str(PLAN_FIX / "fake_sl"),     # D18: tools call each other via sl
    "SL_TEST_PYTHON": sys.executable,
    "SUPERLITE_AUTOCOMMIT": "1",                   # these tests assert real commits
    "SUPERLITE_PROBE_RUN": str(PLAN_FIX / "no_such_probe.py"),
}


def run(script, *args, cwd=None, env=None):
    return subprocess.run([sys.executable, str(SCRIPTS / f"{script}.py"), *map(str, args)],
                          capture_output=True, text=True, cwd=cwd, env=env or ENV,
                          encoding="utf-8")


def plan_args():
    return ["--plan", str(PLAN_FIX / "plan.md")]


def read_log(path):
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]


# ---------------------------------------------------------------- build_prime

def prime(tmp_path, plan=None, spec=None, sections="1,3,5", out="prime.md", *extra):
    r = run("build_prime", "--plan", plan or PLAN_FIX / "plan.md",
            "--spec", spec or PLAN_FIX / "spec.md", "--sections", sections,
            "--out", tmp_path / out, "--prime", "full", *extra)
    return r


def test_prime_is_byte_identical_across_runs(tmp_path):
    a = prime(tmp_path, out="a.md")
    b = prime(tmp_path, out="b.md")
    assert a.returncode == 0, a.stderr
    assert a.stdout == b.stdout
    assert (tmp_path / "a.md").read_bytes() == (tmp_path / "b.md").read_bytes()
    digest, size = a.stdout.split()
    data = (tmp_path / "a.md").read_bytes()
    assert digest == hashlib.sha256(data).hexdigest()
    assert int(size) == len(data)
    # CRLF input and trailing spaces change nothing
    crlf_plan = tmp_path / "plan_crlf.md"
    crlf_plan.write_bytes((PLAN_FIX / "plan.md").read_bytes().replace(b"\n", b"  \r\n"))
    crlf_spec = tmp_path / "spec_crlf.md"
    crlf_spec.write_bytes((PLAN_FIX / "spec.md").read_bytes().replace(b"\n", b"\r\n"))
    c = prime(tmp_path, plan=crlf_plan, spec=crlf_spec, out="c.md")
    assert c.stdout == a.stdout
    assert b"\r" not in data and b" \n" not in data
    assert b"2026" not in data and b"UTC" not in data  # no timestamp


def test_prime_section_order_fixed(tmp_path):
    a = prime(tmp_path, sections="5,1", out="a.md")
    b = prime(tmp_path, sections="1,5", out="b.md")
    assert a.stdout == b.stdout
    text = (tmp_path / "a.md").read_text(encoding="utf-8")
    assert text.index("## 1. Intent") < text.index("## 5. Done when")
    assert "## 3. Errors" not in text


def test_prime_nested_section_not_duplicated_and_missing_fails(tmp_path):
    r = prime(tmp_path, sections="2,2.1")
    assert r.returncode == 0
    text = (tmp_path / "prime.md").read_text(encoding="utf-8")
    assert text.count("Nested detail.") == 1
    bad = prime(tmp_path, sections="9", out="bad.md")
    assert bad.returncode == 2 and "not found" in bad.stderr
    assert not (tmp_path / "bad.md").exists()


def test_prime_enforces_size_cap(tmp_path):
    r = run("build_prime", "--plan", PLAN_FIX / "plan.md", "--spec", PLAN_FIX / "spec.md",
            "--sections", "1", "--prime", "full", "--out", tmp_path / "p.md", "--max-bytes", "100")
    assert r.returncode == 1 and "cap" in r.stderr
    assert not (tmp_path / "p.md").exists()


# ----------------------------------------------------------------- next_tasks

def ready(*extra):
    r = run("next_tasks", *plan_args(), *extra)
    assert r.returncode == 0, r.stderr
    return r.stdout.split()


def test_next_tasks_longest_first_and_cap():
    assert ready() == ["T2", "T5", "T4", "T1"]          # 3, 2.5, 2, 1 min
    assert ready("--cap", "2") == ["T2", "T5"]
    assert ready("--cap", "2", "--running", "T2") == ["T5"]
    assert ready("--cap", "1", "--running", "T2") == []
    assert ready("--committed", "T1,T2") == ["T5", "T3", "T4"]  # T3/T4 tie: plan order
    assert ready("--committed", "T1,T2,T3,T4,T5", "--running", "T6") == []
    assert ready("--committed", "T1,T2,T3,T4,T5") == ["T6"]


def test_parse_owns_expands_braces_and_ignores_notes():
    import next_tasks as nt
    info = nt.parse_plan(PLAN_FIX / "plan.md")
    assert info["T4"]["owns"] == ["pkg/d.py", "pkg/e.py", "docs/"]
    assert info["T2"]["est_time"] == 3.0 and info["T5"]["est_context"] == 25.0
    assert info["T1"]["title"] == "parser"
    assert nt.parse_owns("`a/{b,c}.py`, `tests/test_x.py`") == [
        "a/b.py", "a/c.py", "tests/test_x.py"]


# --------------------------------------------------------------------- assign

def pool(tmp_path, agents):
    p = tmp_path / "pool.json"
    p.write_text(json.dumps({"plan": str(PLAN_FIX / "plan.md"), "agents": agents}))
    return p


def assign(tmp_path, task, agents):
    r = run("assign", "--task", task, "--pool", pool(tmp_path, agents))
    assert r.returncode == 0, r.stderr
    return r.stdout.strip(), r.stderr


def test_assign_known_match_and_tie_polls(tmp_path):
    a1 = {"id": "a1", "tasks": ["T1"], "context_k": 30, "idle_min": 5}
    a2 = {"id": "a2", "tasks": ["T4"], "context_k": 30, "idle_min": 5}
    assert assign(tmp_path, "T3", [a1, a2])[0] == "agent=a1"   # built a dependency: +5
    # equal scores: both built a dependency
    b1 = {"id": "b1", "tasks": ["T1"], "context_k": 20}
    b2 = {"id": "b2", "tasks": ["T2"], "context_k": 20}
    assert assign(tmp_path, "T3", [b1, b2])[0] == "poll"
    # no signal at all
    c1 = {"id": "c1", "tasks": ["T4"], "context_k": 10}
    c2 = {"id": "c2", "tasks": ["T5"], "context_k": 10}
    assert assign(tmp_path, "T3", [c1, c2])[0] == "poll"


def test_assign_file_and_package_scores(tmp_path):
    # T6 owns src/main.py: a1 read it (+3, +1 package); a2 only shares the package (+1)
    a1 = {"id": "a1", "tasks": [], "files": ["src/main.py"]}
    a2 = {"id": "a2", "tasks": [], "files": ["src/other.py"]}
    assert assign(tmp_path, "T6", [a1, a2])[0] == "agent=a1"
    # margin below 3 polls: +3 file vs +1 package gives 4 vs 1 -> margin 3 wins, 2 vs 1 polls
    a3 = {"id": "a3", "tasks": [], "files": ["src/x.py"]}
    assert assign(tmp_path, "T6", [a2, a3])[0] == "poll"


def test_assign_eligibility_and_busy(tmp_path):
    full = {"id": "full", "tasks": ["T1"], "context_k": 70}            # 70 + 15 > 80
    many = {"id": "many", "tasks": ["T1", "T2", "T4"], "context_k": 10}  # 3 tasks
    old = {"id": "old", "tasks": ["T1"], "context_k": 10, "idle_min": 51}
    busy = {"id": "busy", "tasks": ["T3"], "context_k": 10, "busy": True}
    out, err = assign(tmp_path, "T6", [full, many, old, busy])
    assert out == "fresh"
    for name in ("full", "many", "old"):
        assert f"evict={name}" in err
    assert "busy" not in err
    ok = {"id": "ok", "tasks": ["T3"], "context_k": 10, "idle_min": 50}
    assert assign(tmp_path, "T6", [full, ok])[0] == "agent=ok"


# ----------------------------------------------------------------- checkpoint

def git(repo, *args):
    r = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                       encoding="utf-8")
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


def commit(repo, task="T1", *extra, env=None):
    return run("checkpoint", task, "--plan", "plan.md", *extra, cwd=repo, env=env)


def commits(repo):
    return int(git(repo, "rev-list", "--count", "HEAD"))


def test_commit_stages_only_owns_and_records_ledger(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "src/a.py", "def parse(text):\n    return text.split()\n")
    write(repo, "tests/test_a.py", "from src.a import parse\n\n\ndef test_p():\n    assert parse('a b')\n")
    r = commit(repo)
    assert r.returncode == 0, r.stdout + r.stderr
    sha = r.stdout.strip()
    assert sha == git(repo, "rev-parse", "HEAD") and len(sha) == 40
    assert git(repo, "log", "-1", "--format=%s") == "T1: parser"
    assert sorted(git(repo, "show", "--name-only", "--format=", "HEAD").split()) == [
        "src/a.py", "tests/test_a.py"]
    ledger = (repo / ".superlite" / "progress.md").read_text(encoding="utf-8")
    assert f"- checkpoint T1 {sha[:10]} parser" in ledger
    assert git(repo, "status", "--porcelain", "--untracked-files=no") == ""


def test_commit_blocks_out_of_scope(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "src/a.py", "X = 1\n")
    write(repo, "stray.txt", "oops\n")
    # src/b.py is another task's file: left alone, not reported
    before = commits(repo)
    r = commit(repo)
    assert r.returncode == 1
    assert "out-of-scope stray.txt" in r.stdout
    assert "src/a.py" not in r.stdout
    assert commits(repo) == before
    assert git(repo, "diff", "--cached", "--name-only") == ""
    log = read_log(repo / ".superlite" / "incidents.jsonl")
    assert {e["file"] for e in log} == {"stray.txt"}
    assert all(e["kind"] == "exception" and e["agent"] == "checkpoint" for e in log)


def test_commit_two_tasks_mid_build_leaves_other_tasks_files(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "src/a.py", "X = 1\n")                    # T1, finished
    write(repo, "src/b.py", "Y = 2\n")                    # T2, still mid-build
    r = commit(repo, "T1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert git(repo, "show", "--name-only", "--format=", "HEAD") == "src/a.py"
    assert "src/b.py" in git(repo, "status", "--porcelain")
    assert not (repo / ".superlite" / "incidents.jsonl").exists()
    r = commit(repo, "T2")
    assert r.returncode == 0, r.stdout + r.stderr
    assert git(repo, "show", "--name-only", "--format=", "HEAD") == "src/b.py"
    assert git(repo, "status", "--porcelain", "--untracked-files=no") == ""


def test_commit_allow_other_tasks_skips_their_files(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "src/a.py", "X = 1\n")
    write(repo, "src/b.py", "Y = 2\n")
    r = commit(repo, "T1", "--allow-other-tasks")
    assert r.returncode == 0, r.stdout
    assert git(repo, "show", "--name-only", "--format=", "HEAD") == "src/a.py"
    assert "src/b.py" in git(repo, "status", "--porcelain")
    write(repo, "stray.txt", "x")                          # owned by nobody still blocks
    write(repo, "src/c.py", "Z = 3\n")
    assert commit(repo, "T3", "--allow-other-tasks").returncode == 1


def test_pytest_cmd_falls_back_to_uv_when_pytest_missing(monkeypatch):
    import _pyrun
    monkeypatch.setattr(_pyrun, "have_pytest", lambda: False)
    monkeypatch.setattr(_pyrun.shutil, "which", lambda n: "/x/uv")
    assert _pyrun.pytest_cmd() == ["/x/uv", "run", "--no-project", "--with", "pytest",
                                   "python", "-m", "pytest"]
    monkeypatch.setattr(_pyrun.shutil, "which", lambda n: None)
    with pytest.raises(_pyrun.PytestUnavailable, match="pytest is not installed"):
        _pyrun.pytest_cmd()
    monkeypatch.setattr(_pyrun, "have_pytest", lambda: True)
    assert _pyrun.pytest_cmd() == [sys.executable, "-m", "pytest"]


def test_run_tree_kills_grandchild_on_timeout(tmp_path):
    import _pyrun
    pidfile = tmp_path / "pid"
    child = ("import subprocess,sys,time;"
             "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
             f"open(r'{pidfile}','w').write(str(p.pid));time.sleep(60)")
    with pytest.raises(subprocess.TimeoutExpired):
        _pyrun.run_tree([sys.executable, "-c", child], timeout=3, capture_output=True)
    gpid = int(pidfile.read_text())
    time.sleep(1)
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {gpid}"], capture_output=True,
                             text=True).stdout
        assert str(gpid) not in out
    else:
        with pytest.raises(OSError):
            os.kill(gpid, 0)


def test_launch_probes_passes_review_focus_categories(tmp_path, monkeypatch):
    import checkpoint
    plan = tmp_path / "plan.md"
    plan.write_text("## Review Focus\n\n- pkg: deep, numeric\n- other: state, deep\n",
                    encoding="utf-8")
    assert checkpoint.probe_categories(str(plan)) == "deep,numeric,state"
    seen = []
    probe = tmp_path / "probe_run.py"
    probe.write_text("")
    monkeypatch.setenv("SUPERLITE_PROBE_RUN", str(probe))
    monkeypatch.setattr(checkpoint, "spawn_background", lambda cmd: seen.append(cmd))
    checkpoint.launch_probes("abc1234", str(plan))
    assert seen[0][-2:] == ["--categories", "deep,numeric,state"]


def test_commit_rejects_syntax_error(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "src/a.py", "def broken(:\n    pass\n")
    before = commits(repo)
    r = commit(repo)
    assert r.returncode == 1
    assert "syntax src/a.py:1" in r.stdout
    assert commits(repo) == before
    log = read_log(repo / ".superlite" / "incidents.jsonl")
    assert log[0]["file"] == "src/a.py" and log[0]["line"] == 1
    assert not (repo / ".superlite" / "progress.md").exists()


def test_commit_rejects_failing_import_and_accepts_package_import(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "src/a.py", "import module_that_does_not_exist_xyz\n")
    r = commit(repo)
    assert r.returncode == 1 and "import src/a.py:1" in r.stdout
    assert "ModuleNotFoundError" in r.stdout
    assert commits(repo) == 1
    write(repo, "src/a.py", "from src import b_helper\nVALUE = b_helper.V\n")
    write(repo, "src/b_helper.py", "V = 1\n")
    # b_helper is not in T1's Owns -> out of scope; the import itself would pass
    assert commit(repo).returncode == 1
    write(repo, "src/a.py", "import json\nVALUE = json.dumps(1)\n")
    (repo / "src" / "b_helper.py").unlink()
    r = commit(repo)
    assert r.returncode == 0, r.stdout


def test_commit_nothing_and_unknown_task(tmp_path):
    repo = make_repo(tmp_path)
    r = commit(repo)
    assert r.returncode == 1 and "nothing to checkpoint" in r.stderr
    assert commit(repo, "T99").returncode == 2


def test_commit_launches_probe_in_background_only_if_present(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "src/a.py", "X = 1\n")
    env = {**ENV, "SUPERLITE_PROBE_RUN": str(PLAN_FIX / "fake_probe.py")}
    r = commit(repo, env=env)
    assert r.returncode == 0, r.stdout
    marker = repo / "probe_marker.txt"
    for _ in range(100):
        if marker.exists() and marker.read_text():
            break
        time.sleep(0.1)
    assert marker.read_text() == f"--checkpoint {r.stdout.strip()}"
    # absent probe file: no launch, still commits
    marker.unlink()
    write(repo, "src/a.py", "X = 2\n")
    r2 = commit(repo)
    assert r2.returncode == 0
    time.sleep(0.5)
    assert not marker.exists()


# ------------------------------------------------------------------ run_tests

def project(tmp_path, *names):
    proj = tmp_path / "proj"
    proj.mkdir()
    for name in names:
        shutil.copy(FIX / "run_pytest" / f"check_{name}.py", proj / f"test_{name}.py")
    return proj


def test_run_tests_logs_failures_and_verified(tmp_path):
    proj = project(tmp_path, "pass", "fail")
    r = run("run_tests", "--full", cwd=proj)
    assert r.returncode == 1, r.stdout + r.stderr
    passed, failed, skipped, seconds = r.stdout.split()
    assert (passed, failed, skipped) == ("3", "1", "1") and float(seconds) >= 0
    log = read_log(proj / ".superlite" / "incidents.jsonl")
    fails = [e for e in log if e["kind"] == "test-failure"]
    assert len(fails) == 1
    assert fails[0]["file"] == "test_fail.py" and fails[0]["line"] == 9
    assert "test_broken" in fails[0]["context"] and "arithmetic" in fails[0]["context"]
    assert fails[0]["agent"] == "run_tests" and fails[0]["resolution"] == "open"
    verified = [e for e in log if e["kind"] == "verified"]
    assert [e["file"] for e in verified] == ["test_pass.py"]   # the failing file is not
    ledger = (proj / ".superlite" / "progress.md").read_text(encoding="utf-8")
    assert "tests full passed=3 failed=1 skipped=1" in ledger


def test_run_tests_green_full_and_fast(tmp_path):
    proj = project(tmp_path, "pass")
    r = run("run_tests", "--fast", "test_pass.py", cwd=proj)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split()[:3] == ["2", "0", "0"]
    assert not (proj / ".superlite" / "incidents.jsonl").exists()   # fast: no verified
    for _ in range(2):                                               # full twice: deduped
        r = run("run_tests", cwd=proj)
        assert r.returncode == 0 and r.stdout.split()[:3] == ["2", "0", "0"]
    log = read_log(proj / ".superlite" / "incidents.jsonl")
    assert [(e["file"], e["kind"]) for e in log] == [("test_pass.py", "verified")]
    assert "tests fast" in (proj / ".superlite" / "progress.md").read_text(encoding="utf-8")


def test_run_tests_reports_collection_problem(tmp_path):
    proj = project(tmp_path, "pass")
    r = run("run_tests", "--fast", "missing_dir", cwd=proj)
    assert r.returncode == 2
    assert r.stdout.split()[:3] == ["0", "0", "0"]


# --------------------------------------------------------------------- finish

def test_finish_collects_rulings(tmp_path):
    ledger = tmp_path / "progress.md"
    ledger.write_text(
        "# Progress ledger\n\n"
        "- commit T1 abcdef1234 parser\n"
        "Ruling: keep Workbook.set returning a list\n"
        "- commit T2 1234567890 lexer\n"
        "- **Ruling:** accept the sequential T5 -> T6 chain\n"
        "- tests fast passed=4 failed=1 skipped=0 seconds=0.5\n"
        "- tests full passed=9 failed=0 skipped=2 seconds=3.2\n"
        "Ruling: third and last\n", encoding="utf-8")
    log = tmp_path / "i.jsonl"
    log.write_text("\n".join(json.dumps(e) for e in [
        {"file": "a.py", "line": 3, "context": "open one", "kind": "workaround"},
        {"file": "b.py", "line": 4, "context": "fixed", "kind": "exception",
         "resolution": "fixed"},
        {"file": "c.py", "line": 0, "context": "checked", "kind": "verified"},
        {"file": "d.py", "line": 9, "context": "still open", "kind": "flaky",
         "resolution": "open"}]) + "\n", encoding="utf-8")
    r = run("finish", "--ledger", ledger, "--log", log)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    rulings = [ln for ln in out.splitlines() if ln.startswith("- ") and
               ("Workbook" in ln or "sequential" in ln or "third" in ln)]
    assert rulings == ["- keep Workbook.set returning a list",
                       "- accept the sequential T5 -> T6 chain",
                       "- third and last"]
    assert "rulings: 3" in out and "commits: 2" in out  # ENV has auto-commit on
    assert "- T1 abcdef1234 parser" in out and "- T2 1234567890 lexer" in out
    assert "tests (full): 9 passed, 0 failed, 2 skipped in 3.2s" in out
    assert "open incidents: 2" in out
    assert "- a.py:3 [workaround] open one" in out and "- d.py:9 [flaky] still open" in out
    assert "b.py" not in out and "c.py" not in out
    assert run("finish", "--ledger", ledger, "--log", log).stdout == out   # deterministic


def test_finish_lists_scope_rulings_as_changes_from_the_approved_plan(tmp_path):
    ledger = tmp_path / "progress.md"
    ledger.write_text(
        "Ruling: keep the CLI flag names\n"
        "Ruling: [scope] drop CSV export - spec section 4 conflicts with 2 - cost if wrong: one task\n"
        "- **Ruling:** [SCOPE] rename parse() to parse_text() - clashes with stdlib - cost if wrong: re-run\n",
        encoding="utf-8")
    r = run("finish", "--ledger", ledger, "--log", tmp_path / "none.jsonl")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "rulings: 3" in out and "Changed from the approved plan: 2" in out
    block = out.split("Changed from the approved plan: 2", 1)[1].split("\n\n", 1)[0]
    assert "- drop CSV export" in block and "- rename parse() to parse_text()" in block
    assert "CLI flag names" not in block


def test_finish_without_scope_rulings_prints_no_change_block(tmp_path):
    ledger = tmp_path / "progress.md"
    ledger.write_text("Ruling: keep the CLI flag names\n", encoding="utf-8")
    out = run("finish", "--ledger", ledger, "--log", tmp_path / "none.jsonl").stdout
    assert "Changed from the approved plan" not in out


def test_finish_with_nothing_recorded(tmp_path):
    r = run("finish", "--ledger", tmp_path / "none.md", "--log", tmp_path / "none.jsonl")
    assert r.returncode == 0
    assert "commits: 0" in r.stdout and "rulings: 0" in r.stdout
    assert "tests: none recorded" in r.stdout and "open incidents: 0" in r.stdout


# ------------------------------------------------- seam checks, regression, summary

def wait_lines(path, n, timeout=90):
    for _ in range(int(timeout * 10)):
        if path.exists() and len(path.read_text(encoding="utf-8").splitlines()) >= n:
            break
        time.sleep(0.1)
    return sorted(path.read_text(encoding="utf-8").splitlines()) if path.exists() else []


def test_commit_launches_seam_checks_when_both_sides_committed(tmp_path):
    repo = make_repo(tmp_path)
    marker = repo / ".superlite" / "pt_marker.txt"
    write(repo, "src/a.py", "X = 1\n")
    assert commit(repo, "T1").returncode == 0
    write(repo, "src/b.py", "Y = 1\n")
    assert commit(repo, "T2").returncode == 0
    time.sleep(0.7)
    assert not marker.exists()                      # T3 not committed: no seam is complete
    write(repo, "src/c.py", "Z = 1\n")
    assert commit(repo, "T3").returncode == 0
    log = ".superlite/incidents.jsonl"
    assert wait_lines(marker, 2) == [
        f"contract --plan plan.md --seam T1,T3 --log {log}",
        f"contract --plan plan.md --seam T2,T3 --log {log}"]
    time.sleep(0.5)
    assert len(marker.read_text(encoding="utf-8").splitlines()) == 2
    write(repo, "src/main.py", "W = 1\n")           # T6 sits behind T3 only
    assert commit(repo, "T6").returncode == 0
    assert f"contract --plan plan.md --seam T3,T6 --log {log}" in wait_lines(marker, 3)


def test_commit_seam_check_needs_the_ledger_not_the_tree(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "src/c.py", "Z = 1\n")             # T3 first: its producers are uncommitted
    assert commit(repo, "T3").returncode == 0
    time.sleep(0.7)
    assert not (repo / ".superlite" / "pt_marker.txt").exists()


def test_commit_accepts_generated_regression_tests_and_never_stages_superlite(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "src/a.py", "X = 1\n")
    write(repo, "tests/regression/test_probe_deep_1.py", "def test_deep():\n    assert True\n")
    write(repo, ".superlite/probes/probe-results.json", "{}\n")
    write(repo, ".superlite/incidents.jsonl", "")
    r = commit(repo, "T1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert sorted(git(repo, "show", "--name-only", "--format=", "HEAD").split()) == [
        "src/a.py", "tests/regression/test_probe_deep_1.py"]
    exclude = (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert ".superlite/" in exclude.splitlines()
    assert git(repo, "status", "--porcelain").replace("?? plan.md", "").strip() == ""
    # another task's review fix: regression tests belong to whoever commits them
    write(repo, "src/b.py", "Y = 1\n")
    write(repo, "tests/regression/test_probe_state_2.py", "def test_s():\n    assert True\n")
    assert commit(repo, "T2").returncode == 0
    assert "tests/regression/test_probe_state_2.py" in git(
        repo, "show", "--name-only", "--format=", "HEAD").split()
    # the exclude entry is added once
    exclude = (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert exclude.splitlines().count(".superlite/") == 1


def test_commit_regression_dir_does_not_open_other_paths(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "src/a.py", "X = 1\n")
    write(repo, "tests/regression/test_probe_x.py", "def test_x():\n    assert True\n")
    write(repo, "tests/other.py", "pass\n")
    r = commit(repo, "T1")
    assert r.returncode == 1 and "out-of-scope tests/other.py" in r.stdout
    assert "regression" not in r.stdout


def test_run_tests_writes_summary_packet_can_read(tmp_path):
    import pt_review
    proj = project(tmp_path, "pass", "fail")
    r = run("run_tests", "--full", cwd=proj)
    assert r.returncode == 1, r.stdout + r.stderr
    summary = proj / ".superlite" / "test-summary.json"
    d = json.loads(summary.read_text(encoding="utf-8"))
    assert (d["passed"], d["failed"], d["skipped"], d["mode"]) == (3, 1, 1, "full")
    assert len(d["failures"]) == 1 and d["failures"][0].startswith("test_fail.py::test_broken")
    assert d["skipped_tests"] == ["test_fail.py::test_skipped"]
    text, nfail = pt_review.read_test_summary(summary)
    assert nfail == 1
    assert "passed 3" in text and "failed 1" in text and "skipped 1" in text
    assert "FAILING: test_fail.py::test_broken" in text
    assert "SKIPPED: test_fail.py::test_skipped" in text


def test_run_tests_summary_on_fast_green_and_on_pytest_failure(tmp_path):
    proj = project(tmp_path, "pass")
    summary = proj / ".superlite" / "test-summary.json"
    assert run("run_tests", "--fast", "test_pass.py", cwd=proj).returncode == 0
    d = json.loads(summary.read_text(encoding="utf-8"))
    assert (d["passed"], d["failed"], d["mode"], d["failures"]) == (2, 0, "fast", [])
    assert run("run_tests", "--fast", "missing_dir", cwd=proj).returncode == 2
    d = json.loads(summary.read_text(encoding="utf-8"))
    assert d["failed"] == 1 and "pytest produced no results" in d["failures"][0]
    custom = tmp_path / "elsewhere.json"
    assert run("run_tests", "--summary", custom, cwd=proj).returncode == 0
    assert json.loads(custom.read_text(encoding="utf-8"))["passed"] == 2


def _lint_entry(when, newly, pending, stable=None):
    return {"time": when, "stable": len(stable or newly), "fix": 0, "stable_ids": stable or newly,
            "newly_stable": newly, "dispatched": [], "pending": pending}


def test_finish_reports_unblocked_dispatch(tmp_path):
    (tmp_path / "progress.md").write_text(
        "unblocked: dispatched T2 T4 at 10:00:12; waiting on fixes: T1 T3\n"
        "unblocked: dispatched T1 T3 at 10:03:40; waiting on fixes: none\n", encoding="utf-8")
    (tmp_path / "lint-log.jsonl").write_text("\n".join(json.dumps(e) for e in [
        _lint_entry("2026-10-18T10:00:05", ["T2", "T4"], ["T2", "T4"]),
        _lint_entry("2026-10-18T10:03:30", ["T1", "T3"], ["T1", "T3"], ["T1", "T2", "T3", "T4"])]) + "\n", encoding="utf-8")
    r = run("finish", "--ledger", tmp_path / "progress.md", "--log", tmp_path / "none.jsonl")
    assert "unblocked: yes - every stable task was dispatched before the next patch; max lag 10s" in r.stdout


def test_finish_flags_stable_task_held_across_a_patch(tmp_path):
    (tmp_path / "progress.md").write_text("unblocked: dispatched T2 at 10:00:12; waiting on fixes: T1\n", encoding="utf-8")
    (tmp_path / "lint-log.jsonl").write_text("\n".join(json.dumps(e) for e in [
        _lint_entry("2026-10-18T10:00:05", ["T2", "T4"], ["T2", "T4"]),
        _lint_entry("2026-10-18T10:03:30", ["T1"], ["T4", "T1"], ["T1", "T2", "T4"])]) + "\n", encoding="utf-8")
    r = run("finish", "--ledger", tmp_path / "progress.md", "--log", tmp_path / "none.jsonl")
    assert "unblocked: NO - T4 still waiting at the next lint" in r.stdout


def test_finish_unblocked_without_lint_log(tmp_path):
    r = run("finish", "--ledger", tmp_path / "none.md", "--log", tmp_path / "none.jsonl")
    assert "unblocked: no lint log recorded" in r.stdout


def test_import_check_skips_main_modules_it_would_run(tmp_path, monkeypatch):
    import checkpoint as ct
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "pkg" / "__main__.py").write_text("import sys\nsys.exit(2)\n", encoding="utf-8")  # an unguarded entry point
    (tmp_path / "src" / "pkg" / "bad.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    errs = ct.import_errors(["src/pkg/__main__.py", "src/pkg/bad.py"])
    assert [e[0] for e in errs] == ["src/pkg/bad.py"]  # the real import failure still blocks; __main__ is not run
