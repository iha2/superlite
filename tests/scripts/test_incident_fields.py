import datetime
import json
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import incident  # noqa: E402


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPTS / "incident.py"), *args], capture_output=True, text=True)


def test_new_fields():
    e = incident.make_entry("a/b.py", 3, "x", "T1", "test-failure", "", "high", run="r1")
    assert e["run"] == "r1" and e["module"] == "a"
    assert e["date"] == datetime.date.today().isoformat()
    assert e["file"] == "a/b.py" and e["agent"] == "T1" and "time" in e
    assert "category" not in e
    assert incident.make_entry("top.py", 1, "x")["module"] == "."
    assert incident.make_entry("a\\b\\c.py", 1, "x")["module"] == "a/b"
    assert incident.make_entry("a/b.py", 1, "x", date="2026-01-02", module="m")["date"] == "2026-01-02"
    assert incident.make_entry("a/b.py", 1, "x", module="m")["module"] == "m"
    assert "run" not in incident.make_entry("a/b.py", 1, "x")


def test_category_optional(tmp_path):
    log = tmp_path / "i.jsonl"
    assert incident.make_entry("a.py", 1, "x", category="state")["category"] == "state"
    r = run("add", "--file", "a.py", "--line", "1", "--context", "c", "--log", str(log))
    assert r.returncode == 0, r.stderr
    r = run("add", "--file", "a.py", "--line", "2", "--context", "c", "--category", "state", "--log", str(log))
    assert r.returncode == 0, r.stderr
    rows = [json.loads(x) for x in log.read_text().splitlines()]
    assert "category" not in rows[0] and rows[1]["category"] == "state"


def _log(tmp_path):
    log = tmp_path / "i.jsonl"
    rows = [{"file": "a.py", "line": 1, "context": "c", "kind": "flaky"},
            {"kind": "rule-block", "tool": "Bash", "date": "2026-01-01"},
            "not json",
            {"kind": "rule-rewrite", "tool": "Bash"}]
    log.write_text("\n".join(r if isinstance(r, str) else json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return log


def test_read_all_keeps_hook_rows(tmp_path):
    rows = incident.read_all(str(_log(tmp_path)))
    assert [r.get("kind") for r in rows] == ["flaky", "rule-block", "rule-rewrite"]
    assert incident.read_all(str(tmp_path / "none.jsonl")) == []


def test_hotspots_skip_hook_rows(tmp_path):
    r = run("hotspots", "--log", str(_log(tmp_path)))
    assert r.returncode == 0, r.stderr
    out = [json.loads(x) for x in r.stdout.splitlines()]
    assert [o["file"] for o in out] == ["a.py"]


def test_summary_counts_hook_rows(tmp_path):
    r = run("summary", "--log", str(_log(tmp_path)))
    assert r.returncode == 0, r.stderr
    assert "total: 3" in r.stdout
    assert "rule-block=1" in r.stdout and "rule-rewrite=1" in r.stdout and "flaky=1" in r.stdout
    assert "files: a.py=1" in r.stdout


def test_old_rows_ok(tmp_path):
    log = tmp_path / "i.jsonl"
    log.write_text(json.dumps({"file": "a.py", "line": 1, "context": "c", "time": "10:00:00"}) + "\n")
    assert len(incident.read_all(str(log))) == 1
    r = run("hotspots", "--log", str(log))
    assert r.returncode == 0 and "a.py" in r.stdout
    assert run("summary", "--log", str(log)).returncode == 0
