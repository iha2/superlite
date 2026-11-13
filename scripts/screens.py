"""Render the few docs screenshots of superlite running as PNGs.

    python3 scripts/screens.py render            # screens/*.txt -> src/assets/screens/*.png
    python3 scripts/screens.py render lint-graph # just one

Run from site/. Needs Google Chrome (CHROME=<path> to override) and Pillow.
Screens are real output captured from a real run, not typed up. Ordinary terminal
output stays in the pages as text frames; a screen is only for a moment worth seeing.
A screen source is a small header, a `---` line, then the lines exactly as the terminal showed them:

    title: Claude Code · ~/shop
    ins: 1          (lines you typed: green bar)
    mark: 2-4       (lines to look at: accent bar)
    words: Yes      (words to highlight, `|`-separated)
    graph: x.svg    (optional: the rendered mermaid SVG next to it, drawn under the terminal)
    ---
    ❯ /superlite:debug test_login fails
"""
from __future__ import annotations

import html
import os
import re
import subprocess
import sys
import tempfile
import textwrap
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SITE = Path(os.environ.get("SITE", Path(__file__).resolve().parent.parent))
DOCS = SITE / "src/content/docs"
SOURCES = Path(os.environ.get("SCREENS_SRC", SITE / "screens"))
ASSETS = Path(os.environ.get("SCREENS_OUT", SITE / "src/assets/screens"))
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")

COLS = 92          # wrap width in characters
CHAR_W = 8.43      # SF Mono at 14px
LINE_H = 22
PAD = 44           # backdrop padding around the window
BAR_H = 38
BODY_PAD_Y = 18
BODY_PAD_X = 22
SCALE = 2
DOTS = "".join(f'<span class="dot" style="background:{c}"></span>' for c in ("#ff5f57", "#febc2e", "#28c840"))
BACKDROP = "radial-gradient(120% 140% at 0% 0%, #c7d2fe 0%, #e9d5ff 52%, #fbcfe8 100%)"

STYLE = """
* { box-sizing: border-box; }
html, body { margin: 0; }
body { width: %(page_w)dpx; height: %(page_h)dpx; padding: %(pad)dpx; background: %(backdrop)s; }
.win { width: 100%%; height: 100%%; border-radius: 12px; overflow: hidden; background: #16171d;
  box-shadow: 0 24px 48px -12px rgba(49, 46, 129, .45), 0 0 0 1px rgba(15, 15, 30, .55); }
.bar { height: %(bar_h)dpx; display: flex; align-items: center; position: relative; padding-left: 14px;
  background: linear-gradient(#2a2b33, #23242b); border-bottom: 1px solid #0c0c10; }
.dot { width: 12px; height: 12px; border-radius: 50%%; margin-right: 8px; position: relative; z-index: 1; }
.title { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  font: 500 13px -apple-system, "SF Pro Text", "Helvetica Neue", sans-serif; color: #a1a3b0; }
.body { padding: %(body_y)dpx 0; font: 14px/%(line_h)dpx "SF Mono", SFMono-Regular, Menlo, monospace; color: #e4e4ec; }
.l { padding: 0 %(body_x)dpx; white-space: pre; height: %(line_h)dpx; }
.ins { background: rgba(52, 211, 153, .13); box-shadow: inset 3px 0 #34d399; }
.mark { background: rgba(129, 140, 248, .16); box-shadow: inset 3px 0 #818cf8; }
.p { color: #a5b4fc; font-weight: 700; }
.sel { color: #c4b5fd; }
.dim { color: #8b8d9c; }
.w { background: rgba(250, 204, 21, .22); color: #fde68a; border-radius: 3px; }
"""


# ---------------------------------------------------------------- sources

def parse_ranges(spec: str) -> set[int]:
    out: set[int] = set()
    for part in filter(None, re.split(r"[,\s]+", spec.strip("{} "))):
        a, _, b = part.partition("-")
        out.update(range(int(a), int(b or a) + 1))
    return out


def read_source(path: Path) -> dict:
    head, _, body = path.read_text().partition("\n---\n")
    meta = dict(line.split(": ", 1) for line in head.splitlines() if ": " in line)
    return {
        "title": meta.get("title", ""),
        "ins": parse_ranges(meta.get("ins", "")),
        "mark": parse_ranges(meta.get("mark", "")),
        "words": [w for w in meta.get("words", "").split("|") if w],
        "graph": (path.parent / meta["graph"]) if meta.get("graph") else None,
        "lines": body.rstrip("\n").split("\n"),
    }


# ---------------------------------------------------------------- terminal render

def wrap(line: str) -> list[str]:
    if len(line) <= COLS:
        return [line]
    indent = len(line) - len(line.lstrip())
    return textwrap.wrap(line, COLS, subsequent_indent=" " * (indent + 2),
                         break_long_words=True, break_on_hyphens=False) or [line]


def colour(text: str, words: list[str]) -> str:
    s = html.escape(text)
    for w in words:
        s = s.replace(html.escape(w), f'<span class="w">{html.escape(w)}</span>')
    s = re.sub(r"^(\s*)(❯)", r'\1<span class="p">\2</span>', s)
    s = re.sub(r"^(\s*)(›.*)$", r'\1<span class="sel">\2</span>', s)
    if re.match(r"^\s*(\.\.\.|…)\s*$", text):
        s = f'<span class="dim">{s}</span>'
    return s


def terminal_html(src: dict) -> tuple[str, int, int]:
    rows: list[tuple[str, str]] = []
    for n, line in enumerate(src["lines"], 1):
        cls = "ins" if n in src["ins"] else "mark" if n in src["mark"] else ""
        rows += [(cls, part) for part in wrap(line)]
    cols = max([len(t) for _, t in rows] + [64])
    win_w = max(int(cols * CHAR_W + 2 * BODY_PAD_X + 2), 720)
    page_w = win_w + 2 * PAD
    page_h = BAR_H + 2 * BODY_PAD_Y + LINE_H * len(rows) + 2 * PAD + 1
    css = STYLE % dict(page_w=page_w, page_h=page_h, pad=PAD, bar_h=BAR_H, body_y=BODY_PAD_Y,
                       body_x=BODY_PAD_X, line_h=LINE_H, backdrop=BACKDROP)
    lines = "".join(f'<div class="l {c}">{colour(t, src["words"]) or " "}</div>' for c, t in rows)
    win = (f'<div class=win style="height:{page_h - 2 * PAD}px">'
           f'<div class=bar>{DOTS}<div class=title>{html.escape(src["title"])}</div></div>'
           f"<div class=body>{lines}</div></div>")
    if src.get("graph"):
        win += graph_card(src["graph"].read_text())
        page_h += GRAPH_H + GRAPH_GAP
        css = css.replace(f"height: {page_h - GRAPH_H - GRAPH_GAP}px", f"height: {page_h}px", 1)
    doc = f"<!doctype html><meta charset=utf-8><style>{css}{GRAPH_CSS}</style>{win}"
    return doc, page_w, page_h


# ---------------------------------------------------------------- rendered diagram under a terminal

GRAPH_H = 470      # diagram card height
GRAPH_GAP = 26     # gap between the terminal and the card, holds the "renders as" arrow
GRAPH_CSS = """
.gap { height: %(gap)dpx; display: flex; align-items: center; justify-content: center;
  font: 600 12px -apple-system, "SF Pro Text", sans-serif; color: #4338ca; letter-spacing: .04em; }
.card { height: %(h)dpx; border-radius: 12px; background: #fff; display: flex; align-items: center;
  justify-content: center; padding: 18px; box-shadow: 0 24px 48px -12px rgba(49, 46, 129, .35), 0 0 0 1px rgba(15, 15, 30, .12); }
.card svg { max-width: 100%%; max-height: 100%%; height: auto; }
""" % dict(gap=GRAPH_GAP, h=GRAPH_H)


def graph_card(svg: str) -> str:
    if not svg.lstrip().startswith("<svg"):
        raise SystemExit("screens: graph: must name an .svg file (mermaid already rendered)")
    return f'<div class=gap>↓ RENDERED IN CLAUDE CODE</div><div class=card>{svg}</div>'


def shoot(html_doc: str, w: int, h: int, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "p.html"
        page.write_text(html_doc)
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                        f"--force-device-scale-factor={SCALE}", f"--window-size={w},{h}",
                        "--allow-file-access-from-files", f"--user-data-dir={tmp}/u",
                        f"--screenshot={out}", page.as_uri()],
                       check=True, capture_output=True, timeout=90)
    optimise(out)


def optimise(png: Path) -> None:
    from PIL import Image
    img = Image.open(png).convert("RGB")
    img.quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.FLOYDSTEINBERG).save(png, optimize=True)


def render(only: list[str]) -> None:
    jobs = []
    for src_path in sorted(SOURCES.rglob("*.txt")):
        rel = src_path.relative_to(SOURCES).with_suffix(".png")
        if only and rel.with_suffix("").as_posix() not in only:
            continue
        doc, w, h = terminal_html(read_source(src_path))
        jobs.append((doc, w, h, ASSETS / rel))
    with ThreadPoolExecutor(6) as pool:
        list(pool.map(lambda j: shoot(*j), jobs))
    print(f"screens: {len(jobs)} rendered")


if __name__ == "__main__":
    cmd, args = (sys.argv[1] if len(sys.argv) > 1 else "render"), sys.argv[2:]
    {"render": lambda: render(args)}[cmd]()
