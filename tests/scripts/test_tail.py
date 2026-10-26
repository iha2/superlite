"""sl tail: the live feed of a run's metrics.jsonl and progress.md."""
import json
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import tail  # noqa: E402


def test_poll_prints_starts_dones_and_ledger_then_only_new_lines(tmp_path):
    sl = tmp_path / ".superlite"
    sl.mkdir()
    (sl / "metrics.jsonl").write_text(
        json.dumps({"ev": "launch", "t": 1000, "agent_id": "a15c488ca2", "type": "superlite:implementer", "desc": "T3 templates"}) + "\n"
        + '{"ev":"stop","t"\n'
        + json.dumps({"ev": "other"}) + "\n",
        encoding="utf-8",
    )
    (sl / "progress.md").write_text("# Progress\n- sent T3+T1 to a15c488ca2\n\n", encoding="utf-8")
    offsets, t0 = {}, []
    rows = tail.poll(sl, offsets, t0)
    assert len(rows) == 2
    assert "START" in rows[0] and "implementer" in rows[0] and "a15c488c" in rows[0] and "T3 templates" in rows[0]
    assert rows[1].endswith("LEDGER - sent T3+T1 to a15c488ca2")
    assert tail.poll(sl, offsets, t0) == []
    with open(sl / "metrics.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"ev": "stop", "t": 61000, "agent_id": "a15c488ca2", "type": "superlite:implementer"}) + "\n")
    rows = tail.poll(sl, offsets, t0)
    assert len(rows) == 1 and "DONE" in rows[0] and "+   60s" in rows[0]


def test_a_rewritten_file_is_read_again_from_the_start(tmp_path):
    sl = tmp_path / ".superlite"
    sl.mkdir()
    (sl / "progress.md").write_text("- a long first ledger line\n- tests full passed=60 failed=0\n", encoding="utf-8")
    offsets, t0 = {}, []
    assert len(tail.poll(sl, offsets, t0)) == 2
    (sl / "progress.md").write_text("- new run\n", encoding="utf-8")
    rows = tail.poll(sl, offsets, t0)
    assert len(rows) == 1 and rows[0].endswith("LEDGER - new run")


def test_missing_folder_waits_and_once_exits(tmp_path):
    out = subprocess.run([sys.executable, str(SCRIPTS / "tail.py"), "--dir", str(tmp_path / "nope"), "--once"],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0
    assert "waiting for" in out.stdout
