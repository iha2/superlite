#!/usr/bin/env python3
"""Dependency, build and cache paths per language, from config/gitignore.json (data, not code).

Two users: `wave0.py` appends the missing entries of a plan's languages (plus `common`) to the repo's
.gitignore before any agent runs (`ensure`), and `checkpoint.py` never blocks a task on a path that
matches the union of every language's list (`ignored`). Existing .gitignore lines are never removed,
reordered or rewritten; only missing entries are appended. Stdlib only.
"""
import fnmatch
import json
import sys
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1] / "config" / "gitignore.json"
HEADER = "# superlite: dependency, build and cache folders"
_CACHE: dict = {}  # loaded once per process


def load(path: Path | None = None) -> dict[str, list[str]]:  # O(e) first call, O(1) after
    """{"common": [...], "<Language>": [...]}; every list-valued key of the file. {} when unreadable."""
    key = str(path or CONFIG)
    if key not in _CACHE:
        try:
            raw = json.loads(Path(key).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        _CACHE[key] = {k: [e for e in v if isinstance(e, str) and e.strip()]
                       for k, v in raw.items() if isinstance(v, list)}
    return _CACHE[key]


def clean_extra(extra) -> list[str]:  # O(x)
    """The plan's `**Ignore:**` entries as .gitignore lines: stripped, no comments, negations or empties."""
    out = [str(e).strip().strip("`").strip() for e in extra or []]
    return [e for e in out if e and not e.startswith(("#", "!"))]


def entries_for(langs, table=None, extra=()) -> list[str]:  # O(e)
    """`common` first, then each named language's entries in the order given, then the plan's own `extra`; no duplicates."""
    t = table if table is not None else load()
    out: list[str] = []
    for e in [x for k in ["common", *langs] for x in t.get(k, [])] + clean_extra(extra):
        if e not in out:
            out.append(e)
    return out


def union_entries(table=None) -> list[str]:  # O(e)
    """Every entry of every language and `common`."""
    t = table if table is not None else load()
    return entries_for([k for k in t if k != "common"], t)


class Matcher:
    """Matches repo-relative posix paths against directory entries (`x/`, any depth) and file entries (`x`, `*.x`)."""

    def __init__(self, entries):  # O(e)
        self.dirs = {e.rstrip("/") for e in entries if e.endswith("/") and "*" not in e and e.count("/") == 1}
        self.nested = [e for e in entries if e.endswith("/") and "*" not in e and e.count("/") > 1]  # `a/b/`
        self.dir_globs = [e.rstrip("/") for e in entries if e.endswith("/") and "*" in e]
        self.names = {e for e in entries if not e.endswith("/") and "*" not in e}
        self.name_globs = [e for e in entries if not e.endswith("/") and "*" in e]

    def __call__(self, path: str) -> bool:  # O(d + names)
        parts = path.replace("\\", "/").split("/")
        base, folders = parts[-1], parts[:-1]
        if base in self.names or any(fnmatch.fnmatchcase(base, g) for g in self.name_globs):
            return True
        if any(d in self.dirs or any(fnmatch.fnmatchcase(d, g) for g in self.dir_globs) for d in folders):
            return True
        chain = "/" + "/".join(folders) + "/"
        return any("/" + n in chain for n in self.nested)


def never_block_entries(extra=()) -> list[str]:  # O(e + x)
    """Union of every language list and `common`, plus the plan's `extra`. A plan entry without a trailing slash
    counts as both a file and a directory (as in .gitignore); a leading `/` is dropped (matching is at any depth)."""
    out = union_entries()
    for e in clean_extra(extra):
        e = e.lstrip("/")
        out += [e] if e.endswith("/") else [e, e + "/"]
    return out


def union_matcher(extra=()) -> Matcher:  # O(e + x) first call per distinct extra, O(1) after
    key = ("matcher", tuple(clean_extra(extra)))
    if key not in _CACHE:
        _CACHE[key] = Matcher(never_block_entries(extra))
    return _CACHE[key]


def ignored(path: str, extra=()) -> bool:  # O(d + names)
    """True for a path under a dependency, build or cache folder of any language or of the plan's Ignore line
    (never blocks, never staged)."""
    return union_matcher(extra)(path)


def _norm(line: str) -> str:  # O(w)
    return line.strip().lstrip("/").rstrip("/")


def project_extra(root) -> list[str]:  # O(file)
    """Extra gitignore lines from `superlite.json`. A non-list `gitignore` yields []. A bad file raises."""
    scripts = str(Path(__file__).resolve().parent)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import project_config
    raw = project_config.load(root).get("gitignore", [])
    if not isinstance(raw, list):
        return []
    return clean_extra(raw)


def ensure(root, langs, table=None, extra=(), owned=()) -> list[str]:  # O(g + e*o) + file I/O
    """Append to `root/.gitignore` the entries for `langs`, `common` and the plan's `extra` that it lacks; the entries added.

    `node_modules`, `/node_modules` and `node_modules/` count as the same entry. Existing lines are never
    touched; a missing final newline is completed, the file's own line ending is kept, and nothing is
    written when nothing is missing. An entry that covers a path in `owned` (a file or folder a task builds) is skipped, so a second call changes nothing."""
    want = entries_for(langs, table, list(extra or ()) + project_extra(root))
    owned = [o for o in owned if "*" not in o]  # a path some task owns (dist/bundle.js, bin/tool) is built, never ignored
    if owned:
        ms = [(e, Matcher([e])) for e in want]
        want = [e for e, m in ms if not any(m(o) for o in owned)]
    if not want:
        return []
    p = Path(root) / ".gitignore"
    raw = p.read_bytes() if p.is_file() else b""
    text = raw.decode("utf-8", errors="replace")
    have = {_norm(ln) for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith(("#", "!"))}
    missing = []
    for e in want:  # `node_modules` and `node_modules/` are one entry
        if _norm(e) not in have:
            have.add(_norm(e))
            missing.append(e)
    if not missing:
        return []
    nl = "\r\n" if b"\r\n" in raw else "\n"
    block = ([""] if text.strip() else []) + [HEADER, *missing]
    prefix = "" if not raw or raw.endswith(b"\n") else nl
    with open(p, "ab") as f:
        f.write((prefix + nl.join(block) + nl).encode("utf-8"))
    return missing


def read(root) -> str:  # O(size)
    """The repo's .gitignore text, "" when absent."""
    p = Path(root) / ".gitignore"
    try:
        return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
    except OSError:
        return ""
