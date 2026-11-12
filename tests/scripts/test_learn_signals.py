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
    """The frozen fixer-note format of skills/swarm/fixing.md."""
    note = ("# F-T3-2 · tier 1 · round 1 · NOT_FIXED\n"
            "Goal: keys match the plan ids\n"
            "Where: a/b.py:10-14, a/c.py:3\n"
            "Changed: a/b.py:12 - use the id - the name collided - twice\n"
            "Tried and failed: nothing\n"
            "Evidence: test_b::test_key - KeyError\n"
            "Next: read a/c.py:3\n")
    assert sig.parse_fix_note(note) == {
        "id": "F-T3-2", "task": "T3", "tier": 1, "status": "NOT_FIXED", "goal": "keys match the plan ids",
        "where": "a/b.py:10-14, a/c.py:3", "changed": "a/b.py:12 - use the id - the name collided - twice",
        "evidence": "test_b::test_key - KeyError", "file": "a/b.py", "what": "use the id",
        "why": "the name collided - twice"}
    assert sig.parse_fix_note("# F-T3-2 · tier 1 · round 1 · MAYBE\n") is None
    assert sig.parse_fix_note("") is None


def test_parse_fix_note_nothing_kept_and_crlf():
    note = "# F-T3-2 · tier 2 · round 2 · FIXED\r\nWhere: x\\y.py:1-2\r\nChanged: nothing kept\r\n"
    got = sig.parse_fix_note(note)
    assert got["file"] == "x/y.py" and got["what"] == "" and got["changed"] == "nothing kept" and got["tier"] == 2


def test_decode_note_utf8_and_cp1252():
    head = "# F-T1-1 · tier 1 · round 1 · FIXED\n"
    assert sig.decode_note(("\ufeff" + head).encode("utf-8")) == head
    assert sig.parse_fix_note(sig.decode_note(head.encode("cp1252")))["status"] == "FIXED"


def test_parse_learned():
    assert sig.parse_learned("DONE\nLearned: setup: run uv sync first\n") == {"tag": "setup",
                                                                              "text": "run uv sync first"}
    assert sig.parse_learned("Learned: mood: fine") is None
    assert sig.parse_learned("DONE") is None


def test_model_mismatches():
    """The requested tier comes from the launch's agent type (dispatch.json holds only batches)."""
    tiers = {"implementer": "sonnet", "fixer-opus": "opus"}
    launches = [{"ev": "launch", "agent_id": "a1", "type": "superlite:implementer", "model": "claude-opus-4"},
                {"ev": "launch", "agent_id": "a2", "type": "superlite:fixer-opus", "model": "claude-opus-5"},
                {"ev": "stop", "agent_id": "a1", "type": "superlite:implementer", "model": "x"},
                {"ev": "launch", "agent_id": "a3", "type": "superlite:implementer-haiku", "model": "claude-sonnet-5"},
                {"ev": "launch", "agent_id": "zz", "type": "general-purpose", "model": "claude-haiku"}]
    assert sig.model_mismatches(launches, tiers) == [{"requested": "sonnet", "ran": "opus"},
                                                     {"requested": "haiku", "ran": "sonnet"}]


def test_agent_tiers_from_agent_files(tmp_path):
    (tmp_path / "final-reviewer.md").write_text("---\nname: final-reviewer\nmodel: opus\n---\nbody\n")
    (tmp_path / "notes.md").write_text("no frontmatter\n")
    assert sig.agent_tiers(tmp_path) == {"final-reviewer": "opus"}
    assert sig.requested_family("superlite:final-reviewer", sig.agent_tiers(tmp_path)) == "opus"
    real = sig.agent_tiers(Path(sig.__file__).resolve().parents[3] / "agents")
    assert real["implementer-haiku"] == "haiku" and real["fixer-opus"] == "opus" and real["implementer"] == "sonnet"


def test_outbox_row_hashes_repo_and_drops_unlisted():
    row = sig.outbox_row("hook-block", "/my/repo", "2026-11-12", "4.1.0", tool="Bash", count=2,
                         command="rm -rf", target="t", file="f", path="/p", text="secret")
    assert row["tool"] == "Bash" and row["count"] == 2
    assert row["repo"] != "/my/repo" and len(row["repo"]) == 16
    assert not {"command", "target", "file", "path", "text"} & set(row)
    assert "/my/repo" not in json.dumps(row)


def test_outbox_run_field():
    row = sig.outbox_row("hook-block", "/r", "d", "4.1.0", run="r1", tool="Bash", count=1)
    assert row["run"] != "r1" and len(row["run"]) == 16 and row["tool"] == "Bash" and row["count"] == 1
    assert row["signal"] == "hook-block" and row["superlite"] == "4.1.0"


def test_outbox_run_is_opaque_and_stable():
    """A real run id embeds the user's topic slug: the outbox keeps only a stable hash of it."""
    rid = "20261112-045224-my-topic"
    row = sig.outbox_row("hook-block", "/my/repo", "2026-11-12", "4.1.0", run=rid, tool="Bash", count=1)
    blob = json.dumps(row)
    assert "my-topic" not in blob and "topic" not in blob and "20261112" not in blob and "045224" not in blob
    again = sig.outbox_row("model-mismatch", "/my/repo", "2026-11-13", "4.1.0", run=rid, requested="a", ran="b")
    assert again["run"] == row["run"] and len(row["run"]) == 16
    assert sig.outbox_row("hook-block", "/my/repo", "d", "v", run=rid + "-2")["run"] != row["run"]
