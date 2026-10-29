"""Tests for the probe runner (skills/swarm/probes/probe_run.py).

Each test copies a tiny fixture project from tests/scripts/fixtures/probe_*/ into tmp_path and runs
the runner against it as a subprocess, so nothing here touches the real tree.
"""
import ast
import importlib.util
import tempfile
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RUNNER = REPO / "skills" / "swarm" / "probes" / "probe_run.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def make_project(tmp_path, fixture="probe_lib", **patches):
    proj = tmp_path / "proj"
    shutil.copytree(FIXTURES / fixture, proj)
    for rel, pairs in patches.items():
        f = proj / rel.replace("__", "/")
        text = f.read_text()
        for old, new in (pairs if isinstance(pairs, list) else [pairs]):
            assert old in text, old
            text = text.replace(old, new)
        f.write_text(text)
    return proj


def run(proj, *args, env=None, check_exit=None):
    cmd = [sys.executable, str(RUNNER), "--adapter", str(proj / "adapter.py"), "--root", str(proj),
           *map(str, args)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                       env={**os.environ, **(env or {})})
    if check_exit is not None:
        assert p.returncode == check_exit, p.stdout + p.stderr
    out = proj / ".superlite" / "probes" / "probe-results.json"
    return json.loads(out.read_text()) if out.exists() else {"stdout": p.stdout, "stderr": p.stderr}


def incidents(proj):
    log = proj / ".superlite" / "incidents.jsonl"
    if not log.exists():
        return []
    return [json.loads(x) for x in log.read_text().splitlines() if x.strip()]


def pid_alive(pid):
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.OpenProcess.restype = ctypes.c_void_p
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        k32.GetExitCodeProcess(ctypes.c_void_p(h), ctypes.byref(code))
        k32.CloseHandle(ctypes.c_void_p(h))
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def wait_dead(pid, seconds=8):
    end = time.time() + seconds
    while time.time() < end and pid_alive(pid):
        time.sleep(0.1)
    return not pid_alive(pid)


UNGUARDED = {"src__parser.py": ("GUARD = True", "GUARD = False")}


def test_crash_is_reported(tmp_path):
    proj = make_project(tmp_path, **UNGUARDED)
    res = run(proj, "--categories", "deep", "--files", "src/parser.py")
    crashes = [g for g in res["failures"] if g["kind"] == "crash"]
    assert len(crashes) == 1 and crashes[0]["exc"] == "RecursionError"
    assert crashes[0]["file"] == "src/parser.py" and crashes[0]["line"] > 0
    assert res["categories"]["deep"]["status"] == "fail"
    logged = [e for e in incidents(proj) if e["kind"] == "exception"]
    assert len(logged) == 1 and logged[0]["file"] == "src/parser.py"
    assert "RecursionError" in logged[0]["context"] and logged[0].get("resolution") == "open"
    assert not [e for e in incidents(proj) if e["kind"] == "verified"]


def test_hang_is_reported_and_killed(tmp_path):
    proj = make_project(tmp_path)
    pidfile = tmp_path / "spin.pid"
    res = run(proj, "--probes", proj / "probes_hang", "--categories", "hangy", "--files",
              "src/parser.py", env={"PROBE_PIDFILE": str(pidfile)})
    hangs = [g for g in res["failures"] if g["kind"] == "hang"]
    assert len(hangs) == 1
    pid = int(pidfile.read_text())
    assert wait_dead(pid), "the hung probe process is still alive"
    assert res["stats"]["killed_pids"]
    assert [e for e in incidents(proj) if e["kind"] == "perf"]


def test_runs_in_parallel(tmp_path):
    proj = make_project(tmp_path)
    res = run(proj, "--probes", proj / "probes_slow", "--categories", "slow", "--workers", "8",
              "--files", "src/parser.py", check_exit=0)
    assert res["stats"]["probes_run"] == 20
    assert all(p["status"] == "pass" for p in res["probes"])
    assert res["stats"]["run_seconds"] < 5, res["stats"]


def test_pass_logs_verified(tmp_path):
    proj = make_project(tmp_path)
    res = run(proj, "--app-type", "library", check_exit=0)
    assert res["failures"] == []
    assert sorted(res["files"]) == ["src/parser.py", "src/util.py"]
    assert all(c["status"] == "pass" for c in res["categories"].values()), res["categories"]
    verified = [e for e in incidents(proj) if e["kind"] == "verified"]
    pairs = sorted((re.match(r"probe (\w+)", e["context"]).group(1), e["file"]) for e in verified)
    expect = sorted((c, f) for c in ("deep", "numeric", "encoding", "state")
                    for f in ("src/parser.py", "src/util.py"))
    assert pairs == expect


def test_skips_verified_categories(tmp_path):
    proj = make_project(tmp_path)
    log = proj / ".superlite" / "incidents.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(json.dumps({"file": "src/parser.py", "line": 0, "kind": "verified",
                               "context": "probe deep: nesting reviewed by hand",
                               "agent": "reviewer"}) + "\n")
    res = run(proj, "--categories", "deep", "--files", "src/parser.py")
    assert res["categories"]["deep"]["status"] == "skipped"
    assert res["stats"]["probes_run"] == 0
    res = run(proj, "--categories", "deep", "--files", "src/parser.py,src/util.py")
    assert res["categories"]["deep"]["files"] == ["src/util.py"]
    assert res["stats"]["probes_run"] > 0


def test_only_kills_own_pids(tmp_path):
    sentinel = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        proj = make_project(tmp_path)
        res = run(proj, "--probes", proj / "probes_hang", "--categories", "hangy", "--files",
                  "src/parser.py", env={"PROBE_PIDFILE": str(tmp_path / "spin.pid")})
        assert res["stats"]["killed_pids"], "the run should have killed its own hung worker"
        assert sentinel.pid not in res["stats"]["killed_pids"]
        assert sentinel.poll() is None and pid_alive(sentinel.pid)
    finally:
        sentinel.kill()
        sentinel.wait()


def test_server_adapter_uses_free_port(tmp_path):
    proj = make_project(tmp_path, "probe_server")
    res = run(proj, "--categories", "protocol", "--files", "src/fixture_site.py")
    trace = next(p for p in res["probes"] if p["id"] == "protocol/request_unknown_method_TRACE")
    assert trace["status"] == "pass" and trace["detail"] == "status 501"
    assert any(p["entry"] == "raw" for p in res["probes"])
    assert res["failures"] == [], res["failures"]
    ports = res["stats"]["server_ports"]
    assert ports and all(isinstance(p, int) and p > 0 and p != 8000 for p in ports)
    assert res["stats"]["server_starts"] >= 1


def test_category_selection_by_app_type(tmp_path):
    proj = make_project(tmp_path)
    res = run(proj, "--app-type", "cli", "--files", "src/parser.py")
    assert sorted(res["selected_categories"]) == ["encoding", "file", "protocol", "state"]
    assert set(res["categories"]) == {"encoding", "file", "protocol", "state"}
    assert "deep" not in res["categories"] and "numeric" not in res["categories"]
    bad = subprocess.run([sys.executable, str(RUNNER), "--adapter", str(proj / "adapter.py"),
                          "--root", str(proj), "--app-type", "toaster"], capture_output=True, text=True)
    assert bad.returncode == 2


def test_groups_by_root_cause(tmp_path):
    proj = make_project(tmp_path, **UNGUARDED)
    res = run(proj, "--categories", "deep", "--probes", proj / "probes_many", "--files",
              "src/parser.py")
    assert sum(p["status"] == "crash" for p in res["probes"]) >= 30
    assert len(res["failures"]) == 1 and res["failures"][0]["count"] >= 30
    assert len([e for e in incidents(proj) if e["kind"] == "exception"]) == 1


def test_shrinks_and_writes_regression_test(tmp_path):
    proj = make_project(tmp_path, **{"src__parser.py": [("GUARD = True", "GUARD = False"),
                                                      ("LIMIT = None", "LIMIT = 300")]})
    res = run(proj, "--categories", "deep", "--files", "src/parser.py")
    assert len(res["failures"]) == 1, res["failures"]
    g = res["failures"][0]
    assert g["exc"] == "_Boom" and g["regression_test"] == "tests/regression/test_probe_deep_1.py"
    test = proj / g["regression_test"]
    text = test.read_text()
    inp = ast.literal_eval(re.search(r"^INPUT = (.*)$", text, re.M).group(1))
    assert 300 <= inp.count("(") <= 600, inp.count("(")
    pytest_cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(test)]
    assert subprocess.run(pytest_cmd, cwd=proj, capture_output=True).returncode != 0
    parser = proj / "src" / "parser.py"
    parser.write_text(parser.read_text().replace("GUARD = False", "GUARD = True").replace("LIMIT = 300", "LIMIT = None"))
    # False->True and 300->None keep the same byte length. The first pytest wrote a pyc in
    # this same second, and Python reuses it when the size matches, so the rerun still
    # executes LIMIT = 300 and raises _Boom.
    shutil.rmtree(proj / "src" / "__pycache__", ignore_errors=True)
    after = subprocess.run(pytest_cmd, cwd=proj, capture_output=True, text=True)
    assert after.returncode == 0, after.stdout + after.stderr


def test_shared_server_restarts_only_on_crash(tmp_path):
    proj = make_project(tmp_path, "probe_server")
    res = run(proj, "--categories", "http", "--probes", proj / "probes_http", "--workers", "1",
              "--files", "src/fixture_site.py", check_exit=0)
    assert res["stats"]["probes_run"] == 20 and res["stats"]["server_starts"] == 1

    proj2 = tmp_path / "second"
    proj2.mkdir()
    proj2 = make_project(proj2, "probe_server")
    res = run(proj2, "--categories", "http", "--probes", proj2 / "probes_crash", "--workers", "1",
              "--files", "src/fixture_site.py")
    assert res["stats"]["probes_run"] == 20 and res["stats"]["server_starts"] == 2
    assert len(res["failures"]) == 1 and res["failures"][0]["exc"] == "ServerDied"


def test_incremental_skips_unchanged(tmp_path):
    proj = make_project(tmp_path)
    args = ("--categories", "deep,numeric", "--files", "src/parser.py")
    first = run(proj, *args, check_exit=0)
    assert first["stats"]["probes_run"] > 0
    assert (proj / ".superlite" / "probes" / "cache.json").exists()
    (proj / ".superlite" / "probes" / "probe-results.json").unlink()
    second = run(proj, *args, check_exit=0)
    assert second["stats"]["probes_run"] == 0
    assert all(c["status"] == "pass" and c["cached"] for c in second["categories"].values())
    parser = proj / "src" / "parser.py"
    parser.write_text(parser.read_text() + "\n# changed\n")
    third = run(proj, *args, check_exit=0)
    assert third["stats"]["probes_run"] > 0


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_commit_mode_probes_only_touched_files(tmp_path):
    proj = make_project(tmp_path)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", str(proj)]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "first"], check=True)
    util = proj / "src" / "util.py"
    util.write_text(util.read_text() + "\n\ndef triple(n):\n    return n * 3\n")
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "second"], check=True)
    res = run(proj, "--commit", "HEAD", "--categories", "deep", check_exit=0)
    assert res["files"] == ["src/util.py"]
    assert res["categories"]["deep"]["files"] == ["src/util.py"]
    assert [e["file"] for e in incidents(proj) if e["kind"] == "verified"] == ["src/util.py"]


def test_category_timeouts(tmp_path):
    proj = make_project(tmp_path)
    res = run(proj, "--probes", proj / "probes_tmo", "--categories", "tmo", "--files", "src/parser.py")
    stall = next(p for p in res["probes"] if p["id"] == "tmo/stall")
    assert stall["status"] == "hang" and stall["timeout"] == 2.0
    assert stall["elapsed"] < 3.0


def test_background_mode_returns_at_once(tmp_path):
    proj = make_project(tmp_path)
    p = subprocess.run([sys.executable, str(RUNNER), "--adapter", str(proj / "adapter.py"),
                        "--root", str(proj), "--categories", "state", "--files", "src/parser.py",
                        "--background"], capture_output=True, text=True, timeout=30)
    assert p.returncode == 0 and "started" in json.loads(p.stdout)
    out = proj / ".superlite" / "probes" / "probe-results.json"
    end = time.time() + 90
    while time.time() < end and not out.exists():
        time.sleep(0.3)
    assert out.exists()


@pytest.mark.skipif(shutil.which("git") is None or shutil.which("bash") is None,
                    reason="git or bash not installed")
def test_commit_mode_via_sl_launcher_and_default_paths(tmp_path):
    """`bash sl probe_run --checkpoint SHA` from the repo root, default adapter and --out paths."""
    proj = make_project(tmp_path)
    (proj / ".superlite" / "probes").mkdir(parents=True)
    shutil.move(str(proj / "adapter.py"), str(proj / ".superlite" / "probes" / "adapter.py"))
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", str(proj)]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "first"], check=True)
    parser = proj / "src" / "parser.py"
    parser.write_text(parser.read_text() + "\n# touched\n")
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "second"], check=True)
    sl = REPO / "skills" / "swarm" / "scripts" / "sl"
    p = subprocess.run(["bash", str(sl), "probe_run", "--checkpoint", "HEAD", "--categories", "state"],
                       cwd=proj, capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, p.stdout + p.stderr
    res = json.loads((proj / ".superlite" / "probes" / "probe-results.json").read_text())
    assert res["files"] == ["src/parser.py"]
    assert {"category", "file", "line", "status", "group_id", "input_size", "message"} <= set(res["probes"][0])


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_commit_mode_probes_the_commit_not_the_working_tree(tmp_path):
    """HEAD has the guarded parser; the working tree has an unguarded, uncommitted one. Probing
    HEAD must pass, and the temp checkout must be gone afterwards."""
    proj = make_project(tmp_path)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", str(proj)]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "good"], check=True)
    parser = proj / "src" / "parser.py"
    parser.write_text(parser.read_text().replace("GUARD = True", "GUARD = False"))
    res = run(proj, "--checkpoint", "HEAD", "--categories", "deep", "--files", "src/parser.py")
    assert res["categories"]["deep"]["status"] == "pass" and not res["failures"], res
    assert res["commit"] == "HEAD"
    wt = subprocess.run([*git, "worktree", "list", "--porcelain"], capture_output=True, text=True).stdout
    assert wt.count("worktree ") == 1
    assert not list(Path(tempfile.gettempdir()).glob("probe-commit-*/tree/.git"))
    # Committing the unguarded parser makes --checkpoint HEAD fail, with the incident under the real root.
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "bad"], check=True)
    res = run(proj, "--checkpoint", "HEAD", "--categories", "deep")
    assert [g["kind"] for g in res["failures"]] == ["crash"] and res["failures"][0]["file"] == "src/parser.py"
    assert any(e["kind"] == "exception" for e in incidents(proj))
    assert (proj / "tests" / "regression").is_dir()


def test_checkout_commit_archive_fallback(tmp_path):
    """Outside a worktree-capable repo state (no worktree support), git archive still works."""
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    spec = importlib.util.spec_from_file_location("probe_run_mod", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    proj = make_project(tmp_path)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", str(proj)]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "x"], check=True)
    real_run = subprocess.run

    def no_worktree(cmd, *a, **k):
        if "worktree" in cmd:
            return subprocess.CompletedProcess(cmd, 1, b"", b"no")
        return real_run(cmd, *a, **k)
    mod.subprocess.run = no_worktree
    try:
        tree, repo = mod.checkout_commit(proj, "HEAD")
    finally:
        mod.subprocess.run = real_run
    assert repo is None and (tree / "src" / "parser.py").is_file()
    mod.remove_checkout(tree, repo)
    assert not tree.exists()


PT_REVIEW = REPO / "skills" / "swarm" / "scripts" / "pt_review.py"


def generated_server_project(tmp_path):
    """A minimal server project (the probe_server fixture's package, no hand-written adapter) with a generated adapter."""
    proj = tmp_path / "gen"
    shutil.copytree(FIXTURES / "probe_server", proj)
    (proj / "adapter.py").unlink()
    plan = tmp_path / "plan.md"
    plan.write_text("# Plan\n\n**Probe entry:** serve=fixture_site.create_server()\n", encoding="utf-8")
    cp = subprocess.run([sys.executable, str(PT_REVIEW), "adapter", "--plan", str(plan), "--root", str(proj),
                         "--out", str(proj / "adapter.py")], capture_output=True, text=True)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    return proj


def test_generated_server_adapter_has_start_server_and_passes_the_probes(tmp_path):
    proj = generated_server_project(tmp_path)
    src = (proj / "adapter.py").read_text(encoding="utf-8")
    assert "def start_server(" in src and "def request(" in src
    res = run(proj, "--categories", "protocol", "--files", "src/fixture_site.py", check_exit=0)
    assert res["failures"] == [], res["failures"]
    assert not [e for e in incidents(proj) if e["kind"] == "exception"], incidents(proj)  # w3: 90 of 116 came from a missing start_server
    assert res["stats"]["server_starts"] >= 1 and res["stats"]["server_ports"]


def test_generated_server_adapter_regression_tests_run_against_the_adapter(tmp_path):
    proj = generated_server_project(tmp_path)
    res = run(proj, "--categories", "http", "--probes", proj / "probes_crash", "--workers", "1", "--files", "src/fixture_site.py")
    assert res["failures"], "the crash probe should produce a failure group"
    tests = res["failures"][0].get("regression_test")
    assert tests, res["failures"][0]
    out = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(proj / tests)],
                         cwd=proj, capture_output=True, text=True)
    assert "start_server" not in out.stdout + out.stderr and "AttributeError" not in out.stdout + out.stderr, out.stdout[-800:]


def test_session_defaults_are_absolute_and_background_keeps_custom_out(tmp_path, monkeypatch):
    """Remapped --out/--log stay absolute under --root; custom --out is not dropped on --background."""
    spec = importlib.util.spec_from_file_location("probe_run_remap", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    scripts = REPO / "skills" / "swarm" / "scripts"
    sys.path.insert(0, str(scripts))
    import session as sl_session

    monkeypatch.chdir(tmp_path)
    child = tmp_path / "child"
    child.mkdir()
    sid, sd = sl_session.start(child, host_session="probe-sess")
    monkeypatch.setenv("SUPERLITE_SESSION", sid)
    captured = {}

    def fake_spawn(argv):
        captured["argv"] = list(argv)
        return 0

    monkeypatch.setattr(mod, "spawn_background", fake_spawn)
    # From parent cwd with --root child: absolute session paths must not become child/child/...
    rc = mod.main(["--root", str(child), "--background", "--categories", "state"])
    assert rc == 0
    argv = captured["argv"]
    assert "--out" in argv and "--log" in argv
    out = argv[argv.index("--out") + 1]
    log = argv[argv.index("--log") + 1]
    assert Path(out).is_absolute() and str(sd / "probes" / "probe-results.json") == out
    assert Path(log).is_absolute() and str(sd / "incidents.jsonl") == log
    assert f"{child.name}/{child.name}" not in out

    captured.clear()
    custom = str(tmp_path / "custom.json")
    rc = mod.main(["--root", str(child), "--background", "--out", custom, "--categories", "state"])
    assert rc == 0
    argv = captured["argv"]
    # Custom --out kept; remapped defaults appended last (argparse last-wins for --out/--log).
    assert argv.count("--out") >= 1
    last_out = argv[len(argv) - 1 - argv[::-1].index("--out") + 1]
    assert last_out == custom
    # Never leave the path as a bare argv token without its flag (old strip bug).
    for i, x in enumerate(argv):
        if x == custom:
            assert i > 0 and argv[i - 1] == "--out"
