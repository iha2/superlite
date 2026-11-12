"""finish and run_archive keep an open prototype's feature state live (real run_archive, temp git repo)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import finish  # noqa: E402
import run_archive  # noqa: E402
import state  # noqa: E402

ARGS = ["--ledger", f"{state.DIR_NAME}/progress.md", "--log", f"{state.DIR_NAME}/incidents.jsonl"]


def git(cwd, *a):
    subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = tmp_path.resolve() / "app"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "t")
    (r / "a.txt").write_text("a\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    sl = r / state.DIR_NAME
    sl.mkdir()
    (sl / "progress.md").write_text("Ruling: a - b - c\n")
    (sl / "incidents.jsonl").write_text("")
    (sl / "report.json").write_text("{}\n")
    (sl / "stable.json").write_text("{}\n")
    monkeypatch.chdir(r)
    return r


def feature(sl: Path, status: str, sandbox: str | None = None) -> None:
    sb = sandbox or (sl.parent / state.DIR_NAME / "sandboxes" / "host" / "x").as_posix()
    (sl / "feature.json").write_text(json.dumps({"v": 1, "topic": "x", "status": status, "sandbox": sb}))
    (sl / "orchestrator.json").write_text('{"mode": "human"}\n')


def runs_of(r):
    d = r / state.DIR_NAME / "runs"
    return sorted(d.iterdir()) if d.is_dir() else []


def test_feature_keep_values(repo):
    sl = repo / state.DIR_NAME
    assert run_archive.feature_keep(sl) == set()
    for status in ("building", "tryout", "approved"):
        feature(sl, status)
        assert run_archive.feature_keep(sl) == {"feature.json", "orchestrator.json"}
    feature(sl, "discarded")
    assert run_archive.feature_keep(sl) == set()
    (sl / "feature.json").write_text("not json")
    assert run_archive.feature_keep(sl) == set()


def test_finish_with_open_proto_keeps_feature_state(repo, capsys):
    sl = repo / state.DIR_NAME
    feature(sl, "building")
    assert finish.main(ARGS) == 0
    (arch,) = runs_of(repo)
    for name in ("progress.md", "report.json", "stable.json"):
        assert (arch / name).is_file() and not (sl / name).exists()
    assert (sl / "feature.json").is_file() and (sl / "orchestrator.json").is_file()
    assert not (arch / "feature.json").exists() and not (arch / "orchestrator.json").exists()
    assert "feature.json" not in json.loads((arch / "run.json").read_text())["files"]


def test_finish_without_proto_unchanged(repo, capsys):
    sl = repo / state.DIR_NAME
    (sl / "orchestrator.json").write_text('{"mode": "human"}\n')
    assert finish.main(ARGS) == 0
    (arch,) = runs_of(repo)
    assert (arch / "progress.md").is_file() and (arch / "orchestrator.json").is_file()
    assert not (sl / "progress.md").exists() and not (sl / "orchestrator.json").exists()
    meta = json.loads((arch / "run.json").read_text())
    assert "sandbox" not in meta and meta["status"] == "finished"


def test_archive_keep_only_returns_none(repo):
    sl = repo / state.DIR_NAME
    for p in list(sl.iterdir()):
        p.unlink()
    feature(sl, "building")
    keep = run_archive.feature_keep(sl)
    assert run_archive.archive(sl, "s", keep=tuple(keep)) is None
    assert run_archive.reserve(sl, "s", keep=tuple(keep)) is None
    assert runs_of(repo) == []
    assert (sl / "feature.json").is_file()


def test_run_json_has_sandbox(repo):
    sl = repo / state.DIR_NAME
    feature(sl, "tryout")
    dest = run_archive.archive(sl, "s", rid="20261111-100000-s", keep=tuple(run_archive.feature_keep(sl)))
    meta = json.loads((dest / "run.json").read_text())
    assert meta["sandbox"] == f"{state.DIR_NAME}/sandboxes/host/x"
    assert meta["run_id"] == "20261111-100000-s" == meta["id"]
