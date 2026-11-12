import re
from pathlib import Path

CSS = Path(__file__).resolve().parent.parent / "src" / "styles" / "custom.css"


def _rules():
    return re.findall(r"([^{}]+)\{([^{}]*)\}", CSS.read_text())


def test_dark_backgrounds_scoped_to_dark_theme():
    seen = False
    for sel, body in _rules():
        if "--sl-color-bg" in body:
            seen = True
            sels = [s.strip() for s in sel.split(",")]
            assert sels == [':root[data-theme="dark"]'], f"background override leaks into light mode: {sel.strip()}"
    assert seen


def test_light_block_sets_no_dark_background():
    for sel, body in _rules():
        if 'data-theme="light"' in sel:
            assert "#0b0b12" not in body
