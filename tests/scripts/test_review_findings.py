import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts" / "review_findings.py"
REPLY = ("Verdict: issues\n"
         "a/b.py:12 - state leaks across runs - reset it in setup - tier 2\n"
         "- c.py:3 - encoding dropped on write - pass utf-8 - tier 1\n"
         "junk line\n")


def run(cwd, *args, stdin=None):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, cwd=cwd,
                          input=stdin)


def make_run(tmp_path):
    sl = tmp_path / ".superlite"
    sl.mkdir()
    (sl / "current-run.json").write_text(json.dumps(
        {"id": "20261112-100000-topic", "topic": "t", "slug": "s", "started_at": "2026-11-12T10:00:00"}))
    return sl


def test_record_writes_rows(tmp_path):
    sl = make_run(tmp_path)
    reply = tmp_path / "reply.txt"
    reply.write_text(REPLY, encoding="utf-8")
    r = run(tmp_path, "record", "--file", str(reply))
    assert r.returncode == 0, r.stderr
    assert "review_findings: 2 recorded" in r.stdout
    rows = [json.loads(x) for x in (sl / "review-findings.jsonl").read_text().splitlines()]
    assert [(x["file"], x["line"], x["tier"]) for x in rows] == [("a/b.py", 12, 2), ("c.py", 3, 1)]
    assert rows[0]["run"] == "20261112-100000-topic"


def test_record_from_stdin_appends(tmp_path):
    sl = make_run(tmp_path)
    assert run(tmp_path, "record", stdin=REPLY).returncode == 0
    assert run(tmp_path, "record", stdin=REPLY).returncode == 0
    assert len((sl / "review-findings.jsonl").read_text().splitlines()) == 4


def test_record_zero_lines_ok(tmp_path):
    sl = make_run(tmp_path)
    r = run(tmp_path, "record", stdin="Verdict: clean\n")
    assert r.returncode == 0
    assert "review_findings: 0 recorded" in r.stdout
    assert not (sl / "review-findings.jsonl").exists()


def test_record_help_no_side_effects(tmp_path):
    r = run(tmp_path, "record", "--help")
    assert r.returncode == 0 and "--file" in r.stdout
    assert not (tmp_path / ".superlite").exists()
