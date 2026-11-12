import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"))
import learn_signals as sig  # noqa: E402


def test_read_new_lines_complete_rows(tmp_path):
    p = tmp_path / "a.jsonl"
    p.write_bytes(b'{"a": 1}\n{"b": 2}\n')
    rows, off = sig.read_new_lines(p, 0)
    assert rows == [{"a": 1}, {"b": 2}] and off == p.stat().st_size


def test_read_new_lines_partial_last_line_waits(tmp_path):
    p = tmp_path / "a.jsonl"
    p.write_bytes(b'{"a": 1}\n{"b"')
    rows, off = sig.read_new_lines(p, 0)
    assert rows == [{"a": 1}] and off == 9
    p.write_bytes(b'{"a": 1}\n{"b": 2}\n')
    rows, off2 = sig.read_new_lines(p, off)
    assert rows == [{"b": 2}] and off2 == p.stat().st_size


def test_read_new_lines_skips_bad_and_non_objects(tmp_path):
    p = tmp_path / "a.jsonl"
    p.write_bytes(b'nope\n[1]\n\xff\xfe\n{"ok": true}\n')
    assert sig.read_new_lines(p, 0)[0] == [{"ok": True}]


def test_read_new_lines_missing_and_stale_offset(tmp_path):
    assert sig.read_new_lines(tmp_path / "none", 5) == ([], 5)
    p = tmp_path / "a.jsonl"
    p.write_bytes(b'{"a": 1}\n')
    rows, off = sig.read_new_lines(p, 999)
    assert rows == [{"a": 1}] and off == 9


def test_is_hook_row():
    assert sig.is_hook_row({"kind": "rule-block", "tool": "Bash"})
    assert sig.is_hook_row({"kind": "rule-rewrite"})
    assert not sig.is_hook_row({"kind": "flaky", "file": "a", "line": 1})
    assert not sig.is_hook_row("x")


def test_parse_findings_frozen_format():
    text = ("Verdict: issues\n"
            "a/b.py:12 - state leaks across runs - reset it in setup - tier 2\n"
            "- c.py:3 - encoding dropped on write - pass utf-8 - tier 1\n"
            "- d.py:4 - misc - fix - tier 3\n"
            "not a finding line\n"
            "e.py:x - bad - bad - tier 1\n")
    rows = sig.parse_findings(text, "r1", "2026-11-12")
    assert [(r["file"], r["line"], r["tier"], r["category"]) for r in rows] == [
        ("a/b.py", 12, 2, "state"), ("c.py", 3, 1, "encoding"), ("d.py", 4, 3, "other")]
    assert rows[0]["run"] == "r1" and rows[0]["v"] == 1 and rows[0]["fixed"] is None


def test_parse_findings_partial_line_ignored():
    assert sig.parse_findings("a.py:1 - problem - fi", "r", "d") == []
    assert sig.parse_findings("", "r", "d") == []


def test_parse_fix_note():
    note = ("# F-T3-2 · tier 1 · round 1 · NOT_FIXED\n"
            "File: a/b.py\nCause: wrong key\nFix: use the id\n")
    assert sig.parse_fix_note(note) == {"id": "F-T3-2", "task": "T3", "tier": 1, "status": "NOT_FIXED",
                                        "file": "a/b.py", "cause": "wrong key", "fix": "use the id"}
    assert sig.parse_fix_note("# F-T3-2 · tier 1 · round 1 · MAYBE\n") is None
    assert sig.parse_fix_note("") is None


def test_parse_learned():
    assert sig.parse_learned("DONE\nLearned: setup: run uv sync first\n") == {"tag": "setup",
                                                                              "text": "run uv sync first"}
    assert sig.parse_learned("Learned: mood: fine") is None
    assert sig.parse_learned("DONE") is None


def test_model_mismatches():
    launches = [{"ev": "launch", "agent_id": "a1", "model": "claude-opus-4"},
                {"ev": "launch", "agent_id": "a2", "model": "claude-sonnet-5"},
                {"ev": "stop", "agent_id": "a1", "model": "x"},
                {"ev": "launch", "agent_id": "zz", "model": "claude-haiku"}]
    out = sig.model_mismatches(launches, {"a1": "sonnet", "a2": {"model": "sonnet"}})
    assert out == [{"requested": "sonnet", "ran": "opus"}]


def test_outbox_row_hashes_repo_and_drops_unlisted():
    row = sig.outbox_row("hook-block", "/my/repo", "2026-11-12", "4.1.0", tool="Bash", count=2,
                         command="rm -rf", target="t", file="f", path="/p", text="secret")
    assert row["tool"] == "Bash" and row["count"] == 2
    assert row["repo"] != "/my/repo" and len(row["repo"]) == 16
    assert not {"command", "target", "file", "path", "text"} & set(row)
    assert "/my/repo" not in json.dumps(row)


def test_outbox_run_field():
    row = sig.outbox_row("hook-block", "/r", "d", "4.1.0", run="r1", tool="Bash", count=1)
    assert row["run"] == "r1" and row["tool"] == "Bash" and row["count"] == 1
    assert row["signal"] == "hook-block" and row["superlite"] == "4.1.0"
