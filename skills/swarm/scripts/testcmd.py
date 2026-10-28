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
# Complexity notation:
#   m = marker files checked, f = files walked for the Python test-file scan (bounded), o = output lines
import argparse  # O(1)
import json  # O(1)
import os  # O(1)
import re  # O(1)
import sys  # O(1)
from pathlib import Path  # O(1)

CONFIG = ".superlite/test-cmd.json"  # O(1)
SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "target", "bin", "obj", "dist", "build", "__pycache__", ".superlite"}  # O(1)
WALK_LIMIT = 400  # O(1)


def read_config(root: Path):  # O(file)
    p = root / CONFIG  # O(1)
    try:  # O(1)
        d = json.loads(p.read_text(encoding="utf-8"))  # O(file)
    except (OSError, ValueError):  # O(1)
        return None  # O(1)
    return d if isinstance(d, dict) and d.get("test") else None  # O(1)


def _pkg(root: Path):  # O(file)
    try:  # O(1)
        return json.loads((root / "package.json").read_text(encoding="utf-8"))  # O(file)
    except (OSError, ValueError):  # O(1)
        return None  # O(1)


def _has_pytest_files(root: Path) -> bool:  # O(f)
    seen = 0  # O(1)
    for dp, dns, fns in os.walk(root):  # O(f)
        dns[:] = [d for d in dns if d not in SKIP_DIRS]  # O(dirs)
        for fn in fns:  # O(files)
            seen += 1  # O(1)
            if re.match(r"^(test_.*|.*_test)\.py$", fn):  # O(1)
                return True  # O(1)
        if seen > WALK_LIMIT:  # O(1)
            break  # O(1)
    return False  # O(1)


def detect(root: Path):  # O(m + f)
    """{"language","test","test_file","junit","setup"} from marker files, or None. `python` returns the pytest marker."""
    pkg = _pkg(root)  # O(file)
    if pkg and (pkg.get("scripts") or {}).get("test"):  # O(1)
        deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}  # O(deps)
        lang = "typescript" if (root / "tsconfig.json").exists() or "typescript" in deps else "javascript"  # O(1)
        test, test_file, junit, env = "npm test", "npm test -- {files}", "", {}  # O(1)
        if "vitest" in deps:  # O(1)
            test = "npm test -- --reporter=default --reporter=junit --outputFile.junit={junit}"  # O(1)
            test_file = "npm test -- {files} --reporter=default --reporter=junit --outputFile.junit={junit}"  # O(1)
        elif "jest-junit" in deps and "jest" in deps:  # O(1)
            test = "npm test -- --reporters=default --reporters=jest-junit"  # O(1)
            test_file = "npm test -- {files} --reporters=default --reporters=jest-junit"  # O(1)
            env = {"JEST_JUNIT_OUTPUT_FILE": "{junit}"}  # O(1)
        return {"language": lang, "test": test, "test_file": test_file, "junit": "{junit}" if "{junit}" in test else "", "env": env, "setup": ""}  # O(1)
    if (root / "go.mod").exists():  # O(1)
        return {"language": "go", "test": "go test ./...", "test_file": "go test {files}", "junit": "", "setup": ""}  # O(1)
    if (root / "Cargo.toml").exists():  # O(1)
        return {"language": "rust", "test": "cargo test", "test_file": "cargo test {files}", "junit": "", "setup": ""}  # O(1)
    names = [p.name for p in root.iterdir()] if root.is_dir() else []  # O(entries)
    if any(n.endswith((".sln", ".csproj")) for n in names):  # O(entries)
        return {"language": "csharp", "test": "dotnet test", "test_file": "dotnet test {files}", "junit": "", "setup": ""}  # O(1)
    if (any((root / m).exists() for m in ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini", "conftest.py", "setup.py"))
            or _has_pytest_files(root)):  # O(m + f)
        return {"language": "python", "test": None, "test_file": None, "junit": "", "setup": ""}  # pytest path of run_tests | O(1)
    return None  # O(1)


def _raw_config(root: Path):  # O(file)
    p = root / CONFIG  # O(1)
    try:  # O(1)
        d = json.loads(p.read_text(encoding="utf-8"))  # O(file)
    except (OSError, ValueError):  # O(1)
        return None  # O(1)
    return d if isinstance(d, dict) else None  # O(1)


def _related_candidates(root: Path, src: str) -> list[str]:  # O(1)
    """Paths to try for a source file: config `related` template first, else name convention."""
    src = src.replace("\\", "/")  # O(len)
    stem = Path(src).stem  # O(1)
    raw = _raw_config(root)  # O(file)
    related = (raw or {}).get("related")  # O(1)
    if isinstance(related, str) and "{src}" in related:  # O(1)
        return [related.replace("{src}", stem).replace("\\", "/")]  # O(1)
    # Python (and similar) name convention: src/foo.py -> tests/test_foo.py
    return [f"tests/test_{stem}{Path(src).suffix}"]  # O(1)


def resolve_related(root: str, src: str) -> list[str]:  # O(file)
    """Map one source path to existing related test paths. Raises FileNotFoundError if none."""
    root_p = Path(root)  # O(1)
    src = src.replace("\\", "/")  # O(len)
    found = []  # O(1)
    for cand in _related_candidates(root_p, src):  # O(1)
        if (root_p / cand).is_file():  # O(1)
            found.append(cand.replace("\\", "/"))  # O(1)
    if not found:  # O(1)
        raise FileNotFoundError(f"no tests found for {src}")  # O(1)
    return found  # O(1)


def is_test_path(path: str) -> bool:  # O(1)
    """True when path looks like a test file (keep as --fast arg; do not resolve_related)."""
    p = path.replace("\\", "/")  # O(len)
    name = Path(p).name  # O(1)
    if re.match(r"^(test_.*|.*_test)\.[^.]+$", name):  # O(1)
        return True  # O(1)
    return p.startswith("tests/") or "/tests/" in f"/{p}"  # O(1)


def resolve(root=".", paths=()):  # O(m + f)
    """-> {"language", "python": bool, "command": str|None, "junit": str, "env": {}, "setup": str, "source"} or None."""
    root = Path(root)  # O(1)
    cfg = read_config(root)  # O(file)
    src = "test-cmd.json"  # O(1)
    if cfg is None:  # O(1)
        cfg, src = detect(root), "markers"  # O(m + f)
    if cfg is None:  # O(1)
        return None  # O(1)
    if cfg.get("test") is None and cfg.get("language") == "python":  # O(1)
        return {"language": "python", "python": True, "command": None, "junit": "", "env": {}, "setup": "", "source": src}  # O(1)
    files = " ".join(f'"{p}"' if " " in p else p for p in paths)  # O(p)
    template = cfg.get("test_file") or cfg["test"]  # O(1)
    command = template.replace("{files}", files) if paths else cfg["test"]  # O(1)
    if paths and "{files}" not in template:  # a test_file without {files} runs everything | O(1)
        command = template  # O(1)
    return {"language": cfg.get("language") or "unknown", "python": False, "command": command, "junit": cfg.get("junit") or "",  # O(1)
            "env": cfg.get("env") or {}, "setup": cfg.get("setup") or "", "source": src}


NAME_PATTERNS = [r"^--- FAIL: (\S+)", r"^test (\S+) \.\.\. FAILED", r"^\s*(?:FAIL|✗|×|✕)\s+(.+?)\s*$", r"^\s*Failed\s+(\S+)",  # O(1)
                 r"^\s*\d+\)\s+(.+?)\s*$"]
COUNT_PASSED = [r"(\d+)\s+passed", r"Passed!?.*?Passed:\s*(\d+)", r"test result: \w+\. (\d+) passed", r"^ok\s+\S+", ]  # O(1)


def _parse_xcsift_json(text: str) -> dict | None:  # O(o)
    """xcsift (and similar) emit one JSON object with passed_tests / failed_tests arrays or counts."""
    blob = text.strip()  # O(o)
    if not blob.startswith("{"):  # O(1)
        # Sometimes the JSON is the last non-empty line after xcodebuild noise
        for ln in reversed(blob.splitlines()):  # O(o)
            ln = ln.strip()  # O(w)
            if ln.startswith("{") and ln.endswith("}"):  # O(1)
                blob = ln  # O(1)
                break  # O(1)
        else:  # O(1)
            return None  # O(1)
    try:  # O(1)
        data = json.loads(blob)  # O(o)
    except ValueError:  # O(1)
        return None  # O(1)
    if not isinstance(data, dict):  # O(1)
        return None  # O(1)
    # Prefer explicit test arrays (xcsift) over a nested "summary" object that is itself one row.
    # Mixed reporters may use ints for some *_tests fields and arrays for others — count each correctly.
    passed_list = data.get("passed_tests")  # O(1)
    failed_list = data.get("failed_tests")  # O(1)
    skipped_list = data.get("skipped_tests")  # O(1)

    def _xcs_count(val, alt_key):  # O(1)
        if isinstance(val, list):  # O(1)
            return len(val)  # O(1)
        if isinstance(val, int):  # O(1)
            return val  # O(1)
        return int(data.get(alt_key) or 0)  # O(1)

    if isinstance(passed_list, (list, int)) or isinstance(failed_list, (list, int)) or isinstance(skipped_list, (list, int)):  # O(1)
        if isinstance(passed_list, list) or isinstance(failed_list, list) or isinstance(skipped_list, list):  # O(1)
            names = []  # O(1)
            if isinstance(failed_list, list):  # O(1)
                for item in failed_list[:50]:  # O(f)
                    if isinstance(item, str):  # O(1)
                        names.append(item[:120])  # O(1)
                    elif isinstance(item, dict):  # O(1)
                        names.append(str(item.get("name") or item.get("test") or item)[:120])  # O(1)
            return {  # O(1)
                "passed": _xcs_count(passed_list, "passed"),  # O(1)
                "failed": _xcs_count(failed_list, "failed"),  # O(1)
                "skipped": _xcs_count(skipped_list, "skipped"),  # O(1)
                "names": names,  # O(1)
            }  # O(1)
    # Count fields used by some reporters (avoid treating a whole-run summary object as passed: 1)
    for pk, fk, sk in (("passed_tests", "failed_tests", "skipped_tests"),  # O(1)
                       ("passed", "failed", "skipped"),
                       ("tests_passed", "tests_failed", "tests_skipped")):
        if isinstance(data.get(pk), int) or isinstance(data.get(fk), int):  # O(1)
            return {  # O(1)
                "passed": int(data.get(pk) or 0),  # O(1)
                "failed": int(data.get(fk) or 0),  # O(1)
                "skipped": int(data.get(sk) or 0),  # O(1)
                "names": [],  # O(1)
            }  # O(1)
    return None  # O(1)


def parse_output(text: str, rc: int) -> dict:  # O(o)
    """Best-effort counts and failing test names from a runner's plain output (no JUnit)."""
    xcs = _parse_xcsift_json(text)  # O(o)
    if xcs is not None:  # O(1)
        passed, failed, skipped, names = xcs["passed"], xcs["failed"], xcs["skipped"], xcs["names"]  # O(1)
        if rc and not failed:  # O(1)
            failed = 1  # O(1)
        if not rc and not (passed or skipped or failed):  # O(1)
            passed = 1  # O(1)
        lines = [ln.rstrip() for ln in text.splitlines()]  # O(o)
        return {"passed": passed, "failed": failed, "skipped": skipped, "names": names, "tail": lines[-40:]}  # O(1)
    lines = [ln.rstrip() for ln in text.splitlines()]  # O(o)
    names: list[str] = []  # O(1)
    for ln in lines:  # O(o)
        for pat in NAME_PATTERNS:  # O(1)
            m = re.match(pat, ln)  # O(w)
            if m and re.search(r"\s[\d.]+s$", m.group(1)):  # go's `FAIL<TAB>package<TAB>0.003s` summary line is not a test | O(1)
                break  # O(1)
            if m and m.group(1) not in names and len(names) < 50:  # O(1)
                names.append(m.group(1)[:120])  # O(1)
                break  # O(1)
    blob = "\n".join(lines)  # O(o)
    passed = sum(int(x) for x in re.findall(r"(\d+)\s+passed", blob)) + sum(int(x) for x in re.findall(r"Passed:\s*(\d+)", blob))  # vitest, jest, cargo, dotnet | O(o)
    passed += len(re.findall(r"^ok\s+\S+", blob, re.M))  # go: one line per package | O(o)
    failed = sum(int(x) for x in re.findall(r"(\d+)\s+failed", blob)) + sum(int(x) for x in re.findall(r"Failed:\s*(\d+)", blob))  # O(o)
    failed = max(failed, len(names) if rc else 0)  # O(1)
    skipped = 0  # O(1)
    skipped = sum(int(x) for x in re.findall(r"(\d+)\s+skipped", blob))  # O(o)
    if rc and not failed:  # a red exit with nothing parsed is one failure | O(1)
        failed = 1  # O(1)
    if not rc and not (passed or skipped):  # green exit, nothing parsed: one pass so the run counts as green | O(1)
        passed = 1  # O(1)
    return {"passed": passed, "failed": failed, "skipped": skipped, "names": names, "tail": lines[-40:]}  # O(1)


def main(argv=None):  # O(m + f)
    ap = argparse.ArgumentParser(description=__doc__.split(chr(10))[0])  # O(1)
    ap.add_argument("--fast", nargs="*", default=[])  # O(1)
    ap.add_argument("--root", default=".")  # O(1)
    a = ap.parse_args(argv)  # O(1)
    print(json.dumps(resolve(a.root, a.fast)))  # O(1)
    return 0  # O(1)


if __name__ == "__main__":  # O(1)
    sys.exit(main())  # O(1)
