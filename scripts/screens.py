"""Render the docs screenshots as PNGs.

    python3 scripts/screens.py extract   # one-time: move ```text frame="terminal" blocks out of the pages into screens/
    python3 scripts/screens.py render    # screens/**/*.txt -> src/assets/screens/**/*.png (terminal windows)
    python3 scripts/screens.py github    # reshoot the GitHub pages in GITHUB_SHOTS, framed in a browser window

Run from site/. Needs Google Chrome (CHROME=<path> to override) and Pillow.
A screen source is a small header, a `---` line, then the lines exactly as the terminal shows them:

    title: Claude Code · ~/shop
    ins: 1          (lines you typed: green bar)
    mark: 2-4       (lines to look at: accent bar)
    words: Yes      (words to highlight, `|`-separated)
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
    doc = (f"<!doctype html><meta charset=utf-8><style>{css}</style><div class=win>"
           f'<div class=bar>{DOTS}<div class=title>{html.escape(src["title"])}</div></div>'
           f"<div class=body>{lines}</div></div>")
    return doc, page_w, page_h


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


# ---------------------------------------------------------------- extract (one-time)

FENCE = re.compile(r'^(?P<ind>[ \t]*)```text frame="terminal"(?P<attrs>[^\n]*)\n(?P<body>.*?)^(?P=ind)```[ \t]*$',
                   re.M | re.S)


def alt_text(title: str, lines: list[str]) -> str:
    text = " / ".join(l.strip() for l in lines if l.strip())
    text = re.sub(r"[\[\]]", "", text)
    if len(text) > 220:
        text = text[:217].rstrip() + "..."
    return f"Terminal, {title}: {text}" if title else f"Terminal: {text}"


def extract() -> None:
    total = 0
    for page in sorted(DOCS.rglob("*.mdx")):
        rel = page.relative_to(DOCS).with_suffix("")
        section, name = (rel.parent.as_posix() if rel.parent.as_posix() != "." else "home"), rel.name
        count = 0

        def repl(m: re.Match) -> str:
            nonlocal count
            count += 1
            ind, attrs = m["ind"], m["attrs"]
            lines = [l[len(ind):] if l.startswith(ind) else l.lstrip() for l in m["body"].rstrip("\n").split("\n")]
            title = (re.search(r'title="([^"]*)"', attrs) or [None, ""])[1]
            ins = (re.search(r"ins=\{([^}]*)\}", attrs) or [None, ""])[1]
            mark = (re.search(r"mark=\{([^}]*)\}", attrs) or [None, ""])[1]
            words = re.findall(r'"([^"]+)"', re.sub(r'title="[^"]*"', "", attrs))
            stem = f"{name}-{count}"
            head = [f"title: {title}"] + ([f"ins: {ins}"] if ins else []) + ([f"mark: {mark}"] if mark else [])
            head += [f"words: {'|'.join(words)}"] if words else []
            out = SOURCES / section / f"{stem}.txt"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text("\n".join(head) + "\n---\n" + "\n".join(lines) + "\n")
            png = os.path.relpath(ASSETS / section / f"{stem}.png", page.parent)
            return f"{ind}![{alt_text(title, lines)}]({png})"

        text = page.read_text()
        new = FENCE.sub(repl, text)
        if new != text:
            page.write_text(new)
            total += count
    print(f"extract: {total} terminal screens moved to {SOURCES}")


# ---------------------------------------------------------------- GitHub pages

REPO = "https://github.com/iha2/superlite"
# name: (url path, viewport w, viewport h, crop box in CSS px (x, y, w, h))
GITHUB_SHOTS = {
    "github/pr-56": ("/pull/56", 1100, 760, (0, 70, 1100, 620)),
    "github/pr-57": ("/pull/57", 1100, 760, (0, 70, 1100, 620)),
    "github/pr-58": ("/pull/58", 1100, 760, (0, 70, 1100, 620)),
    "github/pr-60": ("/pull/60", 1100, 760, (0, 70, 1100, 620)),
    "github/copilot-review": ("/pull/58", 1280, 2800, (20, 1490, 900, 520)),
    # Copilot's inline comments are collapsed now that they are resolved: frame the capture taken while open.
    "github/copilot-inline": ("/pull/58", 0, 0, "screens/github/copilot-inline.raw.png"),
}
BROWSER = """
* { box-sizing: border-box; } html, body { margin: 0; }
body { width: %(page_w)dpx; height: %(page_h)dpx; padding: %(pad)dpx; background: %(backdrop)s; }
.shot { position: relative; }
.shot::after { content: ""; position: absolute; left: 0; right: 0; bottom: 0; height: 72px;
  background: linear-gradient(rgba(13, 17, 23, 0), #0d1117); }
.win { border-radius: 12px; overflow: hidden; background: #0d1117;
  box-shadow: 0 24px 48px -12px rgba(49, 46, 129, .45), 0 0 0 1px rgba(15, 15, 30, .55); }
.bar { height: 44px; display: flex; align-items: center; padding: 0 14px;
  background: linear-gradient(#2a2b33, #23242b); border-bottom: 1px solid #0c0c10; }
.dot { width: 12px; height: 12px; border-radius: 50%%; flex: none; margin-right: 8px; }
.url { margin-left: 14px; flex: 1; height: 26px; border-radius: 7px; background: #15161b; color: #8b8d9c;
  font: 13px -apple-system, "SF Pro Text", sans-serif; display: flex; align-items: center; padding: 0 12px; }
.url b { color: #e4e4ec; font-weight: 500; }
img { display: block; width: %(img_w)dpx; height: %(img_h)dpx; }
"""


def github(only: list[str]) -> None:
    from PIL import Image
    for name, (path, vw, vh, box) in GITHUB_SHOTS.items():
        if only and name not in only:
            continue
        with tempfile.TemporaryDirectory() as tmp:
            crop = Path(tmp) / "crop.png"
            if isinstance(box, str):  # a kept capture (1x), framed as is
                img = Image.open(SITE / box)
                img.save(crop)
                cw, ch = img.size
            else:
                cx, cy, cw, ch = box
                raw = Path(tmp) / "raw.png"
                subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                                f"--force-device-scale-factor={SCALE}", f"--window-size={vw},{vh}",
                                f"--user-data-dir={tmp}/u",
                                f"--screenshot={raw}", REPO + path], check=True, capture_output=True, timeout=120)
                Image.open(raw).crop((cx * SCALE, cy * SCALE, (cx + cw) * SCALE, (cy + ch) * SCALE)).save(crop)
            page_w, page_h = cw + 2 * PAD, ch + 44 + 2 * PAD + 1
            css = BROWSER % dict(page_w=page_w, page_h=page_h, pad=PAD, img_w=cw, img_h=ch, backdrop=BACKDROP)
            url = f'<div class=url>github.com/<b>iha2/superlite{html.escape(path)}</b></div>'
            doc = (f"<!doctype html><meta charset=utf-8><style>{css}</style><div class=win>"
                   f'<div class=bar>{DOTS}{url}</div><div class=shot><img src="{crop.as_uri()}"></div></div>')
            shoot(doc, page_w, page_h, ASSETS / f"{name}.png")
        print(f"github: {name}")


if __name__ == "__main__":
    cmd, args = (sys.argv[1] if len(sys.argv) > 1 else "render"), sys.argv[2:]
    {"extract": extract, "render": lambda: render(args), "github": lambda: github(args)}[cmd]()
