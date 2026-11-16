import json
import subprocess
import sys
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parents[2] / "skills"
          / "swarm" / "scripts" / "incident.py")


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True)


def add(log, file, line, context="c"):
    return run("add", "--file", file, "--line", str(line),
               "--context", context, "--log", str(log))


def resolve(log, file, line, evidence="run-12"):
    return run("resolve", "--file", file, "--line", str(line),
               "--evidence", evidence, "--log", str(log))


def rows(log):
    return [json.loads(x) for x in Path(log).read_text(encoding="utf-8").splitlines()]


def test_resolve_marks(tmp_path):
    log = tmp_path / "i.jsonl"
    assert add(log, "src/a.py", 5, "first").returncode == 0
    assert add(log, "src/a.py", 5, "second").returncode == 0
    r = resolve(log, "src/a.py", 5)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "incident: resolved 2 at src/a.py:5"
    out = rows(log)
    assert len(out) == 2
    for row in out:
        assert row["resolution"] == "fixed"
        assert row["evidence"] == "run-12"


def test_resolve_none(tmp_path):
    log = tmp_path / "i.jsonl"
    assert add(log, "src/a.py", 5).returncode == 0
    r = resolve(log, "src/a.py", 9)
    assert r.returncode == 1
    assert r.stdout.strip() == "incident: nothing open at src/a.py:9"
    assert "resolution" not in rows(log)[0]


def test_resolve_path_norm(tmp_path):
    log = tmp_path / "i.jsonl"
    assert add(log, "src/a.py", 5).returncode == 0
    r = resolve(log, "./src\\a.py", 5)
    assert r.returncode == 0, r.stderr
    assert rows(log)[0]["resolution"] == "fixed"


def test_other_rows_untouched(tmp_path):
    log = tmp_path / "i.jsonl"
    assert add(log, "src/a.py", 5, "target").returncode == 0
    assert add(log, "src/a.py", 6, "other line").returncode == 0
    assert add(log, "src/b.py", 5, "other file").returncode == 0
    log.write_text(log.read_text(encoding="utf-8") + "not json\n", encoding="utf-8")
    before = log.read_text(encoding="utf-8").splitlines(keepends=True)
    r = resolve(log, "src/a.py", 5)
    assert r.returncode == 0, r.stderr
    after = log.read_text(encoding="utf-8").splitlines(keepends=True)
    assert len(after) == len(before)
    assert after[1] == before[1]
    assert after[2] == before[2]
    assert after[3] == before[3]
    assert json.loads(after[0])["resolution"] == "fixed"


def test_resolve_keeps_inode(tmp_path):
    log = tmp_path / "i.jsonl"
    assert add(log, "src/a.py", 5).returncode == 0
    before = log.stat().st_ino
    assert resolve(log, "src/a.py", 5).returncode == 0
    assert log.stat().st_ino == before
    assert rows(log)[0]["resolution"] == "fixed"
