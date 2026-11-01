"""Shared constants and helpers of the probe runner (see probe_run.py). Stdlib only."""
from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
from pathlib import Path



HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
for _p in (HERE, SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import state  # noqa: E402 | the scripts dir is on sys.path now

IS_WIN = sys.platform == "win32"
ENTRY_TIMEOUT = {"call": 2.0, "run": 5.0, "request": 10.0, "raw": 10.0}
CATEGORY_ORDER = ["deep", "numeric", "encoding", "protocol", "file", "state"]
APP_TYPES = {
    "library": ["deep", "numeric", "encoding", "state"],
    "cli": ["protocol", "encoding", "state", "file"],
    "http": ["protocol", "encoding", "numeric", "state"],
    "web-ui": ["protocol", "encoding"],
    "file-io": ["file", "encoding", "deep"],
    "pipeline": ["numeric", "encoding", "state", "deep"],
    "concurrency": ["state", "protocol"],
    "auth": ["protocol", "state"],
}
APP_ALIASES = {"parser": "library", "evaluator": "library", "server": "http", "service": "http",
               "http-service": "http", "web": "web-ui", "ui": "web-ui", "generator": "file-io",
               "file": "file-io", "data-pipeline": "pipeline"}
TRACE_MARK = "Traceback (most recent call last)"
LEAK_MARKERS = ("root:x:0:0", "for 16-bit app support", "[extensions]")
BENIGN_5XX = {501, 505}
CODE_EXT = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".go", ".rs", ".rb", ".java",
            ".cs", ".php", ".sh"}
SKIP_DIRS = {".git", state.DIR_NAME, "tests", "test", "__pycache__", "venv", ".venv", "node_modules",
             "regression", "site-packages", "dist", "build"}
SHRINK_BUDGET = 5.0
FRAME_RE = re.compile(r'File "([^"]+)", line (\d+)')


def load_module(path: Path):  # O(src bytes) total (exec module)
    name = "_probemod_" + hashlib.md5(str(path).encode()).hexdigest()[:10]
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def probe_modules(extra: Path | None) -> list[Path]:  # O(m log m) total, m = probe module files
    mods = [HERE / "pack" / f"{c}.py" for c in CATEGORY_ORDER if (HERE / "pack" / f"{c}.py").exists()]
    if extra and extra.is_dir():
        mods += sorted(p for p in extra.glob("*.py") if not p.name.startswith("_"))
    return mods


def untrusted(fn, *args):
    """Run project code (a probe module, a probe's make or check) -> (result, None) or (None, the Exception).
    The one place the parent side lets any failure of that code be data; the caller records it."""
    try:
        return fn(*args), None
    except Exception as e:
        return None, e


def _module_entries(path: Path) -> list[dict]:  # O(p) + module exec
    mod = load_module(path)
    cat = mod.CATEGORY
    return [{"id": f"{cat}/{p['name']}", "category": cat, "name": p["name"],
             "entry": p["entry"], "sizes": p["sizes"] or [None],
             "module": str(path), "regress": p.get("regress", True),
             "has_check": p.get("check") is not None} for p in mod.PROBES]


def load_catalog(extra: Path | None) -> tuple[list[dict], list[str]]:  # O(m + p) total + module exec
    catalog, errors = [], []
    for path in probe_modules(extra):
        entries, exc = untrusted(_module_entries, path)
        if exc is not None:
            errors.append(f"{path.name}: {type(exc).__name__}: {exc}")
        else:
            catalog.extend(entries)
    return catalog, errors


def frames_from_text(text: str) -> list[tuple[str, int]]:  # O(len text) total
    """Innermost-first frames from a Python traceback, a faulthandler dump or both."""
    if "most recent call first" in text:
        text = text[text.index("most recent call first"):]
        return [(f, int(n)) for f, n in FRAME_RE.findall(text)]
    if TRACE_MARK in text:
        chunk = text[text.rindex(TRACE_MARK):]
        return [(f, int(n)) for f, n in FRAME_RE.findall(chunk)][::-1]
    return []


def exc_from_text(text: str) -> str:  # O(len text) total
    for line in reversed([x for x in text.strip().splitlines() if x.strip()]):
        m = re.match(r"^([A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt|Warning)?)\b:?", line)
        if m and not line.startswith((" ", "\t")):
            return m.group(1)
    return "Exception"
