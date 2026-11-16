import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/swarm/scripts"))
import cloud_entries as ce  # noqa: E402

RUN = {"name": "login_fix", "owner": "chris", "status": "running", "step": 14, "agent": "claude"}


def test_frontmatter(tmp_path):
    assert ce.sync([RUN], tmp_path, "chris", False) == [
        "superlite-attach-login_fix", "superlite-watch-login_fix"]
    text = (tmp_path / "superlite-attach-login_fix/SKILL.md").read_text(encoding="utf-8")
    parts = text.split("---\n")
    assert parts[0] == "" and len(parts) == 3
    fm = dict(line.split(": ", 1) for line in parts[1].splitlines())
    assert fm == {"name": "superlite:attach-login_fix",
                  "description": "running \u00b7 step 14 \u00b7 claude",
                  "disable-model-invocation": "true"}
    assert (tmp_path / "superlite-watch-login_fix/.superlite-entry").exists()


def test_admin_prefix(tmp_path):
    assert ce.sync([RUN], tmp_path, "sam", True) == [
        "superlite-attach-chris.login_fix", "superlite-watch-chris.login_fix"]
    assert ce.sync([RUN], tmp_path / "x", "sam", False) == []


def test_marker_only(tmp_path):
    d = tmp_path / "superlite-attach-x"
    d.mkdir()
    (d / "SKILL.md").write_text("mine", encoding="utf-8")
    ce.sync([], tmp_path, "chris", False)
    assert d.exists()
    assert ce.remove_all(tmp_path) == 0
    assert d.exists()


def test_ended_removed(tmp_path):
    ce.sync([RUN], tmp_path, "chris", False)
    ce.sync([], tmp_path, "chris", False)
    assert not list(tmp_path.iterdir())


def test_name_safe(tmp_path):
    bad = dict(RUN, name="../x")
    assert ce.sync([bad], tmp_path, "chris", False) == []
    assert not list(tmp_path.iterdir())
    assert not (tmp_path.parent / "x").exists()


def test_remove_all_count(tmp_path):
    ce.sync([RUN], tmp_path, "chris", False)
    assert ce.remove_all(tmp_path) == 2
    assert not list(tmp_path.iterdir())
    assert ce.remove_all(tmp_path / "missing") == 0
