"""superlite.json overlay: load, cap, gitignore, hosts, prices, testprobe, fail closed."""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import dispatch  # noqa: E402
import gitignore  # noqa: E402
import host_models  # noqa: E402
import metrics  # noqa: E402
import plan_tools  # noqa: E402
import predict  # noqa: E402
import project_config  # noqa: E402
import testprobe  # noqa: E402


def _write(root: Path, obj) -> None:
    (root / "superlite.json").write_text(json.dumps(obj), encoding="utf-8", newline="\n")


def test_missing_file_is_empty(tmp_path):
    assert project_config.load(tmp_path) == {}
    assert project_config.effective_cap(None, tmp_path) == 10


def test_valid_overlay_and_explicit_cap_wins(tmp_path):
    _write(tmp_path, {"version": 1, "cap": 2})
    assert project_config.load(tmp_path)["cap"] == 2
    assert project_config.effective_cap(None, tmp_path) == 2
    assert project_config.effective_cap(10, tmp_path) == 10
    assert project_config.effective_cap(0, tmp_path) == 0


def test_bad_json_names_the_file(tmp_path):
    path = tmp_path / "superlite.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(project_config.ProjectConfigError) as exc:
        project_config.load(tmp_path)
    assert str(path.resolve()) in str(exc.value)
    assert "not valid JSON" in str(exc.value)


def test_unknown_key_and_bad_version(tmp_path):
    _write(tmp_path, {"version": 1, "skills": ["ask"]})
    with pytest.raises(project_config.ProjectConfigError, match="unknown key skills"):
        project_config.load(tmp_path)
    _write(tmp_path, {"version": 2, "cap": 2})
    with pytest.raises(project_config.ProjectConfigError, match="version must be 1"):
        project_config.load(tmp_path)


def test_dispatch_and_lint_fail_closed(tmp_path, monkeypatch, capsys):
    (tmp_path / "superlite.json").write_text("{", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    plan = tmp_path / "plan.md"
    plan.write_text("# Plan\n", encoding="utf-8")
    assert dispatch.main(["--plan", str(plan)]) == 2
    err = capsys.readouterr().err
    assert "superlite.json" in err and "not valid JSON" in err
    assert plan_tools.main(["lint", "--plan", str(plan)]) == 2
    err = capsys.readouterr().err
    assert "superlite.json" in err


def test_dispatch_uses_overlay_cap(tmp_path, monkeypatch):
    _write(tmp_path, {"version": 1, "cap": 2})
    monkeypatch.chdir(tmp_path)
    plan = tmp_path / "plan.md"
    plan.write_text("# Plan\n", encoding="utf-8")
    seen = {}

    def fake_run(*args, **kwargs):
        seen["cap"] = args[5]
        return [], {}

    monkeypatch.setattr(dispatch, "run", fake_run)
    monkeypatch.setitem(sys.modules, "orchestrator", types.SimpleNamespace(require=lambda *a, **k: None))
    assert dispatch.main(["--plan", str(plan)]) == 0
    assert seen["cap"] == 2
    assert dispatch.main(["--plan", str(plan), "--cap", "10"]) == 0
    assert seen["cap"] == 10


def test_gitignore_appends_overlay(tmp_path):
    _write(tmp_path, {"version": 1, "gitignore": ["generated/"]})
    added = gitignore.ensure(tmp_path, ["Python"])
    text = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert "generated/" in added and "generated/" in text
    assert ".superlite/" in text
    assert gitignore.ensure(tmp_path, ["Python"]) == []
    _write(tmp_path, {"version": 1, "gitignore": "generated/"})
    assert gitignore.project_extra(tmp_path) == []


def test_host_candidates_replace_one_job(tmp_path, monkeypatch):
    _write(tmp_path, {"version": 1, "hosts": {"cursor": {"build": {"candidates": ["only-slug"]}}}})
    monkeypatch.chdir(tmp_path)
    cfg = host_models.load_host_cfg("cursor")
    assert cfg["jobs"]["build"]["candidates"] == ["only-slug"]
    assert cfg["jobs"]["mechanical"]["candidates"][0] != "only-slug"


def test_model_price_tagged_project(tmp_path, monkeypatch):
    _write(tmp_path, {"version": 1, "models": {"haiku": {"input": 9}}})
    monkeypatch.chdir(tmp_path)
    data = predict.load_models()
    assert data["models"]["haiku"]["input"] == 9
    assert data["models"]["haiku"]["source"] == "project"
    shipped = predict.load_models(ROOT / "skills" / "swarm" / "config" / "models.json")
    assert shipped["models"]["haiku"]["input"] == 0.10
    assert "source" not in shipped["models"]["haiku"]
    prices = metrics.load_prices()
    assert prices["haiku"][0] == pytest.approx(9 / 1_000_000)


def test_testprobe_overlay_and_env_wins(tmp_path, monkeypatch):
    _write(tmp_path, {"version": 1, "testprobe": {"min_flat_s": 7}})
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SUPERLITE_TESTPROBE_CONFIG", raising=False)
    assert testprobe.load_config()["min_flat_s"] == 7
    env = tmp_path / "probe.json"
    env.write_text(json.dumps({"min_flat_s": 3}), encoding="utf-8")
    monkeypatch.setenv("SUPERLITE_TESTPROBE_CONFIG", str(env))
    assert testprobe.load_config()["min_flat_s"] == 3
