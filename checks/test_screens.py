"""Screenshots: every source has its PNG, every image resolves, none is too big, no text frames are left."""
import re
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent
DOCS = SITE / "src" / "content" / "docs"
SOURCES = SITE / "screens"
ASSETS = SITE / "src" / "assets" / "screens"
MAX_BYTES = 250_000
IMAGE = re.compile(r"!\[[^\]]*\]\(([^)\s]+\.png)\)")


def _pages():
    return sorted(list(DOCS.rglob("*.md")) + list(DOCS.rglob("*.mdx")))


def test_every_screen_source_has_its_png():
    sources = sorted(SOURCES.rglob("*.txt"))
    assert sources, "no screen sources found"
    missing = [str(p.relative_to(SOURCES)) for p in sources
               if not (ASSETS / p.relative_to(SOURCES).with_suffix(".png")).exists()]
    assert missing == []


def test_every_image_in_the_docs_exists():
    seen = 0
    for page in _pages():
        for target in IMAGE.findall(page.read_text()):
            seen += 1
            assert (page.parent / target).resolve().exists(), (page.name, target)
    assert seen > 0


def test_no_terminal_frames_left():
    left = [p.name for p in _pages() if 'frame="terminal"' in p.read_text()]
    assert left == []


def test_every_screen_png_is_at_most_250_kb():
    pngs = sorted(ASSETS.rglob("*.png"))
    assert pngs, "no screen PNGs found"
    big = [(str(p.relative_to(ASSETS)), p.stat().st_size) for p in pngs if p.stat().st_size > MAX_BYTES]
    assert big == []
