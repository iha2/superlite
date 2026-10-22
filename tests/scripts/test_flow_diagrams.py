"""Each mode prints a stored ASCII flow diagram: the files exist, fit the screen, and the commands point at them."""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FLOWS = REPO / "assets" / "flows"
MODES = {"regular": "commands/start.md", "mvp": "commands/mvp.md", "feature": "commands/feature.md", "debug": "commands/debug.md"}


def test_every_mode_has_a_stored_diagram_that_fits_the_screen():
    for mode in MODES:
        text = (FLOWS / f"{mode}.txt").read_text(encoding="utf-8")
        assert text.strip() and text.isascii(), mode
        assert max(len(ln) for ln in text.splitlines()) <= 79, mode  # the intro screen's width
        assert len(text.splitlines()) <= 50, mode  # a pop-up, not a page


def test_each_mode_command_prints_its_own_stored_diagram():
    for mode, cmd in MODES.items():
        s = (REPO / cmd).read_text(encoding="utf-8")
        assert f"assets/flows/{mode}.txt" in s, cmd
    for cmd in ("commands/mvp.md", "commands/feature.md", "commands/debug.md"):
        s = (REPO / cmd).read_text(encoding="utf-8")
        assert "never redraw" in s and "verbatim" in s, cmd  # stored, so not generated each time
    start = (REPO / "commands/start.md").read_text(encoding="utf-8")
    assert "never redrawn" in start and "assets/flows/" in start


def test_prototype_modes_draw_the_sandbox_and_build_modes_draw_the_parallel_fork():
    for mode in ("mvp", "feature"):
        assert "SANDBOX" in (FLOWS / f"{mode}.txt").read_text(encoding="utf-8"), mode
    for mode in ("regular", "mvp", "feature"):
        assert "[Haiku]     [Sonnet]    [Sonnet]" in (FLOWS / f"{mode}.txt").read_text(encoding="utf-8"), mode
