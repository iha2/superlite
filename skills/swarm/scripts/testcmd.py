#!/usr/bin/env python3
"""Resolve the project's test command, language-neutrally.

    testcmd.py [--fast PATHS...] [--root .]      print the resolved command as JSON

Order: (a) `.superlite/test-cmd.json` {"language","test","test_file","junit","setup","related"} (JSON; `test_file` is a template
with {files}; `{junit}` in a command is replaced by the path run_tests wants the JUnit XML at; a literal `junit` path is read
after the run; `related` is a template with `{src}` for source→test mapping); (b) detection from the project's own marker files: package.json scripts.test (npm test; the JUnit flag when
vitest is a dependency, jest-junit's reporter when jest-junit is), go.mod, Cargo.toml, *.sln / *.csproj, then Python
(pyproject.toml, pytest.ini, setup.cfg, tox.ini, conftest.py, or test_*.py files); (c) otherwise None.
Library: resolve, resolve_related, parse_output (best-effort counts and failing names without JUnit). Stdlib only.
"""
import argparse
import fnmatch
import json
import os
import posixpath
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import state  # noqa: E402

SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "target", "bin", "obj", "dist", "build", "__pycache__", state.DIR_NAME}
WALK_LIMIT = 400


def read_config(root: Path):  # O(file)
    p = state.path(root, "test-cmd.json")
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("test") else None


def _pkg(root: Path):  # O(file)
    try:
        return json.loads((root / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _has_pytest_files(root: Path) -> bool:  # O(f)
    seen = 0
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            seen += 1
            if re.match(r"^(test_.*|.*_test)\.py$", fn):
                return True
        if seen > WALK_LIMIT:
            break
    return False


def detect(root: Path):  # O(m + f)
    """{"language","test","test_file","junit","setup"} from marker files, or None. `python` returns the pytest marker."""
    pkg = _pkg(root)
    if pkg and (pkg.get("scripts") or {}).get("test"):
        deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
        lang = "typescript" if (root / "tsconfig.json").exists() or "typescript" in deps else "javascript"
        test, test_file, junit, env = "npm test", "npm test -- {files}", "", {}
        if "vitest" in deps:
            test = "npm test -- --reporter=default --reporter=junit --outputFile.junit={junit}"
            test_file = "npm test -- {files} --reporter=default --reporter=junit --outputFile.junit={junit}"
        elif "jest-junit" in deps and "jest" in deps:
            test = "npm test -- --reporters=default --reporters=jest-junit"
            test_file = "npm test -- {files} --reporters=default --reporters=jest-junit"
            env = {"JEST_JUNIT_OUTPUT_FILE": "{junit}"}
        return {"language": lang, "test": test, "test_file": test_file, "junit": "{junit}" if "{junit}" in test else "", "env": env, "setup": ""}
    if (root / "go.mod").exists():
        return {"language": "go", "test": "go test ./...", "test_file": "go test {files}", "junit": "", "setup": ""}
    if (root / "Cargo.toml").exists():
        return {"language": "rust", "test": "cargo test", "test_file": "cargo test {files}", "junit": "", "setup": ""}
    names = [p.name for p in root.iterdir()] if root.is_dir() else []
    if any(n.endswith((".sln", ".csproj")) for n in names):
        return {"language": "csharp", "test": "dotnet test", "test_file": "dotnet test {files}", "junit": "", "setup": ""}
    if (any((root / m).exists() for m in ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini", "conftest.py", "setup.py"))
            or _has_pytest_files(root)):
        return {"language": "python", "test": None, "test_file": None, "junit": "", "setup": ""}  # pytest path of run_tests
    return None


def _raw_config(root: Path):  # O(file)
    p = state.path(root, "test-cmd.json")
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _related_candidates(root: Path, src: str) -> list[str]:
    """Paths to try for a source file: config `related` template first, else name convention."""
    src = src.replace("\\", "/")
    stem = Path(src).stem
    raw = _raw_config(root)
    related = (raw or {}).get("related")
    if isinstance(related, str) and "{src}" in related:
        return [related.replace("{src}", stem).replace("\\", "/")]
    # Python (and similar) name convention: src/foo.py -> tests/test_foo.py
    return [f"tests/test_{stem}{Path(src).suffix}"]


def resolve_related(root: str, src: str) -> list[str]:  # O(file)
    """Map one source path to existing related test paths. Raises FileNotFoundError if none."""
    root_p = Path(root)
    src = src.replace("\\", "/")
    found = []
    for cand in _related_candidates(root_p, src):
        if (root_p / cand).is_file():
            found.append(cand.replace("\\", "/"))
    if not found:
        raise FileNotFoundError(f"no tests found for {src}")
    return found


def is_test_path(path: str) -> bool:
    """True when path looks like a test file (keep as --fast arg; do not resolve_related)."""
    p = path.replace("\\", "/")
    name = Path(p).name
    if re.match(r"^(test_.*|.*_test)\.[^.]+$", name):
        return True
    return p.startswith("tests/") or "/tests/" in f"/{p}"


FULL_NAMES = {"conftest.py", "pyproject.toml", "setup.cfg", "pytest.ini", "tox.ini", "package.json", "package-lock.json", "yarn.lock",
              "pnpm-lock.yaml", "uv.lock", "poetry.lock", "go.mod", "go.sum", "Cargo.toml", "Cargo.lock"}
TEST_WALK_LIMIT = 5000


def _read_map(root: str) -> dict:  # O(file)
    try:
        d = json.loads(state.path(Path(root), "test-map.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _map_list(v) -> list[str]:
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []


def _git(root: str, *args: str) -> list[str] | None:  # O(output)
    try:
        r = subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=root, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return [x.replace("\\", "/") for x in r.stdout.decode("utf-8", "replace").split("\0") if x]


class BaseUnreadable(Exception):
    """Inside a repo, git could not diff against the base (unknown or shallow-missing SHA, no HEAD yet)."""


def changed_files(root: str, base: str | None) -> list[str]:  # O(changes)
    """Paths changed vs `base` (default HEAD) plus untracked non-ignored files; sorted, posix; [] outside a repo.
    Raises BaseUnreadable when inside a repo the diff fails, so a bad base never reads as "nothing changed"."""
    tracked = _git(root, "diff", "--name-only", "-z", base or "HEAD", "--")
    if tracked is None:
        if _git(root, "rev-parse", "--git-dir") is None:
            return []
        raise BaseUnreadable(f"cannot diff against {base or 'HEAD'}")
    untracked = _git(root, "ls-files", "-z", "--others", "--exclude-standard") or []
    return sorted(set(tracked) | set(untracked))


def is_full_trigger(path: str, root: str = ".") -> bool:  # O(map)
    """True when a change to `path` should run the whole suite."""
    p = path.replace("\\", "/")
    name = p.rsplit("/", 1)[-1]
    if name in FULL_NAMES or name.endswith(".csproj") or p == f"{state.DIR_NAME}/test-cmd.json":
        return True
    return any(fnmatch.fnmatch(p, g) for g in _map_list(_read_map(root).get("_full")))


CODE_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
LANG_EXTS = {"python": (".py",), "typescript": CODE_EXTS, "javascript": CODE_EXTS, "node": CODE_EXTS, "go": (".go",),
             "rust": (".rs",), "csharp": (".cs",), "dotnet": (".cs",)}


def _is_test(p: str) -> bool:
    """A test FILE by name: test_*.<ext>, *_test.<ext>, *.test.<ext>, *.spec.<ext> (spec only with a code extension),
    Rust integration tests (tests/<name>.rs) and .NET test classes (FooTests.cs, FooTest.cs)."""
    p = p.replace("\\", "/")
    name = Path(p).name
    if re.search(r"(^|/)tests/[^/]+\.rs$", p) or re.match(r"^.+Tests?\.cs$", name):
        return True
    if re.match(r"^(test_.*|.*_test)\.[^.]+$", name) or re.search(r"\.test\.[^./]+$", name):
        return not name.endswith((".md", ".json", ".jsonl", ".txt"))
    m = re.search(r"\.spec(\.[^./]+)$", name)
    return bool(m and m.group(1) in CODE_EXTS)


def _ini_testpaths(text: str, section: str):  # the value with its indented continuation lines, or None
    sec, vals = None, None
    for line in text.splitlines():
        if vals is not None:
            if not (line[:1].isspace() and line.strip()):
                break
            vals += [] if line.strip().startswith(("#", ";")) else line.split()
            continue
        m = re.match(r"^\s*\[([^\]]+)\]\s*$", line)
        if m:
            sec = m.group(1).strip()
            continue
        m = re.match(r"^\s*testpaths\s*[=:]\s*(.*)$", line)
        if sec == section and m:
            vals = m.group(1).split()
    return vals or None


def pytest_testpaths(root: str) -> list[str]:  # O(config files); [] when none is configured
    r = Path(root)
    for name, section in (("pytest.ini", "pytest"), ("pyproject.toml", None), ("tox.ini", "pytest"), ("setup.cfg", "tool:pytest")):
        try:
            text = (r / name).read_text(encoding="utf-8")
        except OSError:
            continue
        if section is None:
            try:
                import tomllib
                v = ((tomllib.loads(text).get("tool") or {}).get("pytest") or {}).get("ini_options", {}).get("testpaths")
            except (ImportError, ValueError, AttributeError):  # an unreadable pyproject only disables the filter
                v = None
            v = v.split() if isinstance(v, str) else v
        else:
            v = _ini_testpaths(text, section)
        if isinstance(v, list) and v:
            return [x.replace("\\", "/").strip("/").removeprefix("./") for x in v if isinstance(x, str) and x.strip("/ ")]
    return []


def _language(root: str):
    return (resolve(root) or {}).get("language")


def _collectable(root: str, lang):  # -> predicate: does the project's runner collect this test path
    exts = LANG_EXTS.get(lang or "")
    if lang == "go":
        exts = None
    tpaths = pytest_testpaths(root) if lang == "python" else []

    def keep(t: str) -> bool:
        t = t.replace("\\", "/")
        if lang == "go" and not t.endswith("_test.go"):
            return False
        if exts and not t.endswith(exts):
            return False
        if tpaths:
            parts = t.split("/")
            dirs = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
            return any(fnmatch.fnmatch(d, tp) for tp in tpaths for d in dirs)
        return True
    return keep


def _walk_tests(root: str) -> list[str]:  # O(f), capped
    out: list[str] = []
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if d not in SKIP_DIRS and not d.startswith("."))
        rels = (os.path.relpath(os.path.join(dp, fn), root).replace("\\", "/") for fn in sorted(fns))
        out.extend(r for r in rels if _is_test(r))
        if len(out) >= TEST_WALK_LIMIT:
            return out[:TEST_WALK_LIMIT]
    return out


def _related_hits(root: str, p: str, keep) -> list[tuple[str, str]]:
    try:
        return [(t, "related") for t in resolve_related(root, p) if keep(t)]
    except FileNotFoundError:
        return []


def _mention_hits(p: str, tests: list[str], text) -> list[tuple[str, str]]:
    stem = Path(p).stem
    if not stem:
        return []
    pat = re.compile(r"\b" + re.escape(stem) + r"\b")
    return [(t, "mention") for t in tests if pat.search(text(t))]


def _map_hits(root: str, p: str, rmap: dict, tests: list[str], keep) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for glob, vals in rmap.items():
        if glob == "_full" or not fnmatch.fnmatch(p, glob):
            continue
        for v in (x.replace("\\", "/") for x in _map_list(vals)):
            if any(c in v for c in "*?["):
                out += [(t, "map") for t in tests if fnmatch.fnmatch(t, v)]
            elif keep(v) and (Path(root) / v).is_file():
                out.append((v, "map"))
    return out


def select_affected(root: str, changed: list[str]) -> dict:  # O(changed * tests)
    """-> {"full", "selected", "reasons", "untested"[, "trigger"]}; over-selects when unsure."""
    paths = [c.replace("\\", "/") for c in changed]
    for p in paths:
        if is_full_trigger(p, root):
            return {"full": True, "selected": [], "reasons": {}, "untested": [], "trigger": p}
    lang = _language(root)
    rmap = _read_map(root)
    keep = _collectable(root, lang)
    tests = [t for t in _walk_tests(root) if keep(t)]
    texts: dict[str, str] = {}
    reasons: dict[str, str] = {}
    untested: list[str] = []

    def add(t: str, why: str) -> None:
        reasons.setdefault(t, why)

    def text(t: str) -> str:
        if t not in texts:
            try:
                texts[t] = (Path(root) / t).read_text(encoding="utf-8", errors="replace")
            except OSError:
                texts[t] = ""
        return texts[t]

    for p in paths:
        is_test = _is_test(p) or (lang == "rust" and p.endswith(".rs") and "#[cfg(test)]" in text(p))  # inline Rust tests
        hits = [(p, "changed")] if is_test and keep(p) and (Path(root) / p).is_file() else []
        if not is_test:
            hits += _related_hits(root, p, keep)
        hits += _mention_hits(p, tests, text) + _map_hits(root, p, rmap, tests, keep)  # a changed test still selects its dependents
        for t, why in hits:
            add(t, why)
        if not hits and not is_test:
            untested.append(p)
    return {"full": False, "selected": sorted(reasons), "reasons": dict(sorted(reasons.items())), "untested": untested}


def _go_package(p: str) -> str:  # pkg/foo_test.go -> ./pkg, root_test.go -> ., a package argument stays as is
    p = p.replace("\\", "/")
    if not p.endswith(".go"):
        return p
    d = posixpath.dirname(p)
    return "./" + d if d else "."


def _rust_target(p: str, root: Path) -> tuple[str, ...]:  # tests/foo.rs -> --test foo, inline tests -> --lib or --bins
    if not p.endswith(".rs"):
        return (p,)
    if re.search(r"(^|/)tests/[^/]+\.rs$", p):
        return ("--test", Path(p).stem)
    return ("--lib",) if (root / "src" / "lib.rs").is_file() else ("--bins",)


def _runner_args(runner: str, paths: list[str], root: Path) -> list[str]:
    """Selected test files -> what the runner (the command's first two words) takes: `go test x_test.go` drops the
    package's other files, cargo and dotnet take targets and filters, not paths. Any other command gets the paths."""
    paths = [p.replace("\\", "/") for p in paths]
    if not paths:
        return paths
    if runner == "go test":
        return list(dict.fromkeys(_go_package(p) for p in paths))
    if runner == "cargo test":
        return [t for pair in dict.fromkeys(_rust_target(p, root) for p in paths) for t in pair]
    if runner == "dotnet test":
        classes = [Path(p).stem for p in paths if p.endswith(".cs")]
        rest = [p for p in paths if not p.endswith(".cs")]
        flt = ["--filter", '"' + "|".join(f"FullyQualifiedName~{c}" for c in dict.fromkeys(classes)) + '"'] if classes else []
        return rest + flt
    return paths


def resolve(root=".", paths=()):  # O(m + f)
    """-> {"language", "python": bool, "command": str|None, "junit": str, "env": {}, "setup": str, "source"} or None."""
    root = Path(root)
    cfg = read_config(root)
    src = "test-cmd.json"
    if cfg is None:
        cfg, src = detect(root), "markers"
    if cfg is None:
        return None
    if cfg.get("test") is None and cfg.get("language") == "python":
        return {"language": "python", "python": True, "command": None, "junit": "", "env": {}, "setup": "", "source": src}
    template = cfg.get("test_file") or cfg["test"]
    paths = _runner_args(" ".join(template.split()[:2]), list(paths), root)
    files = " ".join(f'"{p}"' if " " in p else p for p in paths)
    command = template.replace("{files}", files) if paths else cfg["test"]
    if paths and "{files}" not in template:  # a test_file without {files} runs everything
        command = template
    return {"language": cfg.get("language") or "unknown", "python": False, "command": command, "junit": cfg.get("junit") or "",
            "env": cfg.get("env") or {}, "setup": cfg.get("setup") or "", "source": src}


NAME_PATTERNS = [r"^--- FAIL: (\S+)", r"^test (\S+) \.\.\. FAILED", r"^\s*(?:FAIL|✗|×|✕)\s+(.+?)\s*$", r"^\s*Failed\s+(\S+)",
                 r"^\s*\d+\)\s+(.+?)\s*$"]
COUNT_PASSED = [r"(\d+)\s+passed", r"Passed!?.*?Passed:\s*(\d+)", r"test result: \w+\. (\d+) passed", r"^ok\s+\S+", ]


def parse_output(text: str, rc: int) -> dict:  # O(o)
    """Best-effort counts and failing test names from a runner's plain output (no JUnit)."""
    lines = [ln.rstrip() for ln in text.splitlines()]
    names: list[str] = []
    for ln in lines:
        for pat in NAME_PATTERNS:
            m = re.match(pat, ln)
            if m and re.search(r"\s[\d.]+s$", m.group(1)):  # go's `FAIL<TAB>package<TAB>0.003s` summary line is not a test
                break
            if m and m.group(1) not in names and len(names) < 50:
                names.append(m.group(1)[:120])
                break
    blob = "\n".join(lines)
    passed = sum(int(x) for x in re.findall(r"(\d+)\s+passed", blob)) + sum(int(x) for x in re.findall(r"Passed:\s*(\d+)", blob))  # vitest, jest, cargo, dotnet
    passed += len(re.findall(r"^ok\s+\S+", blob, re.M))  # go: one line per package
    failed = sum(int(x) for x in re.findall(r"(\d+)\s+failed", blob)) + sum(int(x) for x in re.findall(r"Failed:\s*(\d+)", blob))
    failed = max(failed, len(names) if rc else 0)
    skipped = 0
    skipped = sum(int(x) for x in re.findall(r"(\d+)\s+skipped", blob))
    if rc and not failed:  # a red exit with nothing parsed is one failure
        failed = 1
    if not rc and not (passed or skipped):  # green exit, nothing parsed: one pass so the run counts as green
        passed = 1
    return {"passed": passed, "failed": failed, "skipped": skipped, "names": names, "tail": lines[-40:]}


def main(argv=None):  # O(m + f)
    ap = argparse.ArgumentParser(description=__doc__.split(chr(10))[0])
    ap.add_argument("--fast", nargs="*", default=[])
    ap.add_argument("--root", default=".")
    a = ap.parse_args(argv)
    print(json.dumps(resolve(a.root, a.fast)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
