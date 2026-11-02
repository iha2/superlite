"""TypeScript side of the Produces check: a dotted name (`model.parseRegistry`) to a .ts/.tsx file and its export.

Used by review_contract.check_interfaces for names with no Python module. Only the export itself is checked, never
the signature. Stdlib only.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import state  # noqa: E402

TS_EXT = (".ts", ".tsx", ".mts", ".cts")
SKIP_DIRS = {"node_modules", ".git", state.DIR_NAME, "dist", "build", "coverage"}
DECL = (r"^\s*export\s+(?:default\s+)?(?:declare\s+)?(?:async\s+)?"
        r"(?:function\s*\*?|const|let|var|type|interface|class|abstract\s+class|enum|const\s+enum|namespace)\s+{name}\b")
EXPORT_LIST = re.compile(r"^\s*export\s+(?:type\s+)?\{([^}]*)\}", re.M)
# Comments and string or template literals, leftmost first, so `//` inside a string stays a string.
LEXEMES = re.compile(r"//[^\n]*|/\*.*?\*/|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`", re.S)


def mask(text: str) -> str:  # O(B) total
    """`text` with every comment and literal blanked to spaces (newlines kept, so line numbers hold)."""
    return LEXEMES.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def ts_match(path: str, rel: str) -> bool:  # O(m) total
    """True when `path` is a TypeScript file whose path without the extension is `rel` or ends in `/rel`."""
    path = path.replace("\\", "/")
    ext = next((e for e in TS_EXT if path.endswith(e)), None)
    if not ext:
        return False
    stem = path[: -len(ext)]
    return stem == rel or stem.endswith("/" + rel)


def _in_owns(root: Path, owns: list[list[str]], rel: str) -> Path | None:  # O(o) stat calls
    hits = (root / o for group in owns for o in group if ts_match(o, rel))
    return next((f for f in hits if f.is_file()), None)


def _direct(root: Path, rel: str) -> Path | None:  # O(1) stat calls (8)
    cands = (base / f"{rel}{ext}" for base in (root, root / "src") for ext in TS_EXT)
    return next((f for f in cands if f.is_file()), None)


def _walk(root: Path, rel: str) -> Path | None:  # O(F) files under root
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        rels = ((Path(dirpath) / fn).relative_to(root).as_posix() for fn in files)
        hit = next((r for r in rels if ts_match(r, rel)), None)
        if hit:
            return root / hit
    return None


def locate(root: Path, parts: list[str], owns: list[list[str]]) -> tuple[Path, str] | None:  # O(k*o + F)
    """(TypeScript file, exported name) for dotted `parts`, else None. `owns` lists Owns paths, the producing task's
    first: they are searched first, then root and src, then (only when the plan owns any TypeScript) the tree."""
    if len(parts) < 2:
        return None
    for k in range(len(parts) - 1, 0, -1):
        rel = "/".join(parts[:k])
        f = _in_owns(root, owns, rel) or _direct(root, rel)
        if f:
            return f, parts[k]
    if not any(o.endswith(TS_EXT) for group in owns for o in group):
        return None
    f = _walk(root, "/".join(parts[:-1]))
    return (f, parts[-1]) if f else None


def owned_match(parts: list[str], owns: list[list[str]]) -> str | None:  # O(k*o) total
    """The first owned TypeScript path that dotted `parts` name as a module, whether or not the file exists."""
    rels = ["/".join(parts[:k]) for k in range(len(parts) - 1, 0, -1)]
    return next((o for rel in rels for group in owns for o in group if ts_match(o, rel)), None)


def export_line(f: Path, name: str) -> int:  # O(B) total
    """The 1-based line where `name` is exported from TypeScript file `f`, else 0."""
    try:
        text = mask(f.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return 0
    m = re.search(DECL.format(name=re.escape(name)), text, re.M)
    if not m:
        lists = ((x, [re.split(r"\s+as\s+", n.strip())[-1].strip() for n in x.group(1).split(",")])
                 for x in EXPORT_LIST.finditer(text))
        m = next((x for x, names in lists if name in names), None)
    return text.count("\n", 0, m.start()) + 1 if m else 0
