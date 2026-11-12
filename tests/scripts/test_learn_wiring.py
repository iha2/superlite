"""The advisory learn hooks in checkpoint, session registration, session close and finish."""
from __future__ import annotations

import inspect
import sys
import types
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import checkpoint  # noqa: E402
import finish  # noqa: E402
import run_archive  # noqa: E402
import session_close  # noqa: E402
import session_registration  # noqa: E402


@pytest.fixture
def calls(monkeypatch):
    log: list = []
    fake = types.ModuleType("learn")
    fake.sync = lambda *a, **k: log.append(("sync", a, k)) or []
    fake.spawn = lambda *a, **k: log.append(("spawn", a, k))
    fake.sealer = lambda *a, **k: (lambda: log.append(("seal", a, k)))
    monkeypatch.setitem(sys.modules, "learn", fake)
    return log


@pytest.fixture
def broken(monkeypatch):
    fake = types.ModuleType("learn")

    def boom(*a, **k):
        raise OSError("learn is broken")
    fake.sync = fake.spawn = boom
    monkeypatch.setitem(sys.modules, "learn", fake)


def test_checkpoint_spawns_sync(calls):
    checkpoint.spawn_learn("plan.md")
    assert calls == [("spawn", (Path(".superlite"), "plan.md"), {})]
    src = inspect.getsource(checkpoint.checkpoint_one)
    assert src.index("append_ledger(") < src.index("spawn_learn(") < src.index("launch_probes(")


def test_prepare_run_syncs_before_snapshot(calls, monkeypatch, tmp_path):
    monkeypatch.setattr(session_registration.run_archive, "ensure_run",
                        lambda live, topic, slug: calls.append(("ensure",)) or ({}, None, None))
    session_registration.prepare_run(tmp_path, tmp_path / ".superlite", "topic", "slug")
    assert [c[0] for c in calls] == ["sync", "ensure"]
    assert calls[0][1] == (tmp_path / ".superlite",)


def test_finish_syncs_before_archive(calls):
    finish.learn_sync(Path("sl"), "plan.md", "rid-1")
    assert calls == [("sync", (Path("sl"), "plan.md"), {"run": "rid-1", "status": "finished", "final": True})]
    src = inspect.getsource(finish._finish_locked)
    assert src.index("learn_sync(") < src.index("archive_run(")
    assert "run_tests.summary_path(sl_dir)" in inspect.getsource(finish.main)


def test_close_syncs(calls):
    seal = session_close.learn_sync(Path("live"))
    assert calls == [("sync", (Path("live"),), {"status": "finished", "report": True})]  # not final: no seal yet
    session_close.learn_seal(seal)
    assert calls[-1] == ("seal", (Path("live"),), {})
    for fn in (session_close._close_host, session_close._close_owned, session_close._close_adopted):
        src = inspect.getsource(fn)  # sealed only after the close transaction succeeded
        assert src.index("learn_sync(live)") < src.index("park_run_state(") < src.index("except JournalError") \
            < src.index("learn_seal(seal)")


def test_sync_error_is_advisory(broken, tmp_path):
    checkpoint.spawn_learn("p.md")
    session_close.learn_sync(tmp_path)
    finish.learn_sync(tmp_path, None, "r")
    session_registration.learn_sync(tmp_path)


def test_keep_has_lessons():
    assert {"lessons.json", "learn-state.json", "runs"} <= run_archive.KEEP


def test_finish_syncs_before_report():
    src = inspect.getsource(finish._finish_locked)
    assert src.index("learn_sync(") < src.index("summary(")


def test_history_section_keeps_enriched_rows(monkeypatch, tmp_path):
    import history
    monkeypatch.setattr(history, "home", lambda: tmp_path / "home")
    sl = tmp_path / ".superlite"
    sl.mkdir()
    plain = {"v": 1, "repository": history.repository_id(sl), "run": "r1", "task": "T1", "first_time_pass": True,
             "date": "2026-01-01"}
    monkeypatch.setattr(history, "run_rows", lambda plan, sl_dir, rid=None: [dict(plain)])
    history.record_rows([dict(plain, status="finished", findings=3)], sl)
    assert finish.record_run_keeping_outcomes(history, "plan.md", sl, "r1") == []
    rows = history.read_rows()
    assert len(rows) == 1 and rows[0]["status"] == "finished" and rows[0]["findings"] == 3


def test_history_section_reports_rows_when_sync_recorded(monkeypatch, tmp_path):
    import history
    monkeypatch.setattr(history, "home", lambda: tmp_path / "home")
    sl = tmp_path / ".superlite"
    sl.mkdir()
    (sl / "metrics.jsonl").write_text("", encoding="utf-8")
    plain = {"v": 1, "repository": history.repository_id(sl), "run": "r1", "task": "T1", "first_time_pass": True,
             "date": "2026-01-01"}
    monkeypatch.setattr(history, "run_rows", lambda plan, sl_dir, rid=None: [dict(plain)])
    history.record_rows([dict(plain, status="finished", findings=0)], sl)
    assert finish.history_section("plan.md", sl, "r1").startswith("history: 1 rows appended")
    assert len(history.read_rows()) == 1


def test_learning_files_are_stationary(tmp_path):
    import session_registry
    names = ("lessons.json", "lessons.json.lock", "learn-state.json", "learn-state.json.lock")
    assert set(names) <= session_registry.STATIONARY
    for n in (*names, "progress.md"):
        (tmp_path / n).write_text("x", encoding="utf-8")
    assert [p.name for p in session_registry.run_entries(tmp_path)] == ["progress.md"]
