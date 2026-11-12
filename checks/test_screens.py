"""Screens: six critical-moment PNGs, each with a source, every image resolves, none is too big or a PR capture."""
import re
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent
DOCS = SITE / "src" / "content" / "docs"
SOURCES = SITE / "screens"
ASSETS = SITE / "src" / "assets" / "screens"
MAX_BYTES = 250_000
IMAGE = re.compile(r"!\[[^\]]*\]\(([^)\s]+\.(?:png|jpg|jpeg|gif|webp|svg))\)")
EXPECTED = {"flow-regular", "lint-graph", "dispatch", "agents-parallel", "ledger", "report"}
BANNED = re.compile(r"github|copilot|(^|[^a-z])pr[-_ ]?\d|pull[-_]?request", re.I)


def _pages():
    return sorted(list(DOCS.rglob("*.md")) + list(DOCS.rglob("*.mdx")))


def test_exactly_the_six_critical_moment_sources_exist():
    assert {p.stem for p in SOURCES.glob("*.txt")} == EXPECTED
    assert not [p for p in SOURCES.rglob("*") if p.is_dir()], "sources are flat"


def test_every_source_has_its_png():
    missing = [p.name for p in SOURCES.glob("*.txt") if not (ASSETS / f"{p.stem}.png").exists()]
    assert missing == []
    assert {p.stem for p in ASSETS.rglob("*.png")} == EXPECTED


def test_every_graph_header_names_an_existing_svg():
    seen = 0
    for src in SOURCES.glob("*.txt"):
        head = src.read_text().split("\n---\n", 1)[0]
        for line in head.splitlines():
            if line.startswith("graph:"):
                seen += 1
                svg = line.split(":", 1)[1].strip()
                assert (SOURCES / svg).is_file(), (src.name, svg)
    assert seen >= 1


def test_every_image_in_the_docs_exists():
    seen = 0
    for page in _pages():
        for target in IMAGE.findall(page.read_text()):
            seen += 1
            assert (page.parent / target).resolve().exists(), (page.name, target)
    assert seen > 0


def test_every_screen_png_is_at_most_250_kb():
    pngs = sorted(ASSETS.rglob("*.png"))
    assert pngs, "no screen PNGs found"
    big = [(p.name, p.stat().st_size) for p in pngs if p.stat().st_size > MAX_BYTES]
    assert big == []


def test_no_pr_or_copilot_images():
    for page in _pages():
        for target in IMAGE.findall(page.read_text()):
            assert "github/" not in target and not BANNED.search(Path(target).name), (page.name, target)
    assert not (ASSETS / "github").exists()
    assert not [p for p in ASSETS.rglob("*") if BANNED.search(p.name)]
