import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"))
import run_archive  # noqa: E402
import session_registry  # noqa: E402

NOW = datetime(2026, 11, 10, 14, 3, 9)


def git(*a, cwd):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


def repo(tmp_path, name="repo"):
    r = tmp_path / name
    r.mkdir()
    git("init", "-q", cwd=r)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "i", cwd=r)
    return r


def live(r, **files):
    sl = r / ".superlite"
    sl.mkdir(exist_ok=True)
    for n, c in files.items():
        (sl / n.replace("__", ".")).write_text(c)
    return sl


def test_run_id_format():
    assert run_archive.run_id("export", NOW) == "20261110-140309-export"


def test_run_id_unfinished_no_slug():
    assert run_archive.run_id(None, NOW, unfinished=True) == "20261110-140309-run-unfinished"


def test_archive_moves_run_files(tmp_path):
    r = repo(tmp_path)
    sl = live(r, progress__md="p", metrics__jsonl="m", test_cmd="x")
    (sl / "test-cmd.json").write_text("{}")
    (sl / "test-map.json").write_text("{}")  # project config, like test-cmd.json: stays live
    (sl / "sessions.json").write_text("{}")
    (sl / "test_cmd").unlink()
    dest = run_archive.archive(sl, "export", now=NOW)
    assert dest == r.resolve() / ".superlite" / "runs" / "20261110-140309-export" or dest == r / ".superlite" / "runs" / "20261110-140309-export"
    assert sorted(p.name for p in dest.iterdir()) == ["metrics.jsonl", "progress.md", "run.json"]
    meta = json.loads((dest / "run.json").read_text())
    assert meta["id"] == "20261110-140309-export" and meta["slug"] == "export"
    assert meta["status"] == "finished" and meta["plan"] is None
    assert meta["files"] == ["metrics.jsonl", "progress.md"]
    assert meta["checkout"] == Path(meta["checkout"]).as_posix()
    assert meta["archived_at"] == "2026-11-10T14:03:09"
    assert sorted(p.name for p in sl.iterdir() if p.name != "runs") == ["sessions.json", "test-cmd.json", "test-map.json"]


def test_sandbox_archive_keeps_stable_feature_run_id(tmp_path):
    r = repo(tmp_path)
    sl = live(r, progress__md="p")
    sb = r / ".superlite" / "sandboxes" / "host" / "export"
    (sl / "feature.json").write_text(json.dumps({
        "sandbox": sb.as_posix(), "status": "building", "run_id": "stable-id",
    }))
    dest = run_archive.archive(sl, "export", unfinished=True, now=NOW, rid="stable-id-unfinished",
                               keep=("feature.json",), checkout=r)
    meta = json.loads((dest / "run.json").read_text())
    assert meta["id"] == "stable-id-unfinished" and meta["run_id"] == "stable-id"


def test_archive_nothing_returns_none(tmp_path):
    r = repo(tmp_path)
    sl = live(r)
    (sl / "test-cmd.json").write_text("{}")
    assert run_archive.archive(sl, now=NOW) is None
    assert not (sl / "runs").exists()


def test_unfinished_status(tmp_path):
    r = repo(tmp_path)
    sl = live(r, progress__md="p")
    dest = run_archive.archive(sl, "x", unfinished=True, now=NOW)
    assert dest.name == "20261110-140309-x-unfinished"
    assert json.loads((dest / "run.json").read_text())["status"] == "unfinished"


def test_copy_preserves_live_files_and_archives_independent_snapshot(tmp_path):
    r = repo(tmp_path)
    sl = live(r, progress__md="p")
    (sl / "nested").mkdir()
    (sl / "nested" / "metrics.jsonl").write_text("m")
    dest = run_archive.archive(sl, "x", unfinished=True, now=NOW, copy=True)
    (sl / "progress.md").write_text("continued")
    (sl / "nested" / "metrics.jsonl").write_text("more")
    assert (dest / "progress.md").read_text() == "p"
    assert (dest / "nested" / "metrics.jsonl").read_text() == "m"
    assert json.loads((dest / "run.json").read_text())["status"] == "unfinished"


@pytest.mark.parametrize("failure", ["copy", "manifest"])
def test_copy_failure_removes_partial_archive(tmp_path, monkeypatch, failure):
    sl = tmp_path / ".superlite"
    sl.mkdir()
    (sl / "progress.md").write_text("p")

    def fail(*args, **_kwargs):
        if failure == "copy":
            args[1].write_text("partial")
        raise OSError("snapshot failed")

    if failure == "copy":
        monkeypatch.setattr(run_archive, "_copy_entry", fail)
    else:
        monkeypatch.setattr(run_archive.os, "replace", fail)
    with pytest.raises(OSError, match="snapshot failed"):
        run_archive.archive(sl, "s", now=NOW, copy=True)
    assert (sl / "progress.md").read_text() == "p"
    assert not list((sl / "runs").iterdir())


def test_ensure_run_writes_marker_without_copy_on_clean_start(tmp_path):
    sl = tmp_path / ".superlite"
    marker, dest, previous = run_archive.ensure_run(sl, "topic", "topic", now=NOW)
    assert dest is None and previous is None
    assert marker == {"id": "20261110-140309-topic", "topic": "topic", "slug": "topic",
                      "started_at": "2026-11-10T14:03:09"}
    assert run_archive.read_current_run(sl) == marker


def test_ensure_run_same_topic_is_idempotent(tmp_path):
    sl = tmp_path / ".superlite"
    marker, dest, _ = run_archive.ensure_run(sl, "topic", "topic", now=NOW)
    assert dest is None
    (sl / "progress.md").write_text("continued")
    repeated, repeated_dest, _ = run_archive.ensure_run(sl, "topic", "topic", now=NOW)
    assert repeated == marker and dest is None and repeated_dest is None
    assert (sl / "progress.md").read_text() == "continued"
    assert not (sl / "runs").exists()


def test_ensure_run_legacy_orchestrator_is_not_copied(tmp_path):
    sl = live(tmp_path, orchestrator__json='{"mode":"human"}')
    marker, dest, _ = run_archive.ensure_run(sl, "topic", "topic", now=NOW)
    assert dest is None
    assert (sl / "orchestrator.json").is_file()
    assert run_archive.read_current_run(sl) == marker


def test_plan_recorded(tmp_path):
    r = repo(tmp_path)
    sl = live(r, progress__md="p")
    dest = run_archive.archive(sl, plan="docs/plans/x.md", now=NOW)
    assert json.loads((dest / "run.json").read_text())["plan"] == "docs/plans/x.md"
    assert dest.name == "20261110-140309-run"


def test_linked_worktree_archives_to_hub(tmp_path):
    r = repo(tmp_path)
    wt = tmp_path / "wt"
    git("worktree", "add", "-q", "-b", "b", str(wt), cwd=r)
    sl = live(wt, progress__md="p")
    dest = run_archive.archive(sl, "w", now=NOW)
    assert dest.parent.resolve() == (r / ".superlite" / "runs").resolve()
    assert (dest / "progress.md").read_text() == "p"
    assert not (sl / "progress.md").exists()


def test_id_clash_suffix(tmp_path):
    r = repo(tmp_path)
    sl = live(r, progress__md="1")
    a = run_archive.archive(sl, "s", now=NOW)
    (sl / "progress.md").write_text("2")
    b = run_archive.archive(sl, "s", now=NOW)
    (sl / "progress.md").write_text("3")
    c = run_archive.archive(sl, "s", now=NOW)
    assert (a.name, b.name, c.name) == ("20261110-140309-s", "20261110-140309-s-2", "20261110-140309-s-3")
    assert json.loads((b / "run.json").read_text())["id"] == b.name


def test_keeps_locks_and_research(tmp_path):
    r = repo(tmp_path)
    sl = live(r, progress__md="p", x__lock="", peers__json="{}")
    (sl / "research" / "a").mkdir(parents=True)
    (sl / "research" / "a" / "ledger.json").write_text("{}")
    for n in ("agent-specs.json", "host-models.json"):
        (sl / n).write_text("{}")
    dest = run_archive.archive(sl, now=NOW)
    assert [p.name for p in run_archive.entries(sl)] == ["progress.md"] or not (sl / "progress.md").exists()
    assert sorted(p.name for p in dest.iterdir()) == ["progress.md", "run.json"]
    for n in ("x.lock", "peers.json", "agent-specs.json", "host-models.json", "research/a/ledger.json"):
        assert (sl / n).exists()


def test_stationary_has_runs(tmp_path):
    assert "runs" in session_registry.STATIONARY
    sl = tmp_path / ".superlite"
    (sl / "runs").mkdir(parents=True)
    (sl / "progress.md").write_text("p")
    assert [p.name for p in session_registry.run_entries(sl)] == ["progress.md"]


def test_outside_git(tmp_path):
    d = tmp_path / "plain"
    sl = live(d.mkdir() or d, progress__md="p")
    dest = run_archive.archive(sl, now=NOW)
    assert dest.parent.resolve() == (d / ".superlite" / "runs").resolve()
    assert run_archive.runs_dir(d).resolve() == dest.parent.resolve()


def test_rid_overrides_id(tmp_path):
    r = repo(tmp_path)
    sl = live(r, progress__md="p")
    dest = run_archive.archive(sl, "s", now=NOW, rid="X1")
    assert dest.name == "X1"
    assert json.loads((dest / "run.json").read_text())["id"] == "X1"


def test_reserved_id_is_the_archive_id(tmp_path):
    r = repo(tmp_path)
    sl = live(r, progress__md="p")
    runs = r / ".superlite" / "runs"
    runs.mkdir(parents=True)
    (runs / "X1").mkdir()
    reserved = run_archive.reserve(sl, "s", now=NOW, rid="X1")
    assert reserved.name == "X1-2"
    dest = run_archive.archive(sl, "s", now=NOW, rid="X1", reserved=reserved)
    assert dest == reserved
    assert json.loads((dest / "run.json").read_text())["id"] == "X1-2"


def _register(r, slug):
    session_registry.write(r, {"sessions": [{"slug": slug}], "active": slug})


def test_current_slug_registered(tmp_path):
    r = repo(tmp_path)
    _register(r, "export")
    assert run_archive.current_slug(r) == "export"


def test_current_slug_none(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    assert run_archive.current_slug(d) is None
    assert run_archive.current_slug(tmp_path / "missing") is None


def test_current_slug_takes_no_lock(tmp_path):
    r = repo(tmp_path)
    _register(r, "export")
    with session_registry.registry_lock(r):
        assert run_archive.current_slug(r) == "export"


def test_restore_moves_back(tmp_path):
    r = repo(tmp_path)
    sl = live(r, progress__md="p")
    dest = run_archive.archive(sl, "s", now=NOW, rid="X1")
    run_archive.restore(dest, sl)
    assert (sl / "progress.md").read_text() == "p"
    assert not dest.exists()
    assert not (sl / "run.json").exists()


def test_partial_move_failure_rolls_back(tmp_path, monkeypatch):
    import shutil
    sl = tmp_path / ".superlite"
    sl.mkdir()
    for n in ("a.md", "b.md", "c.md"):
        (sl / n).write_text(n)
    real, calls = shutil.move, []

    def flaky(src, dst):
        calls.append(src)
        if len(calls) == 3 and Path(src).name == "c.md":
            raise OSError("boom")
        return real(src, dst)

    monkeypatch.setattr(run_archive.shutil, "move", flaky)
    try:
        run_archive.archive(sl, "s")
        raise AssertionError("expected OSError")
    except OSError:
        pass
    assert sorted(p.name for p in sl.iterdir() if p.name != "runs") == ["a.md", "b.md", "c.md"]
    assert not list((tmp_path / ".superlite" / "runs").iterdir())


def test_manifest_failure_rolls_back_all_files_and_metadata(tmp_path, monkeypatch):
    sl = tmp_path / ".superlite"
    sl.mkdir()
    (sl / "progress.md").write_text("p")

    def fail_replace(*_):
        raise OSError("manifest write failed")

    monkeypatch.setattr(run_archive.os, "replace", fail_replace)
    with pytest.raises(OSError, match="manifest write failed"):
        run_archive.archive(sl, "s", now=NOW)
    assert (sl / "progress.md").read_text() == "p"
    assert not list((sl / "runs").iterdir())
