#!/usr/bin/env python3
"""Dependency, build and cache paths per language, from config/gitignore.json (data, not code).

Two users: `wave0.py` appends the missing entries of a plan's languages (plus `common`) to the repo's
.gitignore before any agent runs (`ensure`), and `commit_task.py` never blocks a task on a path that
matches the union of every language's list (`ignored`). Existing .gitignore lines are never removed,
reordered or rewritten; only missing entries are appended. Stdlib only.
"""
# Complexity notation:
#   e = entries in the table, g = lines in the existing .gitignore, w = path length, d = directory segments of a path
import fnmatch  # O(1)
import json  # O(1)
from pathlib import Path  # O(1)

CONFIG = Path(__file__).resolve().parents[1] / "config" / "gitignore.json"  # O(1)
HEADER = "# superlite: dependency, build and cache folders"  # O(1)
_CACHE: dict = {}  # loaded once per process | O(1)


def load(path: Path | None = None) -> dict[str, list[str]]:  # O(e) first call, O(1) after
    """{"common": [...], "<Language>": [...]}; every list-valued key of the file. {} when unreadable."""
    key = str(path or CONFIG)  # O(1)
    if key not in _CACHE:  # O(1)
        try:  # O(1)
            raw = json.loads(Path(key).read_text(encoding="utf-8"))  # O(size)
        except (OSError, ValueError):  # O(1)
            raw = {}  # O(1)
        _CACHE[key] = {k: [e for e in v if isinstance(e, str) and e.strip()]  # O(e)
                       for k, v in raw.items() if isinstance(v, list)}
    return _CACHE[key]  # O(1)


def clean_extra(extra) -> list[str]:  # O(x)
    """The plan's `**Ignore:**` entries as .gitignore lines: stripped, no comments, negations or empties."""
    out = [str(e).strip().strip("`").strip() for e in extra or []]  # O(x)
    return [e for e in out if e and not e.startswith(("#", "!"))]  # O(x)


def entries_for(langs, table=None, extra=()) -> list[str]:  # O(e)
    """`common` first, then each named language's entries in the order given, then the plan's own `extra`; no duplicates."""
    t = table if table is not None else load()  # O(1)
    out: list[str] = []  # O(1)
    for e in [x for k in ["common", *langs] for x in t.get(k, [])] + clean_extra(extra):  # O(e)
        if e not in out:  # O(e)
            out.append(e)  # O(1)
    return out  # O(1)


def union_entries(table=None) -> list[str]:  # O(e)
    """Every entry of every language and `common`."""
    t = table if table is not None else load()  # O(1)
    return entries_for([k for k in t if k != "common"], t)  # O(e)


class Matcher:  # O(e) to build, O(d + names) per path
    """Matches repo-relative posix paths against directory entries (`x/`, any depth) and file entries (`x`, `*.x`)."""

    def __init__(self, entries):  # O(e)
        self.dirs = {e.rstrip("/") for e in entries if e.endswith("/") and "*" not in e and e.count("/") == 1}  # O(e)
        self.nested = [e for e in entries if e.endswith("/") and "*" not in e and e.count("/") > 1]  # `a/b/` | O(e)
        self.dir_globs = [e.rstrip("/") for e in entries if e.endswith("/") and "*" in e]  # O(e)
        self.names = {e for e in entries if not e.endswith("/") and "*" not in e}  # O(e)
        self.name_globs = [e for e in entries if not e.endswith("/") and "*" in e]  # O(e)

    def __call__(self, path: str) -> bool:  # O(d + names)
        parts = path.replace("\\", "/").split("/")  # O(w)
        base, folders = parts[-1], parts[:-1]  # O(1)
        if base in self.names or any(fnmatch.fnmatchcase(base, g) for g in self.name_globs):  # O(globs)
            return True  # O(1)
        if any(d in self.dirs or any(fnmatch.fnmatchcase(d, g) for g in self.dir_globs) for d in folders):  # O(d * globs)
            return True  # O(1)
        chain = "/" + "/".join(folders) + "/"  # O(w)
        return any("/" + n in chain for n in self.nested)  # O(nested)


def never_block_entries(extra=()) -> list[str]:  # O(e + x)
    """Union of every language list and `common`, plus the plan's `extra`. A plan entry without a trailing slash
    counts as both a file and a directory (as in .gitignore); a leading `/` is dropped (matching is at any depth)."""
    out = union_entries()  # O(e)
    for e in clean_extra(extra):  # O(x)
        e = e.lstrip("/")  # O(w)
        out += [e] if e.endswith("/") else [e, e + "/"]  # O(1)
    return out  # O(e + x)


def union_matcher(extra=()) -> Matcher:  # O(e + x) first call per distinct extra, O(1) after
    key = ("matcher", tuple(clean_extra(extra)))  # O(x)
    if key not in _CACHE:  # O(1)
        _CACHE[key] = Matcher(never_block_entries(extra))  # O(e + x)
    return _CACHE[key]  # O(1)


def ignored(path: str, extra=()) -> bool:  # O(d + names)
    """True for a path under a dependency, build or cache folder of any language or of the plan's Ignore line
    (never blocks, never staged)."""
    return union_matcher(extra)(path)  # O(d + names)


def _norm(line: str) -> str:  # O(w)
    return line.strip().lstrip("/").rstrip("/")  # O(w)


def ensure(root, langs, table=None, extra=(), owned=()) -> list[str]:  # O(g + e*o) + file I/O
    """Append to `root/.gitignore` the entries for `langs`, `common` and the plan's `extra` that it lacks; the entries added.

    `node_modules`, `/node_modules` and `node_modules/` count as the same entry. Existing lines are never
    touched; a missing final newline is completed, the file's own line ending is kept, and nothing is
    written when nothing is missing. An entry that covers a path in `owned` (a file or folder a task builds) is skipped, so a second call changes nothing."""
    want = entries_for(langs, table, extra)  # O(e)
    owned = [o for o in owned if "*" not in o]  # a path some task owns (dist/bundle.js, bin/tool) is built, never ignored | O(o)
    if owned:  # O(1)
        ms = [(e, Matcher([e])) for e in want]  # O(e)
        want = [e for e, m in ms if not any(m(o) for o in owned)]  # O(e*o)
    if not want:  # O(1)
        return []  # O(1)
    p = Path(root) / ".gitignore"  # O(1)
    raw = p.read_bytes() if p.is_file() else b""  # O(size)
    text = raw.decode("utf-8", errors="replace")  # O(size)
    have = {_norm(ln) for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith(("#", "!"))}  # O(g)
    missing = []  # O(1)
    for e in want:  # `node_modules` and `node_modules/` are one entry | O(e)
        if _norm(e) not in have:  # O(1)
            have.add(_norm(e))  # O(1)
            missing.append(e)  # O(1)
    if not missing:  # O(1)
        return []  # O(1)
    nl = "\r\n" if b"\r\n" in raw else "\n"  # O(size)
    block = ([""] if text.strip() else []) + [HEADER, *missing]  # O(e)
    prefix = "" if not raw or raw.endswith(b"\n") else nl  # O(1)
    with open(p, "ab") as f:  # O(1)
        f.write((prefix + nl.join(block) + nl).encode("utf-8"))  # O(e)
    return missing  # O(e)


def read(root) -> str:  # O(size)
    """The repo's .gitignore text, "" when absent."""
    p = Path(root) / ".gitignore"  # O(1)
    try:  # O(1)
        return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""  # O(size)
    except OSError:  # O(1)
        return ""  # O(1)
