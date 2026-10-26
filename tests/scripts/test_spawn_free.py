"""Instantiation optimizations: in-process taskgraph and incidents, dispatch, multi-task
checkpoint, and the sl interpreter cache. These run the real sibling tools (no fake sl);
SUPERLITE_BASH points at a missing binary so any leftover `bash sl` spawn fails loudly."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parents[1] / "skills" / "swarm" / "scripts"
PLAN = HERE / "fixtures" / "run_plan" / "plan.md"

ENV = {k: v for k, v in os.environ.items() if k not in ("SUPERLITE_SL", "SUPERLITE_PYTHON")}
ENV["SUPERLITE_BASH"] = str(HERE / "no-such-bash")  # a spawn through sl would fail
ENV["SUPERLITE_PROBE_RUN"] = str(HERE / "no_such_probe.py")
ENV["SUPERLITE_AUTOCOMMIT"] = "1"  # these tests assert real commits


def run(script, *args, cwd=None):
    return subprocess.run([sys.executable, str(SCRIPTS / f"{script}.py"), *map(str, args)],
                          capture_output=True, text=True, cwd=cwd, env=ENV, encoding="utf-8")


def read_log(path):
    p = Path(path)
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


# ------------------------------------------------------------- in-process taskgraph

def test_next_tasks_in_process_matches_launcher_route():
    r = run("next_tasks", "--plan", PLAN)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["T2", "T5", "T4", "T1"]
    r = run("next_tasks", "--plan", PLAN, "--committed", "T1,T2")
    assert r.stdout.split() == ["T5", "T3", "T4"]


def test_taskgraph_full_is_memoized_and_has_seams():
    sys.path.insert(0, str(SCRIPTS))
    import next_tasks as nt
    a = nt.taskgraph_full(PLAN)
    assert a is nt.taskgraph_full(PLAN)  # one parse per process
    assert a["graph"]["T3"] == ["T1", "T2"]
    assert sorted(a["seams"]) == ["T1↔T3", "T2↔T3", "T3↔T6"]


def test_bad_taskgraph_dies_with_plan_tools_message(tmp_path):
    bad = tmp_path / "plan.md"
    bad.write_text(PLAN.read_text(encoding="utf-8").replace("T3 --> T6", "T3 --> T9"),
                   encoding="utf-8")
    r = run("next_tasks", "--plan", bad)
    assert r.returncode == 2
    assert "taskgraph failed" in r.stderr and "T9" in r.stderr


# ------------------------------------------------------------- incidents in one write

def test_incident_append_batches_and_validates(tmp_path):
    sys.path.insert(0, str(SCRIPTS))
    import incident
    log = tmp_path / "i.jsonl"
    incident.append(log, [incident.make_entry("a.py", 1, "x" * 300, agent="t", kind="flaky"),
                          incident.make_entry("b.py", 0, "ok", kind="verified")])
    rows = read_log(log)
    assert [r["file"] for r in rows] == ["a.py", "b.py"]
    assert len(rows[0]["context"]) == 200 and rows[0]["context"].endswith("…")
    with pytest.raises(ValueError):
        incident.make_entry("a.py", 1, "c", kind="nope")
    with pytest.raises(ValueError):
        incident.make_entry("a.py", -1, "c")


# ------------------------------------------------------------- dispatch

def pool(tmp_path, agents):
    p = tmp_path / "pool.json"
    p.write_text(json.dumps({"plan": str(PLAN), "agents": agents}), encoding="utf-8")
    return p


def dispatch(tmp_path, agents, *extra, ledger=None):
    led = ledger or tmp_path / "progress.md"
    r = run("dispatch", "--pool", pool(tmp_path, agents), "--ledger", led, "--batch", "1", *extra)  # one task per line
    assert r.returncode == 0, r.stderr
    return [[x, d.split(" tier=")[0]] for x, d in (ln.split(" ", 1) for ln in r.stdout.splitlines())], r.stderr


def test_dispatch_never_gives_one_agent_two_tasks(tmp_path):
    # a1 read src/b.py (T2, +3 file +1 pkg) and pkg/f.py (T5, +3 +1): it wins T2 first
    # (longest), then T5 must not go to a1 again.
    a1 = {"id": "a1", "tasks": [], "files": ["src/b.py", "pkg/f.py"]}
    lines, _ = dispatch(tmp_path, [a1])
    assert lines[0] == ["T2", "agent=a1"]
    assert dict(lines)["T5"] == "fresh"


def test_dispatch_poll_lists_candidates_and_evicts(tmp_path):
    b1 = {"id": "b1", "tasks": ["T1"], "context_k": 20}
    b2 = {"id": "b2", "tasks": ["T2"], "context_k": 20}
    old = {"id": "old", "tasks": [], "idle_min": 99}
    busy = {"id": "busy", "tasks": [], "busy": True, "current": "T4"}
    lines, err = dispatch(tmp_path, [b1, b2, old, busy], "--committed", "T1,T2")
    d = dict(lines)
    assert "T4" not in d  # running per the busy agent's `current`
    assert d["T3"] == "poll b1,b2"  # both built a dependency
    assert "evict=old idle over 50 min" in err and "evict=busy" not in err


def test_dispatch_reads_committed_from_ledger(tmp_path):
    led = tmp_path / "progress.md"
    led.write_text("# Progress ledger\n\n- commit T1 abcdef1234 parser\n"
                   "- commit T2 1234abcdef lexer\n", encoding="utf-8")
    lines, _ = dispatch(tmp_path, [], ledger=led)
    assert [t for t, _ in lines] == ["T5", "T3", "T4"]
    assert all(d == "fresh" for _, d in lines)


# -------------------------------------------------------------- multi-task checkpoint

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
    shutil.copy(PLAN, repo / "plan.md")
    (repo / "README.md").write_text("base\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    return repo


def write(repo, rel, text):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_commit_several_tasks_in_one_call_then_dispatch(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "src/a.py", "def parse(text):\n    return text.split()\n")
    write(repo, "src/b.py", "def lex(text):\n    return list(text)\n")
    r = run("checkpoint", "T1", "T2", "--plan", "plan.md", "--dispatch", "--batch", "1", cwd=repo)
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout.splitlines()
    assert out[0].startswith("T1 ") and out[1].startswith("T2 ")
    assert out[2] == "next:"
    assert [ln.split()[0] for ln in out[3:]] == ["T5", "T3", "T4"]
    assert git(repo, "log", "--format=%s", "-2").splitlines() == ["T2: lexer", "T1: parser"]
    assert git(repo, "show", "--name-only", "--format=", "HEAD~1") == "src/a.py"


def test_commit_several_reports_each_failure_and_logs_in_process(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "src/a.py", "def parse(text):\n    return text.split()\n")
    write(repo, "src/c.py", "def broken(:\n")
    r = run("checkpoint", "T1", "T3", "--plan", "plan.md", cwd=repo)
    assert r.returncode == 1
    assert r.stdout.splitlines()[0].startswith("T1 ")
    assert "T3 FAILED" in r.stdout
    rows = read_log(repo / ".superlite" / "incidents.jsonl")
    assert rows and rows[0]["file"] == "src/c.py" and rows[0]["agent"] == "checkpoint"
    assert "time" in rows[0]  # written by the real incident module, not a spawn


def test_single_task_output_unchanged(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "src/a.py", "def parse(text):\n    return text.split()\n")
    r = run("checkpoint", "T1", "--plan", "plan.md", cwd=repo)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == git(repo, "rev-parse", "HEAD")


# ------------------------------------------------------------- sl interpreter cache

def test_sl_caches_interpreter_and_recovers_from_stale_cache(tmp_path):
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not available")
    env = {**ENV, "SUPERLITE_CACHE_DIR": str(tmp_path / "cache")}
    env.pop("SUPERLITE_BASH")
    cmd = [bash, str(SCRIPTS / "sl"), "incident", "summary", "--log", str(tmp_path / "i.jsonl")]
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if r.returncode == 3:
        pytest.skip("no Python reachable from bash")
    assert r.returncode == 0, r.stderr
    cache = tmp_path / "cache" / "python"
    first = cache.read_text().strip()
    assert first
    cache.write_text("/no/such/python\n")
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert cache.read_text().strip() == first


def test_sl_trusts_superlite_python(tmp_path):
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not available")
    env = {**ENV, "SUPERLITE_CACHE_DIR": str(tmp_path / "cache"),
           "SUPERLITE_PYTHON": Path(sys.executable).as_posix()}
    env.pop("SUPERLITE_BASH")
    r = subprocess.run([bash, str(SCRIPTS / "sl"), "incident", "summary", "--log",
                        str(tmp_path / "i.jsonl")], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert not (tmp_path / "cache" / "python").exists()  # no probe, nothing cached


# ------------------------------------------------------------- review fixes

def test_dispatch_keeps_failed_task_running_and_its_agent_off_the_list(tmp_path):
    # a1 reported T2 FAILED: busy is false, but `current` stays until T2 is committed
    a1 = {"id": "a1", "tasks": [], "files": ["src/b.py"], "current": "T2"}
    a2 = {"id": "a2", "tasks": [], "files": ["pkg/f.py"]}
    lines, _ = dispatch(tmp_path, [a1, a2])
    d = dict(lines)
    assert "T2" not in d  # still running, not re-dispatched
    assert d["T5"] == "agent=a2"
    assert all("a1" not in v for v in d.values())


def test_dispatch_reserves_polled_agents(tmp_path):
    # T2 (longest) ties b1/b2 on the package point and polls both; T5 must not reuse them
    b1 = {"id": "b1", "tasks": [], "files": ["src/x.py"]}
    b2 = {"id": "b2", "tasks": [], "files": ["src/y.py"]}
    lines, _ = dispatch(tmp_path, [b1, b2])
    d = dict(lines)
    assert d["T2"] == "poll b1,b2"
    assert d["T5"] == "fresh"


def test_commit_releases_pool_agent_and_dispatch_failure_keeps_commit(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "src/a.py", "def parse(text):\n    return text.split()\n")
    pool_path = repo / ".superlite" / "pool.json"
    pool_path.parent.mkdir(parents=True, exist_ok=True)
    pool_path.write_text(json.dumps({"agents": [
        {"id": "a1", "tasks": [], "busy": True, "current": "T1"}]}), encoding="utf-8")
    r = run("checkpoint", "T1", "--plan", "plan.md", cwd=repo)
    assert r.returncode == 0, r.stderr
    ag = json.loads(pool_path.read_text(encoding="utf-8"))["agents"][0]
    assert ag["busy"] is False and "current" not in ag and ag["tasks"] == ["T1"]
    # a broken pool after the commit: dispatch warns, the commit's exit code stands
    write(repo, "src/b.py", "def lex(text):\n    return list(text)\n")
    pool_path.write_text("{not json", encoding="utf-8")
    r = run("checkpoint", "T2", "--plan", "plan.md", "--dispatch", "--batch", "1", cwd=repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "warning: dispatch failed" in r.stderr and "next:" in r.stdout
    assert git(repo, "log", "--format=%s", "-1") == "T2: lexer"


def test_add_incidents_skips_only_the_bad_item(tmp_path, capsys):
    sys.path.insert(0, str(SCRIPTS))
    import next_tasks as nt
    log = tmp_path / "i.jsonl"
    old = os.environ.pop("SUPERLITE_SL", None)
    try:
        nt.add_incidents(log, [("a.py", 1, "ok", "test-failure", "run_tests", "open"),
                               ("", 2, "no file", "test-failure", "run_tests", "open"),
                               ("c.py", None, "no line", "test-failure", "run_tests", None),
                               ("d.py", 0, "fine", "verified", "run_tests", None)])
    finally:
        if old is not None:
            os.environ["SUPERLITE_SL"] = old
    assert [r["file"] for r in read_log(log)] == ["a.py", "d.py"]
    assert capsys.readouterr().err.count("warning: incident not logged") == 2


def _sl(tmp_path, env_extra, *args):
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not available")
    env = {**ENV, "SUPERLITE_CACHE_DIR": str(tmp_path / "cache"), **env_extra}
    env.pop("SUPERLITE_BASH")
    return subprocess.run([bash, str(SCRIPTS / "sl"), *args], capture_output=True, text=True, env=env)


def test_sl_unrunnable_superlite_python_falls_back_to_search(tmp_path):
    r = _sl(tmp_path, {"SUPERLITE_PYTHON": str(tmp_path / "no such python.exe")},
            "incident", "summary", "--log", str(tmp_path / "i.jsonl"))
    if r.returncode == 3:
        pytest.skip("no Python reachable from bash")
    assert r.returncode == 0, r.stderr
    assert "not runnable; searching" in r.stderr


def test_sl_cache_is_keyed_on_path(tmp_path):
    log = str(tmp_path / "i.jsonl")
    r = _sl(tmp_path, {}, "incident", "summary", "--log", log)
    if r.returncode == 3:
        pytest.skip("no Python reachable from bash")
    cache = tmp_path / "cache" / "python"
    exe, key = cache.read_text().splitlines()
    assert "|" in key
    # same interpreter, a different PATH: the old entry no longer matches, a new probe rewrites it
    r = _sl(tmp_path, {"PATH": os.environ["PATH"] + os.pathsep + str(tmp_path)},
            "incident", "summary", "--log", log)
    assert r.returncode == 0, r.stderr
    exe2, key2 = cache.read_text().splitlines()
    assert key2 != key and exe2


def test_sl_tool_without_arguments(tmp_path):
    r = _sl(tmp_path, {}, "graphify_gate")
    if r.returncode == 3:
        pytest.skip("no Python reachable from bash")
    assert "unbound variable" not in r.stderr
