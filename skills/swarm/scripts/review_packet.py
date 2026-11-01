"""Review packet: one size-capped file the reviewer reads in a single turn.

    superlite-cli plan packet --plan PLAN.md [--root .] [--log F] [--probes DIR] [--out F]
                              [--max-tokens 20000] [--tests F] [--base REF]

Reads probe failures from the run folder's probes/probe-results.json and the test summary from
test-summary.json (written by run_tests.py) by default. Incidents are read straight from the
JSONL format, never through incident.py. Review Focus section lines: `- <package>: deep, numeric`.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402
import planfile  # noqa: E402
import state  # noqa: E402
from review_contract import check_interfaces, rel_to, review_focus  # noqa: E402

CATEGORIES = ("deep", "numeric", "encoding", "protocol", "file", "state")
CHARS_PER_TOKEN = 1.9  # 20k tokens ~ 38k chars
RESOLVED = {"fixed", "resolved", "closed", "wontfix", "accepted"}
PASS_STATUSES = {"pass", "passed", "ok"}
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "tests",
             "test", "build", "dist", ".pytest_cache"}
EXCERPT_FILE_LINES = 120
EXCERPT_TOTAL_LINES = 600


def _incident(ln: str) -> dict | None:
    """One log line as an incident entry, or None when it is not one."""
    try:
        e = json.loads(ln)
    except ValueError:
        return None
    if not isinstance(e, dict) or "file" not in e:
        return None
    e["file"] = str(e.get("file", "")).replace("\\", "/")
    try:
        e["line"] = int(e.get("line") or 0)
    except (TypeError, ValueError):
        e["line"] = 0
    return e


def read_incidents(path: Path) -> list[dict]:  # O(file size) total, single pass
    if not path.is_file():
        return []
    lines = (ln.strip() for ln in path.read_text(encoding="utf-8", errors="replace").splitlines())
    return [e for e in map(_incident, (ln for ln in lines if ln)) if e is not None]


def excerpt(root: Path, file: str, line: int, ctx: int = 12) -> str:  # O(B) per call (re-reads whole file every call)
    f = root / file
    if not f.is_file():
        f = root / "src" / file
    if not f.is_file():
        return "(file not found)"
    lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
    if line <= 0:
        lo, hi = 1, min(len(lines), 2 * ctx + 1)
    else:
        lo, hi = max(1, line - ctx), min(len(lines), line + ctx)
    rows = []
    for n in range(lo, hi + 1):
        mark = ">" if n == line else " "
        rows.append(f"{mark}{n:>4} | {lines[n - 1][:160]}")
    return "\n".join(rows) if rows else "(empty file)"


@dataclass
class Item:
    section: str  # incidents | probes | uncovered | contract
    rank: tuple
    title: str
    body: str
    file: str = ""
    line: int = 0


def incident_items(entries: list[dict]) -> list[Item]:  # O(K) total
    groups: dict[tuple[str, int], list[dict]] = {}
    for e in entries:
        if e.get("kind") != "verified":
            groups.setdefault((e["file"], e["line"]), []).append(e)
    items: list[Item] = []
    for (file, line), es in groups.items():
        repeated = len(es) >= 2
        unresolved = any(str(e.get("resolution", "")).lower() not in RESOLVED for e in es)
        low = any(str(e.get("confidence", "")).lower() == "low" for e in es)
        if not (repeated or unresolved or low):
            continue
        why = [w for w, on in (("repeated", repeated), ("unresolved", unresolved), ("low confidence", low)) if on]
        tier = 0 if repeated else 1 if unresolved else 2
        lines = []
        for e in es[:3]:
            tags = "/".join(str(e[k]) for k in ("kind", "resolution", "confidence", "agent") if e.get(k))
            lines.append(f"- [{tags}] {str(e.get('context', ''))[:200]}")
        if len(es) > 3:
            lines.append(f"- (+{len(es) - 3} more entries at this location)")
        items.append(Item("incidents", (1, tier, -len(es), file, line),
                          f"{file}:{line}  ({', '.join(why)}; {len(es)} entr{'y' if len(es) == 1 else 'ies'})",
                          "\n".join(lines), file, line))
    return items


def read_probe_failures(probes: Path) -> list[Item]:  # O(R) total
    f = probes if probes.is_file() else probes / "probe-results.json"
    if not f.is_file():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except ValueError:
        return []
    results = data.get("results", []) if isinstance(data, dict) else data
    items: list[Item] = []
    for r in results if isinstance(results, list) else []:
        if not isinstance(r, dict) or str(r.get("status", "pass")).lower() in PASS_STATUSES:
            continue
        file = str(r.get("file", "")).replace("\\", "/")
        line = int(r.get("line") or 0)
        detail = str(r.get("detail") or r.get("message") or r.get("probe") or "")[:200]
        items.append(Item("probes", (3, str(r.get("category", "")), file, line),
                          f"probe {r.get('category', '?')} {r.get('status')}: {file}:{line}",
                          f"- {detail}" if detail else "", file, line))
    return items


def focus_packages(plan: planfile.Plan) -> list[tuple[str, list[str]]]:  # O(focus lines * m) total
    out: list[tuple[str, list[str]]] = []
    for ln in review_focus(plan).splitlines():
        m = re.match(r"^\s*(?:[-*]\s*)?\**`?([\w./-]+)`?\**\s*[:—-]\s*(.+)$", ln)
        if not m:
            continue
        cats = [c for c in re.split(r"[,·/;\s]+", m.group(2).lower().replace("**", "")) if c in CATEGORIES]
        if cats:
            out.append((Path(m.group(1)).name, list(dict.fromkeys(cats))))
    return out


def package_dir(root: Path, pkg: str) -> Path | None:
    for cand in (root / "src" / pkg, root / pkg):
        if cand.is_dir():
            return cand
    return None


def _first_def_line(path: Path) -> int:  # O(B) read + parse total
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return 1
    return next((n.lineno for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))), 1)


def _largest_module(root: Path, pkg: str) -> tuple[str, int]:  # O(F_pkg*stat + B) total
    """File and first def line of the biggest module in a package, for the excerpt of an unverified item."""
    d = package_dir(root, pkg)
    if not d:
        return "", 0
    cands = sorted((p for p in d.rglob("*.py") if p.name != "__init__.py"
                    and not any(part in SKIP_DIRS or part.startswith(".") for part in p.relative_to(d).parts)),
                   key=lambda p: -p.stat().st_size)
    if not cands:
        return "", 0
    return rel_to(root, cands[0]), _first_def_line(cands[0])


def uncovered_items(plan: planfile.Plan, root: Path, entries: list[dict]) -> list[Item]:  # O(pkgs*cats*(V + F_pkg*stat + B)) total
    verified = [e for e in entries if e.get("kind") == "verified"]
    items: list[Item] = []
    for pkg, cats in focus_packages(plan):
        files = [e for e in verified if pkg in re.split(r"[/.]", e["file"].replace(".py", ""))
                 or e["file"].replace(".py", "") == pkg]
        for cat in cats:
            hit = any(str(e.get("category", "")).lower() == cat
                      or re.search(rf"\b{cat}\b", str(e.get("context", "")).lower()) for e in files)
            if hit:
                continue
            file, line = _largest_module(root, pkg)
            items.append(Item("uncovered", (4, pkg, cat),
                              f"package {pkg}: no `verified` entry for category `{cat}`",
                              "- Unverified: judge from the excerpt or list it as a finding.", file, line))
    return items


def package_of(root: Path, f: Path) -> str | None:  # O(d) stat/resolve syscalls total
    try:
        rel = f.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    parts = list(rel.parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
    elif parts and not (root / parts[0] / "__init__.py").is_file():
        return None
    return parts[0] if len(parts) > 1 else None


def source_files(root: Path) -> list[Path]:  # O(tree size after pruning) total
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        out.extend(Path(dirpath) / f for f in filenames if f.endswith(".py"))
    return out


def _import_aliases(tree: ast.AST) -> dict[str, str]:  # O(nodes) total
    alias: dict[str, str] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for al in n.names:
                alias[(al.asname or al.name).split(".")[0]] = al.name if al.asname else al.name.split(".")[0]
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            for al in n.names:
                alias[al.asname or al.name] = f"{n.module}.{al.name}"
    return alias


def _call_targets(tree: ast.AST, alias: dict[str, str]):  # O(nodes) total
    """Yield (line, dotted callee) for each call whose root name is an imported name."""
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        chain, cur = [], n.func
        while isinstance(cur, ast.Attribute):
            chain.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name) and cur.id in alias:
            yield n.lineno, ".".join([alias[cur.id]] + chain[::-1])


def cross_package_calls(root: Path) -> list[str]:  # O(S*B) total, reads+parses every source file
    files = source_files(root)
    pkgs = {p for p in (package_of(root, f) for f in files) if p}
    rows: set[tuple[str, int, str, str, str]] = set()
    for f in files:
        own = package_of(root, f)
        if not own:
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except (SyntaxError, OSError):
            continue
        for lineno, full in _call_targets(tree, _import_aliases(tree)):
            target = full.split(".")[0]
            if target in pkgs and target != own:
                rows.add((rel_to(root, f), lineno, own, target, full))
    return [f"- {f}:{ln}  {a} -> {b}: {full}()" for f, ln, a, b, full in sorted(rows)]


def _base_ref(root: Path, ref: str | None) -> str:
    """The ref to diff against: `ref`, else the root commit, else HEAD."""
    if ref is not None:
        return ref
    roots = base.git("rev-list", "--max-parents=0", "HEAD", cwd=root, check=False).stdout.split()
    return roots[0] if roots else "HEAD"


def git_diff_stat(root: Path, ref: str | None) -> str:  # O(X) + up to 2 git subprocess spawns total
    if not (root / ".git").exists():
        return "(not a git repository)"
    try:
        r = base.git("diff", "--stat", _base_ref(root, ref), cwd=root, check=False)
    except base.GitError as e:
        print(f"packet: diff stat unavailable: {e}", file=sys.stderr)
        return "(diff stat unavailable)"
    if r.returncode != 0:
        return "(diff stat unavailable)"
    return r.stdout.strip() or "(no changes)"


def source_excerpts(root: Path, ref: str | None, files: list[str],  # O(X) + one git spawn per file shown
                    per_file: int = EXCERPT_FILE_LINES, total: int = EXCERPT_TOTAL_LINES) -> str:
    """Changed hunks (`git diff <ref> -- file`) of the hot-spot files, bounded per file and in total."""
    if not (root / ".git").exists():
        return "- (not a git repository)"
    ref = _base_ref(root, ref)
    out: list[str] = []
    left = total
    for n, f in enumerate(files):
        if left <= 0:
            out.append(f"- (excerpts stopped at the {total}-line total cap; {len(files) - n} more file(s) omitted)")
            break
        try:
            r = base.git("diff", "-U3", ref, "--", f, cwd=root, check=False)
        except base.GitError as e:
            print(f"packet: no diff for {f}: {e}", file=sys.stderr)
            continue
        lines = r.stdout.splitlines() if r.returncode == 0 else []
        if not lines:
            continue
        take = lines[:min(per_file, left)]
        left -= len(take)
        out.append(f"### {f}\n```diff\n" + "\n".join(take) + "\n```")
        if len(take) < len(lines):
            why = "per-file" if len(take) == per_file else "total"
            out.append(f"(truncated: {len(take)} of {len(lines)} diff lines shown, {why} cap)")
    return "\n".join(out) or "- none (no changed hunks in the hot-spot files)"


def read_test_summary(path: Path) -> tuple[str, int]:  # O(file size) total
    if not path.is_file():
        return "- no test summary recorded (`run_tests.py --full` runs alongside this review)", 0
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return "- test summary unreadable", 0
    failed = d.get("failures") or d.get("failed_tests") or []
    skipped = d.get("skipped_tests") or []
    nfail = int(d["failed"]) if isinstance(d.get("failed"), int) else len(failed)
    rows = [f"- passed {d.get('passed', '?')} · failed {nfail} · skipped {d.get('skipped', len(skipped))}"]
    rows += [f"- FAILING: {t}" for t in failed[:30]]
    rows += [f"- SKIPPED: {t}" for t in skipped[:30]]
    return "\n".join(rows), nfail


def render_item(root: Path, it: Item, shown: set[tuple[str, int]], idx: str) -> str:  # O(B) per item (excerpt re-reads file)
    out = [f"### {idx} {it.title}"]
    if it.body:
        out.append(it.body)
    if it.file:
        key = (it.file, it.line)
        if key in shown:
            out.append("(excerpt shown with the item for the same location)")
        else:
            shown.add(key)
            out.append("```\n" + excerpt(root, it.file, it.line) + "\n```")
    return "\n".join(out) + "\n"


def cap_text(s: str, limit: int, note: str = "(truncated)") -> str:  # O(len(s)) total
    if len(s) <= limit:
        return s
    return s[: max(0, limit - len(note) - 1)].rstrip() + "\n" + note


def _collect(plan: planfile.Plan, root: Path, log: Path, probes: Path):  # O(S*B + I*B + I log I) + 1 python subprocess
    """All ranked items, the contract mismatches that produced some, and how many signatures were checked."""
    entries = read_incidents(log)
    items = incident_items(entries) + read_probe_failures(probes) + uncovered_items(plan, root, entries)
    mismatches, n_checked = check_interfaces(plan, root, None)
    for m in mismatches:
        items.append(Item("contract", (2, m.file, m.line, m.msg), f"{m.file}:{m.line}  part1 {m.task}",
                          f"- {m.msg}", m.file, m.line))
    items.sort(key=lambda i: i.rank)
    return items, mismatches, n_checked


def _head(plan: planfile.Plan, fast: bool, n_open: int, counts: dict, nfail: int) -> list[str]:
    verdict = ("Nothing is open: no open incidents, probe failures, unverified categories, contract mismatches or "
               "failing tests. Reply with a short APPROVED (about 10 s)." if fast else
               f"Open items: {n_open} (incidents {counts['incidents']}, probe failures {counts['probes']}, "
               f"unverified categories {counts['uncovered']}, contract mismatches {counts['contract']}, "
               f"failing tests {nfail}). Excerpts are +-12 lines.")
    return [
        f"# Review packet: {'FAST PATH' if fast else 'REVIEW REQUIRED'}",
        f"plan: {plan.path.name}",
        "Answer in one turn, no tools: a verdict, then findings as `file:line - problem - fix`. "
        "Anything marked unverified is judged from its excerpt or listed as a finding.",
        verdict,
        "",
    ]


def _choose(root: Path, items: list[Item], budget: int):  # O(I*B) total
    """Render items in rank order until the budget is spent; later items are only counted."""
    chosen: list[tuple[Item, str]] = []
    used, shown, stopped = 0, set(), False
    omitted = {"incidents": 0, "probes": 0, "uncovered": 0, "contract": 0}
    for n, it in enumerate(items, 1):
        text = render_item(root, it, shown, f"[{n}]")
        stopped = stopped or used + len(text) > budget
        if stopped:
            omitted[it.section] += 1
            continue
        chosen.append((it, text))
        used += len(text)
    return chosen, omitted


def cmd_packet(a: argparse.Namespace) -> int:  # O(S*B + I*B + I log I) + git/python subprocesses total
    plan = planfile.read(a.plan)
    root = Path(a.root).resolve()
    sl = state.run_dir(root)
    log = Path(a.log) if a.log else sl / "incidents.jsonl"
    probes = Path(a.probes) if a.probes else sl / "probes"
    out = Path(a.out) if a.out else sl / "review-packet.md"
    tests = Path(a.tests) if a.tests else sl / "test-summary.json"
    cap = int(a.max_tokens * CHARS_PER_TOKEN)

    items, mismatches, n_checked = _collect(plan, root, log, probes)
    counts = {s: sum(1 for i in items if i.section == s) for s in ("incidents", "probes", "uncovered", "contract")}
    tests_text, nfail = read_test_summary(tests)
    n_open = sum(counts.values()) + (1 if nfail else 0)
    fast = n_open == 0

    seams = cross_package_calls(root)
    seam_text = cap_text("\n".join(seams) if seams else "- none", 5000,
                         f"- (+ more calls omitted; {len(seams)} total)")
    focus = cap_text(review_focus(plan) or "(plan has no Review Focus section)", 3000)
    diff = cap_text(git_diff_stat(root, a.base), 2000)
    hot: list[str] = []  # hot-spot files: those the ranked items point at, in rank order
    for it in items:
        rel = rel_to(root, root / it.file) if it.file else None
        if rel and rel not in hot:
            hot.append(rel)
    excerpts = cap_text(source_excerpts(root, a.base, hot), max(2000, cap // 3))

    head = _head(plan, fast, n_open, counts, nfail)
    tail = ["## 4. Test summary", tests_text, "",
            "## 5. Cross-package seams (calls from one package into another)", seam_text, "",
            "## 6. Diff stat", "```\n" + diff + "\n```", "",
            "## 7. Review Focus", focus, "",
            "## 8. Source excerpts (changed hunks of the hot-spot files)", excerpts, ""]
    sec_titles = {
        "incidents": "## 1. Open incidents (repeated file:line first, then unresolved, then low confidence)",
        "probes": "## 2. Probe results: failures still open and unverified categories",
        "contract": f"## 3. Contract check: real signatures vs the plan's Produces ({n_checked} checked, "
                    f"{len(mismatches)} mismatch(es))",
    }
    fixed_len = len("\n".join(head + tail)) + 400 + sum(len(t) + 40 for t in sec_titles.values())
    chosen, omitted = _choose(root, items, cap - fixed_len)
    parts = list(head)
    for sec in ("incidents", "probes", "contract"):
        parts.append(sec_titles[sec])
        sec_items = [t for it, t in chosen if it.section == sec or (sec == "probes" and it.section == "uncovered")]
        parts.extend(sec_items or ["- none\n"])
        om = omitted[sec] + (omitted["uncovered"] if sec == "probes" else 0)
        if om:
            parts.append(f"- ({om} lower-ranked item(s) omitted to stay within the size cap)\n")
    parts.extend(tail)
    text = cap_text("\n".join(parts), cap, "(packet truncated at the size cap)")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {out} ({len(text)} chars, ~{int(len(text) / CHARS_PER_TOKEN)} tokens) - "
          f"{'FAST PATH' if fast else f'{n_open} open item(s)'}")
    return 0
