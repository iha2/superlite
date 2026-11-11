"""finish archives the run into <hub>/.superlite/runs/<id> after the summary (run_archive faked unless SUPERLITE_REAL=1)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import finish  # noqa: E402

KEEP = {"test-cmd.json", "sessions.json", "peers.json", "agent-specs.json", "host-models.json"}
ARGS = ["--ledger", ".superlite/progress.md", "--log", ".superlite/incidents.jsonl"]


STAMP = "20261110-140309"


def fake_module(calls, slug_box):
    m = types.ModuleType("run_archive")

    def run_id(slug, now, unfinished=False):
        return f"{STAMP}-{slug or 'run'}{'-unfinished' if unfinished else ''}"

    def current_slug(checkout):
        return slug_box[0]

    def reserve(sl, slug=None, unfinished=False, now=None, rid=None):
        files = [p for p in Path(sl).iterdir() if p.is_file() and p.name not in KEEP and not p.name.endswith(".lock")]
        if not files:
            return None
        root = Path(sl) / "runs"
        root.mkdir(parents=True, exist_ok=True)
        candidate, n = rid or run_id(slug, now), 1
        while True:
            dest = root / candidate
            try:
                dest.mkdir()
                return dest
            except FileExistsError:
                n += 1
                candidate = f"{rid or run_id(slug, now)}-{n}"

    def cancel_reservation(dest):
        if dest:
            try:
                Path(dest).rmdir()
            except OSError:
                pass

    def read_current_run(sl):
        try:
            return json.loads((Path(sl) / "current-run.json").read_text())
        except (OSError, ValueError):
            return None

    def write_current_run(sl, marker):
        (Path(sl) / "current-run.json").write_text(json.dumps(marker) + "\n")

    def archive(sl, slug=None, unfinished=False, plan=None, now=None, rid=None, reserved=None):
        calls.append((Path(sl), slug, plan, rid))
        files = sorted(p.name for p in Path(sl).iterdir() if p.is_file() and p.name not in KEEP and not p.name.endswith(".lock"))
        if not files:
            cancel_reservation(reserved)
            return None
        dest = Path(reserved) if reserved else reserve(sl, slug, now=now, rid=rid)
        for f in files:
            (Path(sl) / f).replace(dest / f)
        (dest / "run.json").write_text(json.dumps({"id": dest.name, "slug": slug, "plan": plan,
                                                   "status": "finished", "files": files}))
        return dest

    m.run_id, m.current_slug, m.reserve, m.cancel_reservation, m.archive = run_id, current_slug, reserve, cancel_reservation, archive
    m.read_current_run, m.write_current_run = read_current_run, write_current_run
    return m


@pytest.fixture
def slug_box():
    return [None]


@pytest.fixture
def calls(monkeypatch, slug_box):
    c = []
    if os.environ.get("SUPERLITE_REAL") != "1":
        monkeypatch.setitem(sys.modules, "run_archive", fake_module(c, slug_box))
    return c


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
    sl = r / ".superlite"
    sl.mkdir()
    (sl / "progress.md").write_text("Ruling: a - b - c\n")
    (sl / "incidents.jsonl").write_text("")
    monkeypatch.chdir(r)
    return r


def run_main(capsys, args=ARGS):
    assert finish.main(args) == 0
    return capsys.readouterr()


def runs_of(r):
    d = r / ".superlite" / "runs"
    return sorted(d.iterdir()) if d.is_dir() else []


def test_finish_archives(repo, calls, capsys):
    out = run_main(capsys).out
    assert "- a - b - c" in out
    (arch,) = runs_of(repo)
    assert out.rstrip("\n").splitlines()[-1] == f"run: archived to .superlite/runs/{arch.name}"
    assert not (repo / ".superlite" / "progress.md").exists()
    assert (arch / "progress.md").is_file() and (arch / "incidents.jsonl").is_file()
    assert json.loads((arch / "run.json").read_text())["status"] == "finished"


def test_plan_passed_to_run_json(repo, calls, capsys):
    run_main(capsys, ARGS + ["--plan", "P.md"])  # no metrics.jsonl: history records nothing
    assert calls[0][2] == "P.md" if calls else True
    assert json.loads((runs_of(repo)[0] / "run.json").read_text())["plan"] == "P.md"


def test_slug_from_session(repo, calls, capsys, slug_box):
    if os.environ.get("SUPERLITE_REAL") == "1":
        pytest.skip("the fake registry answers the slug")
    slug_box[0] = "export"
    run_main(capsys)
    assert runs_of(repo)[0].name == f"{STAMP}-export"
    assert calls[0][1] == "export"


def test_nothing_to_archive_no_line(repo, calls, capsys):
    (repo / ".superlite" / "progress.md").unlink()
    (repo / ".superlite" / "incidents.jsonl").unlink()
    (repo / ".superlite" / "test-cmd.json").write_text("{}")
    out = run_main(capsys).out
    assert "run: archived" not in out
    assert runs_of(repo) == []


def test_summary_unchanged(repo, calls, capsys):
    expected = finish.summary(".superlite/progress.md", ".superlite/incidents.jsonl", ".superlite/lint-log.jsonl")
    out = run_main(capsys).out
    assert out.startswith(expected)
    extra = out[len(expected):].splitlines()
    assert extra and extra[-1].startswith("run: archived to ")
    assert all(not ln.startswith("run: ") for ln in extra[:-1])


def test_release_before_archive(repo, calls, capsys, monkeypatch):
    import exclusive
    (repo / ".superlite" / "peers.json").write_text("{}")
    order = []
    real = exclusive.release

    def release(sl):
        order.append(("release", (Path(sl) / "peers.json").is_file()))
        return real(sl)

    monkeypatch.setattr(exclusive, "release", release)
    ra = sys.modules["run_archive"] if "run_archive" in sys.modules else __import__("run_archive")
    real_archive = ra.archive

    def spy(*a, **k):
        order.append(("archive", None))
        return real_archive(*a, **k)

    monkeypatch.setattr(ra, "archive", spy)
    run_main(capsys)
    assert order == [("release", True), ("archive", None)]


def test_non_superlite_ledger_dir_not_archived(tmp_path, calls, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "progress.md").write_text("Ruling: x\n")
    out = run_main(capsys, ["--ledger", str(tmp_path / "progress.md"), "--log", str(tmp_path / "i.jsonl")]).out
    assert "run: " not in out
    assert (tmp_path / "progress.md").is_file()
    assert not (tmp_path / ".superlite" / "runs").exists()


def test_archive_error_exits_zero(repo, calls, capsys, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")

    ra = sys.modules["run_archive"] if "run_archive" in sys.modules else __import__("run_archive")
    monkeypatch.setattr(ra, "archive", boom)
    cap = run_main(capsys)
    assert "run: not archived (disk full)" in cap.err
    assert "run: archived" not in cap.out


def test_widget_follows_archive(repo, calls, capsys, monkeypatch):
    import run_widget

    def card(plan, root, ledger=None):
        (Path(root) / run_widget.AFTER).write_text("<html></html>", encoding="utf-8")
        return run_widget.widget_line(Path(root) / run_widget.AFTER, Path.cwd())

    monkeypatch.setattr(finish, "card_line", card)
    out = run_main(capsys, ARGS + ["--plan", "P.md"]).out
    (arch,) = runs_of(repo)
    assert out.rstrip("\n").splitlines()[-1].startswith(f"widget: .superlite/runs/{arch.name}/{run_widget.AFTER} ")
    assert (arch / run_widget.AFTER).is_file()
    assert not (repo / ".superlite" / run_widget.AFTER).exists()


def test_history_run_is_archive_id(repo, calls, capsys, monkeypatch):
    import history
    seen = []

    def record_run(plan, sl_dir=".superlite", run_id=None):
        seen.append(run_id)
        return []

    monkeypatch.setattr(history, "record_run", record_run)
    (repo / ".superlite" / "metrics.jsonl").write_text("")
    (repo / "P.md").write_text("x\n")
    run_main(capsys, ARGS + ["--plan", "P.md"])
    (arch,) = runs_of(repo)
    assert seen == [arch.name] and calls[0][3] == arch.name


def test_history_uses_collision_suffix_before_recording(repo, calls, capsys, monkeypatch):
    import history
    seen = []

    def record_run(plan, sl_dir=".superlite", run_id=None):
        seen.append(run_id)
        return []

    monkeypatch.setattr(history, "record_run", record_run)
    (repo / ".superlite" / "metrics.jsonl").write_text("")
    (repo / "P.md").write_text("x\n")
    (repo / ".superlite" / "runs").mkdir()
    (repo / ".superlite" / "runs" / f"{STAMP}-run").mkdir()
    (repo / ".superlite" / "current-run.json").write_text(json.dumps(
        {"id": f"{STAMP}-run", "topic": "run", "slug": "run", "started_at": "2026-11-10T14:03:09"}))
    run_main(capsys, ARGS + ["--plan", "P.md"])
    arch = runs_of(repo)[-1]
    assert arch.name == f"{STAMP}-run-2"
    assert seen == [arch.name] and calls[0][3] == arch.name
    marker = json.loads((arch / "current-run.json").read_text())
    assert marker["id"] == arch.name
    assert json.loads((arch / "run.json").read_text())["id"] == arch.name


def test_finish_uses_marker_id_for_archive_and_history(repo, calls, capsys, monkeypatch):
    import history
    seen = []

    def record_run(plan, sl_dir=".superlite", run_id=None):
        seen.append(run_id)
        return []

    monkeypatch.setattr(history, "record_run", record_run)
    (repo / ".superlite" / "metrics.jsonl").write_text("")
    (repo / "P.md").write_text("x\n")
    marker = {"id": f"{STAMP}-topic", "topic": "topic", "slug": "topic", "started_at": "2026-11-10T14:03:09"}
    (repo / ".superlite" / "current-run.json").write_text(json.dumps(marker) + "\n")
    run_main(capsys, ARGS + ["--plan", "P.md"])
    (arch,) = runs_of(repo)
    assert arch.name == marker["id"]
    assert seen == [marker["id"]]
    assert json.loads((arch / "current-run.json").read_text())["id"] == marker["id"]
    assert json.loads((arch / "run.json").read_text())["id"] == marker["id"]
