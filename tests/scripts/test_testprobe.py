"""Hung test run probe: classification, start threshold, additive summary fields, degradation, and real
hanging processes started through run_tests (thresholds shrunk through SUPERLITE_TESTPROBE_CONFIG)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "subagent-driven-development" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import run_tests  # noqa: E402
import testprobe as tp  # noqa: E402

RUN_TESTS = SCRIPTS / "run_tests.py"
CFG = dict(tp.DEFAULTS)


def S(t, out=100, cpu=1.0, pids=(1, 2), method="fake"):
    return tp.Sample(t, out, cpu, None if pids is None else frozenset(pids), {p: f"p{p}" for p in (pids or ())}, method)


# ------------------------------------------------------------------ classification (fake samples)

def test_flat_output_and_flat_cpu_is_blocked():
    assert tp.classify(S(0, cpu=5.0), S(12, cpu=5.05), CFG) == "blocked"
    assert tp.judge(S(0, cpu=5.0), S(12, cpu=5.0), CFG) == ("blocked", 0.0)


def test_flat_output_and_busy_cpu_is_spinning():
    kind, pct = tp.judge(S(0, cpu=5.0), S(12, cpu=16.0), CFG)
    assert kind == "spinning" and 90 < pct < 93


def test_everything_else_keeps_waiting():
    assert tp.classify(S(0, out=10), S(12, out=11, cpu=1.0), CFG) == "waiting"      # output moved
    assert tp.classify(S(0, cpu=1.0), S(12, cpu=2.0), CFG) == "waiting"             # ~8% CPU: neither idle nor busy
    assert tp.classify(S(0, pids=(1, 2)), S(12, pids=(1, 2, 3)), CFG) == "waiting"  # a child appeared
    assert tp.classify(S(0, pids=(1, 2)), S(12, pids=(1,)), CFG) == "waiting"       # a child exited
    assert tp.classify(S(5), S(5), CFG) == "waiting"                                # no time passed


def test_unknown_cpu_is_stalled_not_an_error():
    assert tp.classify(S(0, cpu=None), S(12, cpu=None), CFG) == "stalled"
    assert tp.classify(S(0, cpu=1.0), S(12, cpu=None), CFG) == "stalled"
    assert tp.classify(S(0, pids=None, cpu=None), S(12, pids=None, cpu=None), CFG) == "stalled"


def test_probe_reports_once_after_min_flat_and_notes_recovery():
    cfg = dict(CFG, min_flat_s=30)
    p = tp.Probe(cfg, "tests/markdown.test.ts")
    got = [p.feed(S(t, cpu=5.0 + t * 0.9)) for t in (0, 12, 24, 36, 48)]   # spinning at 90% of a core
    assert got[:3] == [None, None, None]                                   # flat for 24 s only
    info = got[3]
    assert info["verdict"] == "spinning" and info["flat_s"] == 36.0 and info["test_file"] == "tests/markdown.test.ts"
    assert info["tree"] == [{"pid": 1, "name": "p1"}, {"pid": 2, "name": "p2"}] and info["recovered"] is False
    assert got[4] is None and p.suspect["flat_s"] == 48.0                  # reported once, kept up to date
    assert p.feed(S(60, out=999, cpu=60.0)) is None and p.suspect["recovered"] is True


def test_probe_flat_streak_resets_when_the_run_moves():
    p = tp.Probe(dict(CFG, min_flat_s=30), None)
    outs = [(0, 1), (12, 1), (24, 1), (36, 2), (48, 2), (60, 2)]
    assert [p.feed(S(t, out=o, cpu=1.0)) for t, o in outs] == [None] * 6   # 24 s flat, a write, then 24 s flat


def test_auto_kill_needs_blocked_for_long_enough_and_is_off_by_default():
    flat = [S(t, cpu=1.0) for t in range(0, 400, 12)]
    off, on = tp.Probe(CFG, None), tp.Probe(dict(CFG, auto_kill_blocked=True, auto_kill_after_s=300), None)
    due = []
    for s in flat:
        off.feed(s), on.feed(s)
        due.append((off.autokill_due(), on.autokill_due()))
    assert not any(a for a, _ in due)
    assert not due[10][1] and due[-1][1]
    spin = tp.Probe(dict(CFG, auto_kill_blocked=True, auto_kill_after_s=1), None)
    for t in range(0, 100, 12):
        spin.feed(S(t, cpu=t * 0.9))
    assert spin.suspect["verdict"] == "spinning" and not spin.autokill_due()   # only BLOCKED may be killed


def test_probing_starts_after_three_times_the_expected_time():
    assert tp.start_after(CFG, 100) == 300 and tp.start_after(CFG, 20) == 60
    assert tp.start_after(CFG, None) == 180 and tp.start_after(CFG, 0) == 180   # fallback_expected_s 60
    assert tp.start_after(dict(CFG, min_start_s=500), 10) == 500
    assert tp.start_after(dict(CFG, multiplier=2, fallback_expected_s=10, min_start_s=0), None) == 20


def sleeper(seconds):
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


def test_run_watched_samples_only_after_the_start_threshold():
    calls = []

    def sampler(pid, paths):
        calls.append(time.monotonic())
        return tp.take_sample(pid, paths)

    cfg = dict(CFG, min_start_s=0, sample_interval_s=0.2)
    r = tp.run_watched(sleeper(0.6), cfg=cfg, expected_s=1.0, sampler=sampler)   # starts at 3 s: never
    assert r.returncode == 0 and calls == [] and r.probe.suspect is None and r.killed is None
    r = tp.run_watched(sleeper(1.0), cfg=cfg, expected_s=0.1, sampler=sampler)   # starts at 0.3 s
    assert len(calls) >= 2 and calls[0] > 0
    before = len(calls)
    r = tp.run_watched(sleeper(0.5), cfg=dict(cfg, enabled=False), expected_s=0.01, sampler=sampler)
    assert r.returncode == 0 and len(calls) == before   # disabled: no sampling at all


def test_run_watched_captures_output_and_exit_code():
    r = tp.run_watched([sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"],
                       cfg=dict(CFG, enabled=False))
    assert (r.returncode, r.stdout.strip(), r.stderr.strip()) == (3, "out", "err")


# ------------------------------------------------------------------ wording and additive fields

def test_exit_messages_are_never_bare_codes():
    assert tp.exit_message(4294967295) == "process killed or crashed (exit -1)"
    assert tp.exit_message(-1) == "process killed or crashed (exit -1)"
    assert tp.exit_message(-9) == "process killed by signal 9"
    assert tp.exit_message(0xC0000005) == "process crashed (exit 0xC0000005)"
    assert tp.exit_message(3) == "exit code 3"


def fake_result(suspect=None, killed=None, elapsed=9.0):
    probe = types.SimpleNamespace(suspect=suspect, cfg=CFG)
    return types.SimpleNamespace(probe=probe, killed=killed, elapsed=elapsed, returncode=1)


SUSPECT = {"verdict": "spinning", "flat_s": 42.0, "cpu_pct": 97.0, "cpu_method": "ctypes GetProcessTimes",
           "test_file": "tests/markdown.test.ts", "recovered": False,
           "tree": [{"pid": 10, "name": "node.exe"}, {"pid": 11, "name": "node.exe"}]}


def test_summary_fields_are_additive_and_empty_for_a_quiet_run():
    assert tp.summary_fields(fake_result()) == {}
    f = tp.summary_fields(fake_result(dict(SUSPECT)))
    assert f["verdict"] == "SUSPECT HANG" and f["suspect_hang"]["verdict"] == "spinning"
    assert f["suspect_hang"]["flat_s"] == 42.0 and f["suspect_hang"]["test_file"] == "tests/markdown.test.ts"
    assert f["suspect_hang"]["line"].startswith("SUSPECT HANG: SPINNING tests/markdown.test.ts")
    assert "node.exe(10)" in f["suspect_hang"]["line"] and "infinite loop" in f["suspect_hang"]["line"]
    t = tp.summary_fields(fake_result(None, "timeout"))
    assert t["verdict"] == "TIMEOUT" and t["timeout_s"] == 9.0 and "suspect_hang" not in t
    both = tp.summary_fields(fake_result(dict(SUSPECT), "timeout"))
    assert both["verdict"] == "SUSPECT HANG" and both["timeout_s"] == 9.0
    assert tp.hang_text(fake_result(None, "timeout")).startswith("TIMEOUT:")
    assert tp.hang_text(fake_result()) == ""


def test_write_summary_keeps_the_old_shape_without_extra(tmp_path):
    counts = {"passed": 1, "failed": 0, "skipped": 0, "skipped_tests": []}
    run_tests.write_summary(tmp_path / "a.json", "fast", counts, [], 1.0)
    old = json.loads((tmp_path / "a.json").read_text(encoding="utf-8"))
    assert set(old) == {"passed", "failed", "skipped", "seconds", "mode", "failures", "skipped_tests"}
    run_tests.write_summary(tmp_path / "b.json", "fast", counts, [], 1.0, extra=tp.summary_fields(fake_result(dict(SUSPECT))))
    new = json.loads((tmp_path / "b.json").read_text(encoding="utf-8"))
    assert set(old) < set(new) and all(new[k] == old[k] for k in old)


def test_expected_seconds_sources(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPERLITE_TEST_EXPECT_S", raising=False)
    summ = tmp_path / "s.json"
    a = types.SimpleNamespace(expect=None, publish=None, summary=str(summ))
    assert run_tests.expected_seconds(a, "full") is None
    summ.write_text(json.dumps({"mode": "full", "passed": 5, "failed": 0, "seconds": 12.5}), encoding="utf-8")
    assert run_tests.expected_seconds(a, "full") == 12.5
    assert run_tests.expected_seconds(a, "fast") is None                    # another mode
    summ.write_text(json.dumps({"mode": "full", "passed": 5, "failed": 1, "seconds": 400}), encoding="utf-8")
    assert run_tests.expected_seconds(a, "full") is None                    # a red run says nothing
    summ.write_text(json.dumps({"mode": "full", "passed": 1, "failed": 0, "seconds": 400, "suspect_hang": {}}), encoding="utf-8")
    assert run_tests.expected_seconds(a, "full") is None
    monkeypatch.setenv("SUPERLITE_TEST_EXPECT_S", "7")
    assert run_tests.expected_seconds(a, "full") == 7
    a.expect = 30.0
    assert run_tests.expected_seconds(a, "full") == 30.0


# ------------------------------------------------------------------ the OS call, and its failure

def test_take_sample_sees_a_live_process_tree_and_its_cpu():
    busy = subprocess.Popen([sys.executable, "-c", "while True: pass"])
    try:
        time.sleep(0.4)
        a = tp.take_sample(os.getpid(), [])
        assert a.method != "none" and a.cpu is not None and os.getpid() in a.pids and busy.pid in a.pids
        b = tp.take_sample(busy.pid, [])
        time.sleep(0.6)
        c = tp.take_sample(busy.pid, [])
        assert b.pids == frozenset({busy.pid}) and c.cpu - b.cpu > 0.2, (b.cpu, c.cpu, b.method)
        assert tp.cpu_method() == a.method
    finally:
        busy.kill()
        busy.wait()


def test_cpu_call_failure_degrades_to_output_and_children(monkeypatch, tmp_path):
    out = tmp_path / "out.txt"
    out.write_bytes(b"x" * 10)

    def boom():
        raise OSError("no cpu for you")

    monkeypatch.setattr(tp, "read_table", boom)
    s = tp.take_sample(os.getpid(), [out])
    assert s.cpu is None and s.pids is None and s.out == 10 and s.method == "none"
    assert tp.classify(s, tp.Sample(s.t + 12, 10, None, None, {}, "none"), CFG) == "stalled"
    assert "none" in tp.cpu_method()


def test_backend_none_degrades_a_real_hang_to_stalled(monkeypatch):
    monkeypatch.setenv("SUPERLITE_TESTPROBE_BACKEND", "none")
    tp._BACKEND.clear()
    seen = []
    cfg = dict(CFG, min_start_s=0.3, fallback_expected_s=0.1, sample_interval_s=0.25, min_flat_s=0.5, hard_timeout_s=2.0)
    r = tp.run_watched(sleeper(60), cfg=cfg, on_suspect=seen.append)
    tp._BACKEND.clear()
    assert r.killed == "timeout" and len(seen) == 1
    assert seen[0]["verdict"] == "stalled" and seen[0]["cpu_pct"] is None and seen[0]["cpu_method"] == "none"
    assert "CPU time unavailable" in tp.suspect_line(seen[0])


# ------------------------------------------------------------------ real hangs through run_tests

FAST = {"enabled": True, "multiplier": 3, "fallback_expected_s": 0.1, "min_start_s": 0.4, "sample_interval_s": 0.3,
        "min_flat_s": 0.8, "cpu_idle_pct": 2, "cpu_busy_pct": 20, "auto_kill_blocked": False,
        "auto_kill_after_s": 1.0, "hard_timeout_s": 0, "max_tree_listed": 8}

PARENT = """import subprocess, sys, time
c = subprocess.Popen([sys.executable, "-c", {child!r}])
open("child.pid", "w").write(str(c.pid))
{body}
"""


def hang_project(tmp_path, kind, **cfg):
    proj = tmp_path / "proj"
    proj.mkdir()
    spin = kind == "spin"
    body = "while True: pass" if spin else "time.sleep(1000)"
    child = "while True: pass" if spin else "import time; time.sleep(1000)"
    (proj / "hang.py").write_text(PARENT.format(child=child, body=body), encoding="utf-8")
    (proj / ".superlite").mkdir()
    (proj / ".superlite" / "test-cmd.json").write_text(json.dumps(
        {"language": "typescript", "test": f"{sys.executable} hang.py", "test_file": f"{sys.executable} hang.py {{files}}",
         "junit": "", "setup": ""}), encoding="utf-8")
    conf = tmp_path / "probe.json"
    conf.write_text(json.dumps({**FAST, **cfg}), encoding="utf-8")
    env = {**os.environ, "SUPERLITE_TESTPROBE_CONFIG": str(conf), "PYTHONUTF8": "1"}
    env.pop("SUPERLITE_TEST_EXPECT_S", None)
    return proj, env


def rt(proj, env, *args, timeout=60):
    return subprocess.run([sys.executable, str(RUN_TESTS), *args], cwd=proj, env=env, capture_output=True, text=True,
                          encoding="utf-8", timeout=timeout)


def alive(pid):
    return pid in tp.read_table()[0]


def read_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def test_spinning_run_is_reported_and_hard_timeout_kills_the_whole_tree(tmp_path):
    proj, env = hang_project(tmp_path, "spin", hard_timeout_s=2.0)
    t0 = time.monotonic()
    r = rt(proj, env, "--fast", "tests/markdown.test.ts")
    assert time.monotonic() - t0 < 30
    assert r.returncode == 1, r.stdout + r.stderr
    line = next(ln for ln in r.stdout.splitlines() if ln.startswith("SUSPECT HANG"))   # printed at once
    assert "SPINNING" in line and "tests/markdown.test.ts" in line and "infinite loop" in line
    s = read_json(proj / ".superlite" / "test-summary.json")
    assert {"passed", "failed", "skipped", "seconds", "mode", "failures", "skipped_tests"} <= set(s)   # old shape intact
    assert s["failed"] >= 1 and s["mode"] == "fast" and "running" not in s
    assert s["verdict"] == "SUSPECT HANG" and s["timeout_s"] >= 2.0
    h = s["suspect_hang"]
    assert h["verdict"] == "spinning" and h["test_file"] == "tests/markdown.test.ts" and h["flat_s"] >= 0.8
    assert h["cpu_pct"] >= 20 and len(h["tree"]) >= 2 and h["cpu_method"]
    assert s["failures"][0].startswith("-::TIMEOUT:") and "SPINNING" in s["failures"][0]
    assert "4294967295" not in json.dumps(s)
    inc = [json.loads(x) for x in (proj / ".superlite" / "incidents.jsonl").read_text(encoding="utf-8").splitlines()]
    perf = [e for e in inc if e.get("kind") == "perf"]
    assert len(perf) == 1 and "SUSPECT HANG spinning" in perf[0]["context"] and perf[0]["file"] == "tests/markdown.test.ts"
    assert not (proj / ".superlite" / "test-summary.suspect").exists()   # a foreground run leaves no sidecar
    time.sleep(0.3)
    assert not alive(int((proj / "child.pid").read_text()))                # the grandchild died with the tree


def test_blocked_run_wait_returns_early_and_auto_kill_is_by_pid(tmp_path):
    proj, env = hang_project(tmp_path, "sleep", auto_kill_blocked=True, auto_kill_after_s=1.5)
    assert rt(proj, env, "--background").returncode == 0
    w = rt(proj, env, "--wait", "--timeout", "40")
    assert w.returncode == 3, w.stdout + w.stderr           # early exit on the suspect, not a timeout
    assert w.stdout.startswith("SUSPECT HANG: BLOCKED") and "deadlock or wait" in w.stdout
    published = proj / ".superlite" / "test-summary.json"
    for _ in range(100):                                      # the provisional summary lands right after the sidecar
        if published.exists():
            break
        time.sleep(0.1)
    prov = read_json(published)
    assert prov["running"] is True and prov["verdict"] == "SUSPECT HANG" and prov["failed"] == 1
    w = rt(proj, env, "--wait", "--patient", "--timeout", "60")
    assert w.returncode == 1, w.stdout + w.stderr
    s = json.loads(w.stdout)
    assert s["verdict"] == "SUSPECT HANG" and s["suspect_hang"]["verdict"] == "blocked"
    assert s["suspect_hang"]["cpu_pct"] <= 2 and s["suspect_hang"]["killed_after_s"] > 0 and "running" not in s
    assert "killed the process tree" in s["failures"][0] and "exit code" not in s["failures"][0]
    final = read_json(published)
    assert final["verdict"] == "SUSPECT HANG" and "running" not in final   # the real summary replaced the provisional one
    time.sleep(0.3)
    assert not alive(int((proj / "child.pid").read_text()))


def test_wait_reports_suspect_and_timeout_from_a_sidecar(tmp_path):
    sl = tmp_path / ".superlite"
    sl.mkdir()
    mine = sl / "test-summary-r1.json"
    (sl / "test-summary.latest").write_text(json.dumps({"id": "r1", "summary": str(mine)}), encoding="utf-8")
    run_tests.atomic_write(run_tests.suspect_path(mine), json.dumps(
        {"suspect_hang": {"line": "SUSPECT HANG: BLOCKED x - no output for 40s"}}))
    env = {**os.environ}
    w = rt(tmp_path, env, "--wait", "--timeout", "30")
    assert w.returncode == 3 and "SUSPECT HANG: BLOCKED x" in w.stdout and "--patient" in w.stdout
    w = rt(tmp_path, env, "--wait", "--patient", "--timeout", "1")
    assert w.returncode == 2 and "TIMEOUT" in w.stderr and "timed out" in w.stderr and "SUSPECT HANG: BLOCKED x" in w.stderr
    mine.write_text(json.dumps({"passed": 1, "failed": 0, "skipped": 0}), encoding="utf-8")
    assert rt(tmp_path, env, "--wait", "--timeout", "5").returncode == 0     # a finished summary wins over the sidecar


@pytest.mark.skipif(os.name != "nt", reason="the 4294967295 exit code is a Windows one")
def test_windows_minus_one_exit_reads_as_killed_or_crashed(tmp_path):
    (tmp_path / ".superlite").mkdir()
    (tmp_path / ".superlite" / "test-cmd.json").write_text(json.dumps(
        {"language": "go", "test": f'{sys.executable} -c "import ctypes; ctypes.windll.kernel32.ExitProcess(0xFFFFFFFF)"', "test_file": "", "junit": "", "setup": ""}),
        encoding="utf-8")
    r = rt(tmp_path, {**os.environ})
    assert r.returncode == 1
    s = read_json(tmp_path / ".superlite" / "test-summary.json")
    assert s["failures"][0].startswith("-::process killed or crashed (exit -1)") and "4294967295" not in json.dumps(s)
    assert set(s) == {"passed", "failed", "skipped", "seconds", "mode", "failures", "skipped_tests"}


def test_a_quiet_fast_run_pays_nothing_and_keeps_its_summary_shape(tmp_path):
    (tmp_path / ".superlite").mkdir()
    (tmp_path / ".superlite" / "test-cmd.json").write_text(json.dumps(
        {"language": "go", "test": f'{sys.executable} -c "print(1)"', "test_file": "", "junit": "", "setup": ""}), encoding="utf-8")
    r = rt(tmp_path, {**os.environ})
    assert r.returncode == 0 and "SUSPECT" not in r.stdout
    assert set(read_json(tmp_path / ".superlite" / "test-summary.json")) == {
        "passed", "failed", "skipped", "seconds", "mode", "failures", "skipped_tests"}
    assert not list((tmp_path / ".superlite").glob("*.suspect"))
