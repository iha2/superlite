"""The prototype guidance names the sandbox path, the ecosystems and tests by path; no worktree."""
from pathlib import Path

TEXT = (Path(__file__).resolve().parents[2] / "skills" / "feature-prototype" / "SKILL.md").read_text(encoding="utf-8")


def test_guidance_names_sandbox_path():
    assert ".superlite/sandboxes/<session>/<topic>/" in TEXT


def test_guidance_no_worktree():
    assert "worktree" not in TEXT.lower() and "--adopt" not in TEXT


def test_guidance_ecosystems():
    for name in ("Python", "Node", "Go", "Rust", "JVM", ".NET"):
        assert name in TEXT


def test_guidance_tests_by_path():
    assert "run_tests --fast <sandbox>/tests" in TEXT
