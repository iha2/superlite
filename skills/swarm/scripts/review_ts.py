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
# Named exports only: `export default function f` exports `default`, not `f`. const/let/var go through VAR_DECL.
# Each pattern starts a module-level statement: a line start or after `;` (block bodies are blanked first, top_level).
DECL = (r"(?:^|;)\s*export\s+(?:declare\s+)?(?:async\s+)?"
        r"(?:function\s*\*?|type|interface|class|abstract\s+class|enum|const\s+enum|namespace)\s+{name}\b")
VAR_DECL = re.compile(r"(?:^|;)\s*export\s+(?:declare\s+)?(?:const|let|var)\s+(?!enum\b)", re.M)
EXPORT_HEAD = re.compile(r"\bexport\s+(?:type\s+)?$")
IDENT = re.compile(r"[A-Za-z_$][\w$]*")
BRACKETS = {"(": 1, "[": 1, "{": 1, ")": -1, "]": -1, "}": -1}
EXPORT_LIST = re.compile(r"(?:^|;)\s*export\s+(?:type\s+)?\{([^}]*)\}", re.M)
# Comments and string or template literals, leftmost first, so `//` inside a string stays a string.
LEXEMES = re.compile(r"//[^\n]*|/\*.*?\*/|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`", re.S)


def mask(text: str) -> str:  # O(B) total
    """`text` with every comment and literal blanked to spaces (newlines kept, so line numbers hold)."""
    return LEXEMES.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def top_level(text: str) -> str:  # O(B) total
    """Masked `text` with the inside of every top-level block blanked (braces and newlines kept), except export lists, so
    an `export` inside a namespace, class or function body is not taken for a module export."""
    out, depth, keep = list(text), 0, False
    for i, c in enumerate(text):
        if c == "{" and depth == 0:
            keep = EXPORT_HEAD.search(text[max(0, i - 40):i]) is not None
        depth += (c == "{") - (c == "}" and depth > 0)
        if depth and not keep and c not in "{}\n":
            out[i] = " "
    return "".join(out)


def ts_match(path: str, rel: str) -> bool:  # O(m) total
    """True when `path` is a TypeScript file whose path without the extension is `rel` or ends in `/rel`."""
    path = path.replace("\\", "/")
    ext = next((e for e in TS_EXT if path.endswith(e)), None)
    if not ext:
        return False
    stem = path[: -len(ext)]
    return stem == rel or stem.endswith("/" + rel)


def _in_owns(owns: list[list[str]], rel: str) -> str | None:  # O(o) total
    """The first owned path (producer's Owns first) that `rel` names, whether or not it exists."""
    return next((o for group in owns for o in group if ts_match(o, rel)), None)


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
        o = _in_owns(owns, rel)
        if o:  # a missing owned file stops here (the missing-module check reports it), never a namesake elsewhere
            return (root / o, parts[k]) if (root / o).is_file() else None
        f = _direct(root, rel)
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


def bindings(text: str, i: int) -> list[str]:  # O(statement length) total
    """The names a const/let/var statement starting at `i` (after the keyword) binds: each identifier at bracket depth
    0 at the start or after a comma, up to `;` or a newline that ends the statement. `text` is masked (no literals)."""
    names, depth, want, last = [], 0, True, ""
    while i < len(text):
        c = text[i]
        m = IDENT.match(text, i) if want and depth == 0 else None
        if m:
            names.append(m.group(0))
            want, last, i = False, m.group(0)[-1], m.end()
            continue
        if depth == 0 and (c == ";" or (c == "\n" and last not in ",=")):
            break
        depth += BRACKETS.get(c, 0)
        want = want or (depth == 0 and c == ",")
        last = last if c.isspace() else c
        i += 1
    return names


def export_line(f: Path, name: str) -> int:  # O(B) total
    """The 1-based line where `name` is exported from TypeScript file `f`, else 0."""
    try:
        text = top_level(mask(f.read_text(encoding="utf-8", errors="replace")))
    except OSError:
        return 0
    m = re.search(DECL.format(name=re.escape(name)), text, re.M)
    if not m:
        m = next((x for x in VAR_DECL.finditer(text) if name in bindings(text, x.end())), None)
    if not m:  # export { a, b as c, type T }: the exported name is the last word of each specifier
        lists = ((x, [re.split(r"\s+as\s+", re.sub(r"^type\s+", "", n.strip()))[-1].strip() for n in x.group(1).split(",")])
                 for x in EXPORT_LIST.finditer(text))
        m = next((x for x, names in lists if name in names), None)
    return text.count("\n", 0, m.start() + m.group(0).index("export")) + 1 if m else 0  # the line `export` is on
