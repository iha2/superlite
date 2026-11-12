"""history.record_rows: a changed outcome for the same (repository, run, task) is appended and the newest wins."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"))

import history  # noqa: E402


@pytest.fixture
def sl(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERLITE_HOME", str(tmp_path / "home"))
    d = tmp_path / "proj" / ".superlite"
    d.mkdir(parents=True)
    return d


def row(**kw):
    base = {"v": 1, "run": "r", "task": "T1", "repository": "/repo", "date": "2026-01-01", "tier": "sonnet",
            "kind": "design", "loc": 50, "tests": "fast", "duration_s": 60, "first_time_pass": False}
    return {**base, **kw}


def test_new_row(sl):
    assert history.record_rows([row()], str(sl)) == [row()]
    assert len(history.read_rows()) == 1 and len(history.read_rows(sl / "history.jsonl")) == 1
    assert (history.home() / "stats.json").is_file() and (sl / "stats.json").is_file()


def test_same_row_noop(sl):
    history.record_rows([row()], str(sl))
    assert history.record_rows([row()], str(sl)) == []
    assert len(history.read_rows()) == 1


def test_changed_outcome_appended(sl):
    history.record_rows([row()], str(sl))
    assert len(history.record_rows([row(first_time_pass=True)], str(sl))) == 1
    assert len(history.read_rows()) == 2 and len(history.read_rows(sl / "history.jsonl")) == 2
    assert history.record_rows([row(first_time_pass=True)], str(sl)) == []
    assert history.load_stats()["bands"]  # derived from the newest row only
    assert sum(b["n"] for b in history.load_stats()["bands"].values()) == 1


def test_unique_rows_last_wins():
    rows = [row(first_time_pass=False), row(task="T2"), row(first_time_pass=True, duration_s=70)]
    out = history.unique_rows(rows)
    assert [(r["task"], r["first_time_pass"]) for r in out] == [("T2", False), ("T1", True)]


def test_record_run_unchanged_signature():
    assert list(inspect.signature(history.record_run).parameters) == ["plan_path", "sl_dir", "run_id"]
    assert list(inspect.signature(history.unique_rows).parameters) == ["rows"]
    assert list(inspect.signature(history.record_rows).parameters) == ["rows", "sl_dir"]


def test_old_stats_identical():
    rows = [row(task=f"T{i}", duration_s=10 * i, repository=repo) for i in range(1, 5) for repo in ("/a", "/b")]
    assert history.unique_rows(rows) == rows  # no repeats: nothing dropped
    dup = rows + [dict(rows[0], run="other")]  # content duplicate, new run id: still counted once
    assert history.derive(history.unique_rows(dup)) == history.derive(rows)
    assert history.derive(history.unique_rows(rows))["rows"] == 8  # same content in two repositories counts twice
