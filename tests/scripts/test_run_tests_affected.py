"""run_tests --affected / --files / --reuse with testcmd selection and tree_id faked."""
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import run_tests  # noqa: E402
import testcmd  # noqa: E402

TREE = "3f9a0c1d2e4b5a69"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "a.py").write_text("def a():\n    return 1\n")
    (tmp_path / "tests" / "test_a.py").write_text("def test_a():\n    assert 1 == 1\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(run_tests, "current_tree", lambda: TREE)
    return tmp_path


def fake(monkeypatch, changed, sel):
    monkeypatch.setattr(testcmd, "changed_files", lambda root, base: changed, raising=False)
    monkeypatch.setattr(testcmd, "select_affected", lambda root, ch: sel, raising=False)


def go(*args):
    rc = run_tests.main(["--summary", "s.json", "--log", "l.jsonl", "--ledger", "p.md", *args])
    return rc, json.loads(Path("s.json").read_text())


def test_affected_runs_selected(repo, monkeypatch):
    fake(monkeypatch, ["src/a.py"], {"full": False, "selected": ["tests/test_a.py"],
                                     "reasons": {"tests/test_a.py": "related"}, "untested": []})
    rc, s = go("--affected")
    assert rc == 0 and s["passed"] == 1
    assert s["mode"] == "affected" and s["selected"] == ["tests/test_a.py"]
    assert s["reason"] == "related" and s["changed_count"] == 1 and s["tree"] == TREE


def test_affected_nothing(repo, monkeypatch, capsys):
    fake(monkeypatch, ["README.md"], {"full": False, "selected": [], "reasons": {}, "untested": []})
    rc, s = go("--affected")
    assert rc == 0 and "0 0 0 0.0" in capsys.readouterr().out
    assert s["selected"] == [] and s["untested"] == []


def test_affected_trigger_full(repo, monkeypatch):
    (repo / "conftest.py").write_text("")
    fake(monkeypatch, ["conftest.py"], {"full": True, "selected": [], "reasons": {}, "untested": [],
                                        "trigger": "conftest.py"})
    rc, s = go("--affected")
    assert rc == 0 and s["passed"] == 1 and s["reason"] == "full: conftest.py"


def test_files_flag(repo, monkeypatch):
    def boom(root, base):
        raise AssertionError("git changes must be ignored")
    monkeypatch.setattr(testcmd, "changed_files", boom, raising=False)
    seen = []

    def sel(root, ch):
        seen.append(ch)
        return {"full": False, "selected": ["tests/test_a.py"], "reasons": {"tests/test_a.py": "mention"}, "untested": []}
    monkeypatch.setattr(testcmd, "select_affected", sel, raising=False)
    rc, s = go("--affected", "--files", "src/a.py")
    assert rc == 0 and seen == [["src/a.py"]] and s["reason"] == "mention"


def test_reuse_hit(repo, capsys):
    Path("s.json").write_text(json.dumps({"mode": "full", "passed": 3, "failed": 0, "tree": TREE}))
    rc = run_tests.main(["--summary", "s.json", "--full", "--reuse"])
    assert rc == 0 and "reused: s.json" in capsys.readouterr().out


def test_reuse_miss(repo):
    Path("s.json").write_text(json.dumps({"mode": "full", "passed": 3, "failed": 0, "tree": "other"}))
    rc, s = go("--full", "--reuse")
    assert rc == 0 and s["passed"] == 1 and s["tree"] == TREE


def test_fast_unchanged(repo):
    rc, s = go("--fast", "tests/test_a.py")
    assert rc == 0 and s["mode"] == "fast" and "tree" not in s


def test_affected_selected_not_remapped(repo, monkeypatch):
    def boom(root, src):
        raise FileNotFoundError(f"no tests found for {src}")
    monkeypatch.setattr(testcmd, "resolve_related", boom)
    fake(monkeypatch, ["tests/test_a.py"], {"full": False, "selected": ["tests/test_a.py"],
                                            "reasons": {"tests/test_a.py": "changed"}, "untested": []})
    rc, s = go("--affected")
    assert rc == 0 and s["passed"] == 1 and s["selected"] == ["tests/test_a.py"]


def test_wait_run_id_reads_own_run(repo, capsys):
    older, newer = "20260101T000000-1-aaaaaa", "20260101T000001-2-bbbbbb"
    for rid, passed in ((older, 3), (newer, 7)):
        Path(f"s-{rid}.json").write_text(json.dumps({"passed": passed, "failed": 0, "skipped": 0, "run_id": rid}))
    Path("s.latest").write_text(json.dumps({"id": newer, "summary": str(Path(f"s-{newer}.json").resolve())}))
    assert run_tests.main(["--summary", "s.json", "--wait", "--run", older, "--timeout", "2"]) == 0
    assert json.loads(capsys.readouterr().out)["passed"] == 3
    assert run_tests.main(["--summary", "s.json", "--wait", "--timeout", "2"]) == 0
    assert json.loads(capsys.readouterr().out)["passed"] == 7


def test_background_prints_wait_line(repo, monkeypatch, capsys):
    monkeypatch.setattr(run_tests.base, "spawn_background", lambda *a, **k: None)
    assert run_tests.main(["--summary", "s.json", "--fast", "tests", "--background"]) == 0
    out = capsys.readouterr().out.splitlines()
    rid = json.loads(Path("s.latest").read_text())["id"]
    assert f"wait: run_tests --wait --run {rid}" in out


def test_summary_line_stays_last(repo, monkeypatch, capsys):
    monkeypatch.setattr(run_tests.base, "spawn_background", lambda *a, **k: None)
    assert run_tests.main(["--summary", "s.json", "--fast", "tests", "--background"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[-1].startswith("summary: ")
    assert out[0].startswith("wait: run_tests --wait --run ")


def test_summary_has_run_id(repo, monkeypatch):
    fake(monkeypatch, ["src/a.py"], {"full": False, "selected": ["tests/test_a.py"],
                                     "reasons": {"tests/test_a.py": "related"}, "untested": []})
    rc, s = go("--affected")
    assert rc == 0 and s["run_id"]
    rc, s = go("--affected", "--run-id", "RID-1", "--publish", "pub.json")
    assert s["run_id"] == "RID-1"
    assert json.loads(Path("pub.json").read_text())["run_id"] == "RID-1"


def test_wait_run_id_mismatch(repo, capsys):
    Path("s-X1.json").write_text(json.dumps({"passed": 1, "failed": 0, "skipped": 0, "run_id": "OTHER"}))
    assert run_tests.main(["--summary", "s.json", "--wait", "--run", "X1", "--timeout", "2"]) == 2
    assert "run id mismatch" in capsys.readouterr().err
    Path("s-X2.json").write_text(json.dumps({"passed": 1, "failed": 0, "skipped": 0}))
    assert run_tests.main(["--summary", "s.json", "--wait", "--run", "X2", "--timeout", "2"]) == 2


@pytest.mark.parametrize(("mode", "failed", "expected"), [
    ("affected", 0, 0),
    ("affected", 1, 1),
    ("full", 0, 1),
    ("fast", 0, 1),
])
def test_wait_empty_selection_success_only_for_affected(repo, mode, failed, expected):
    rid = "EMPTY-1"
    Path(f"s-{rid}.json").write_text(json.dumps({
        "mode": mode, "selected": [], "reason": "", "passed": 0, "failed": failed,
        "skipped": 0, "run_id": rid,
    }))
    assert run_tests.main(["--summary", "s.json", "--wait", "--run", rid, "--timeout", "2"]) == expected
