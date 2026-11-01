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
import json
import os
import re
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
    files = " ".join(f'"{p}"' if " " in p else p for p in paths)
    template = cfg.get("test_file") or cfg["test"]
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
