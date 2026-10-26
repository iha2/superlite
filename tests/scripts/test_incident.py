import json
import subprocess
import sys
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parents[2] / "skills"
          / "swarm" / "scripts" / "incident.py")


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True)


def add(log, file, line, context="c", *extra):
    return run("add", "--file", file, "--line", str(line),
               "--context", context, "--log", str(log), *extra)


def lines(log):
    return Path(log).read_text(encoding="utf-8").splitlines()


def hot(log, *args):
    r = run("hotspots", "--log", str(log), *args)
    assert r.returncode == 0, r.stderr
    return [json.loads(x) for x in r.stdout.splitlines()]


def test_add_writes_one_json_line(tmp_path):
    log = tmp_path / "i.jsonl"
    assert add(log, "a.py", 1, "ctx").returncode == 0
    ls = lines(log)
    assert len(ls) == 1
    assert set(json.loads(ls[0])) == {"file", "line", "context", "time"}


def test_context_truncated_to_200(tmp_path):
    log = tmp_path / "i.jsonl"
    add(log, "a.py", 1, "x" * 300)
    ctx = json.loads(lines(log)[0])["context"]
    assert len(ctx) == 200 and ctx.endswith("…")


def test_line_must_be_int_ge_0(tmp_path):
    r = add(tmp_path / "i.jsonl", "a.py", -1)
    assert r.returncode == 2
    assert "line must be >= 0" in r.stderr


def test_kind_must_be_known(tmp_path):
    assert add(tmp_path / "i.jsonl", "a.py", 1, "c", "--kind", "banana").returncode == 2


def test_all_kinds_accepted_including_verified(tmp_path):
    log = tmp_path / "i.jsonl"
    for k in ("test-failure", "exception", "workaround", "spec-ambiguity",
              "assumption", "flaky", "perf", "unsure", "verified"):
        assert add(log, "a.py", 0, "c", "--kind", k).returncode == 0, k
    assert len(lines(log)) == 9


def test_parallel_appends_are_whole_lines(tmp_path):
    log = tmp_path / "i.jsonl"
    code = ("import subprocess,sys\n"
            "for i in range(50):\n"
            "    subprocess.run([sys.executable,sys.argv[1],'add','--file','a.py',"
            "'--line',str(i),'--context','c'*100,'--log',sys.argv[2]],check=True)\n")
    procs = [subprocess.Popen([sys.executable, "-c", code, str(SCRIPT), str(log)])
             for _ in range(8)]
    for p in procs:
        assert p.wait() == 0
    ls = lines(log)
    assert len(ls) == 400
    for x in ls:
        json.loads(x)


def test_hotspots_filters_files_and_sorts(tmp_path):
    log = tmp_path / "i.jsonl"
    for f, n in (("a.py", 10), ("b.py", 5), ("a.py", 10), ("a.py", 3)):
        add(log, f, n)
    got = [(e["file"], e["line"]) for e in hot(log, "--files", "a.py")]
    assert got == [("a.py", 10), ("a.py", 10), ("a.py", 3)]


def test_hotspots_puts_open_before_fixed(tmp_path):
    log = tmp_path / "i.jsonl"
    add(log, "a.py", 1, "fixed one", "--resolution", "fixed")
    add(log, "a.py", 2, "open one", "--resolution", "open")
    got = hot(log, "--files", "a.py")
    assert [e["resolution"] for e in got] == ["open", "fixed"]


def test_summary_runs(tmp_path):
    log = tmp_path / "i.jsonl"
    add(log, "a.py", 1, "c", "--kind", "verified")
    r = run("summary", "--log", str(log))
    assert r.returncode == 0 and "total: 1" in r.stdout
