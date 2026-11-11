"""The shipped docs describe the run archive: finish moves the run into `.superlite/runs/`, the feature flow
finishes its integration plan, and sessions never move `runs/`."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_swarm_no_manual_archive():
    text = read("skills/swarm/SKILL.md")
    assert "Archive `.superlite/incidents.jsonl`" not in text
    assert ".superlite/runs/" in text


def test_feature_integration_finish_plan():
    lines = read("commands/feature.md").splitlines()
    step6 = [line for line in lines if line.startswith("6. ")]
    assert len(step6) == 1
    assert "superlite-cli finish --plan" in step6[0]


def test_sessions_doc_runs_stationary():
    text = read("docs/sessions.md")
    assert ".superlite/runs/" in text
    rows = [line for line in text.splitlines() if line.startswith("| `runs/`")]
    assert len(rows) == 1
    assert "never moved by sessions" in rows[0]


def test_readme_mentions_runs():
    assert ".superlite/runs/" in read("README.md")


def test_start_handoff_does_not_repeat_session_ensure():
    start = read("commands/start.md")
    assert "session was ensured above" in start
    assert "skip that command's initial ensure step" in start
    for path in ("commands/small.md", "commands/review.md"):
        assert "When invoked independently (not from `/superlite:start`)" in read(path)
