"""testcmd affected-test selection: changed_files, is_full_trigger, select_affected."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import testcmd  # noqa: E402


def write(root: Path, rel: str, text: str = "") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_changed_test_selects_itself(tmp_path):
    write(tmp_path, "tests/scripts/test_x.py", "x")
    r = testcmd.select_affected(str(tmp_path), ["tests/scripts/test_x.py"])
    assert r == {"full": False, "selected": ["tests/scripts/test_x.py"], "reasons": {"tests/scripts/test_x.py": "changed"}, "untested": []}


def test_related_hit(tmp_path):
    write(tmp_path, "src/foo.py", "")
    write(tmp_path, "tests/test_foo.py", "pass")
    r = testcmd.select_affected(str(tmp_path), ["src/foo.py"])
    assert r["selected"] == ["tests/test_foo.py"] and r["reasons"] == {"tests/test_foo.py": "related"}


def test_mention_whole_word(tmp_path):
    write(tmp_path, "src/cost.py", "")
    write(tmp_path, "tests/test_a.py", "import cost\n")
    write(tmp_path, "tests/test_b.py", "costly = 1\n")
    r = testcmd.select_affected(str(tmp_path), ["src/cost.py"])
    assert r["selected"] == ["tests/test_a.py"]
    assert r["reasons"] == {"tests/test_a.py": "mention"}


def test_deleted_file_selects_mentions(tmp_path):
    write(tmp_path, "tests/test_a.py", "from gone import thing\n")
    r = testcmd.select_affected(str(tmp_path), ["src/gone.py"])
    assert r["selected"] == ["tests/test_a.py"] and r["untested"] == []


def test_untested_listed(tmp_path):
    write(tmp_path, "src/lonely.py", "")
    write(tmp_path, "tests/test_a.py", "nothing")
    r = testcmd.select_affected(str(tmp_path), ["src/lonely.py"])
    assert r["selected"] == [] and r["untested"] == ["src/lonely.py"] and r["full"] is False


def test_map_glob(tmp_path):
    write(tmp_path, "src/a.py", "")
    write(tmp_path, "tests/test_all.py", "")
    write(tmp_path, ".superlite/test-map.json", json.dumps({"src/*.py": ["tests/test_all.py"]}))
    r = testcmd.select_affected(str(tmp_path), ["src/a.py"])
    assert r["selected"] == ["tests/test_all.py"] and r["reasons"] == {"tests/test_all.py": "map"}
    assert r["untested"] == []


def test_map_value_glob(tmp_path):
    write(tmp_path, "src/a.py", "")
    write(tmp_path, "tests/test_one.py", "")
    write(tmp_path, ".superlite/test-map.json", json.dumps({"src/*.py": ["tests/test_*.py"]}))
    r = testcmd.select_affected(str(tmp_path), ["src/a.py"])
    assert r["selected"] == ["tests/test_one.py"]


def test_bad_map_ignored(tmp_path):
    write(tmp_path, "src/a.py", "")
    write(tmp_path, ".superlite/test-map.json", "{not json")
    r = testcmd.select_affected(str(tmp_path), ["src/a.py"])
    assert r["full"] is False and r["untested"] == ["src/a.py"]


def test_full_trigger_names(tmp_path):
    for name in ("pyproject.toml", "sub/conftest.py", "go.mod", "app/App.csproj", ".superlite/test-cmd.json", "uv.lock"):
        assert testcmd.is_full_trigger(name, str(tmp_path)), name
    assert not testcmd.is_full_trigger("src/a.py", str(tmp_path))
    assert testcmd.is_full_trigger("sub\\pyproject.toml", str(tmp_path))


def test_full_trigger_map_extra(tmp_path):
    write(tmp_path, ".superlite/test-map.json", json.dumps({"_full": ["db/*.sql"]}))
    r = testcmd.select_affected(str(tmp_path), ["src/a.py", "db/a.sql"])
    assert r["full"] is True and r["trigger"] == "db/a.sql" and r["selected"] == []


def test_full_trigger_result(tmp_path):
    r = testcmd.select_affected(str(tmp_path), ["pyproject.toml"])
    assert r == {"full": True, "selected": [], "reasons": {}, "untested": [], "trigger": "pyproject.toml"}


def test_non_python_suffix(tmp_path):
    write(tmp_path, "web/cart.ts", "")
    write(tmp_path, "web/cart.test.ts", "import { cart } from './cart'\n")
    r = testcmd.select_affected(str(tmp_path), ["web/cart.ts"])
    assert r["selected"] == ["web/cart.test.ts"]


def test_non_utf8_test_file_does_not_crash(tmp_path):
    write(tmp_path, "tests/test_ok.py", "cost\n")
    (tmp_path / "tests" / "test_bin.py").write_bytes(b"\xff\xfe cost \x00")
    r = testcmd.select_affected(str(tmp_path), ["src/cost.py"])
    assert "tests/test_ok.py" in r["selected"]


def test_skip_dirs_not_walked(tmp_path):
    write(tmp_path, "node_modules/x/test_dep.py", "cost")
    write(tmp_path, ".hidden/test_h.py", "cost")
    r = testcmd.select_affected(str(tmp_path), ["src/cost.py"])
    assert r["selected"] == []


@pytest.mark.skipif(not shutil.which("git"), reason="git missing")
def test_changed_files_untracked(tmp_path):
    def git(*a):
        subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=n", *a], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    write(tmp_path, ".gitignore", "ignored.txt\n")
    write(tmp_path, "tracked.py", "1")
    write(tmp_path, "sp ace/é.py", "1")
    git("add", ".")
    git("commit", "-qm", "i")
    write(tmp_path, "tracked.py", "2")
    write(tmp_path, "new.py", "x")
    write(tmp_path, "ignored.txt", "x")
    got = testcmd.changed_files(str(tmp_path), None)
    assert got == ["new.py", "tracked.py"]
    write(tmp_path, "sp ace/é.py", "2")
    assert "sp ace/é.py" in testcmd.changed_files(str(tmp_path), "HEAD")


def test_changed_files_not_a_repo(tmp_path):
    assert testcmd.changed_files(str(tmp_path), None) == []


def test_fixture_files_not_selected(tmp_path):
    write(tmp_path, "src/stem.py", "")
    write(tmp_path, "tests/test_stem.py", "import stem\n")
    write(tmp_path, "tests/fx/data.jsonl", "stem\n")
    write(tmp_path, "docs/x.spec.md", "mentions stem\n")
    r = testcmd.select_affected(str(tmp_path), ["src/stem.py", "tests/fx/data.jsonl"])
    assert r["selected"] == ["tests/test_stem.py"]


def test_testpaths_filter(tmp_path):
    write(tmp_path, "pyproject.toml", '[tool.pytest.ini_options]\ntestpaths = ["tests/scripts"]\n')
    write(tmp_path, "src/a.py", "")
    write(tmp_path, "tests/scripts/test_a.py", "import a\n")
    write(tmp_path, "bench/hidden/test_a.py", "import a\n")
    r = testcmd.select_affected(str(tmp_path), ["src/a.py"])
    assert r["selected"] == ["tests/scripts/test_a.py"]


def test_other_language_dropped_for_python(tmp_path):
    write(tmp_path, "src/a.py", "")
    write(tmp_path, "tests/test_a.py", "import a\n")
    write(tmp_path, "web/a.test.ts", "import a\n")
    r = testcmd.select_affected(str(tmp_path), ["src/a.py"])
    assert r["selected"] == ["tests/test_a.py"]


@pytest.mark.skipif(not shutil.which("git"), reason="git missing")
def test_changed_files_bad_base_raises(tmp_path):
    def git(*a):
        subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=n", *a], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    write(tmp_path, "a.py", "1")
    git("add", ".")
    git("commit", "-qm", "i")
    with pytest.raises(testcmd.BaseUnreadable):
        testcmd.changed_files(str(tmp_path), "0123456789abcdef0123456789abcdef01234567")


@pytest.mark.parametrize("marker, code, test", [
    ("Cargo.toml", "src/lib.rs", "tests/integration.rs"),
    ("app.csproj", "src/Cart.cs", "tests/CartTests.cs"),
])
def test_opaque_runner_code_change_runs_everything(tmp_path, marker, code, test):
    write(tmp_path, marker, "")
    write(tmp_path, code, "")
    write(tmp_path, test, "")
    r = testcmd.select_affected(str(tmp_path), [code])
    assert r["full"] is True and r["trigger"].endswith(code)


def test_opaque_runner_doc_change_selects_nothing(tmp_path):
    write(tmp_path, "Cargo.toml", "")
    write(tmp_path, "README.md", "")
    r = testcmd.select_affected(str(tmp_path), ["README.md"])
    assert r["full"] is False and r["selected"] == []


def test_go_selection_tests_packages(tmp_path):
    write(tmp_path, "go.mod", "module x\n")
    spec = testcmd.resolve(str(tmp_path), ["pkg/foo_test.go", "pkg/bar_test.go", "root_test.go"])
    assert spec["command"] == "go test ./pkg ."


def test_changed_test_still_selects_mapped_and_mentioning_tests(tmp_path):
    write(tmp_path, "tests/test_helpers.py", "")
    write(tmp_path, "tests/test_api.py", "x")
    write(tmp_path, "tests/test_ui.py", "from test_helpers import h\n")
    write(tmp_path, ".superlite/test-map.json", json.dumps({"tests/test_helpers.py": ["tests/test_api.py"]}))
    r = testcmd.select_affected(str(tmp_path), ["tests/test_helpers.py"])
    assert r["reasons"] == {"tests/test_api.py": "map", "tests/test_helpers.py": "changed", "tests/test_ui.py": "mention"}


def test_deleted_test_still_selects_its_mapped_tests(tmp_path):
    write(tmp_path, "tests/test_api.py", "x")
    write(tmp_path, ".superlite/test-map.json", json.dumps({"tests/test_helpers.py": ["tests/test_api.py"]}))
    r = testcmd.select_affected(str(tmp_path), ["tests/test_helpers.py"])
    assert r["selected"] == ["tests/test_api.py"] and r["untested"] == []


@pytest.mark.parametrize("ini, want", [
    ("[pytest]\ntestpaths = tests/unit\n    tests/integration\n", ["tests/unit", "tests/integration"]),
    ("[pytest]\ntestpaths =\n    tests/unit\n    # skipped\n    tests/integration\nother = 1\n", ["tests/unit", "tests/integration"]),
    ("[pytest]\ntestpaths = a b\n\n    c\n", ["a", "b"]),
])
def test_ini_testpaths_continuation_lines(tmp_path, ini, want):
    write(tmp_path, "pytest.ini", ini)
    assert testcmd.pytest_testpaths(str(tmp_path)) == want
