#!/usr/bin/env python3
"""Review-side plan tools: adapter, packet, contract.

    pt_review.py adapter  --plan PLAN.md [--root .] [--out .superlite/probes/adapter.py]
    pt_review.py packet   --plan PLAN.md [--root .] [--log F] [--probes DIR] [--out F]
                          [--max-tokens 20000] [--tests F] [--base REF]
    pt_review.py contract --plan PLAN.md [--root .] [--seam Ta,Tb] [--log F]
                          [--timeout 300]

`packet` reads probe failures from `.superlite/probes/probe-results.json` and the test
summary from `.superlite/test-summary.json` (written by run_tests.py) by default. A
`contract --seam` run logs to `.superlite/incidents.jsonl` by default: each mismatch is an
`exception` incident, a pass is a `verified` entry.

`plan_tools.py` dispatches `adapter|packet|contract` to `main(argv)` here; the module
also runs standalone. Stdlib only. Incidents are read straight from the JSONL format
(plan section 6), never through incident.py.

Plan conventions read here (per task section `### Tn ...`):
  **Owns:** a.py, tests/test_a.py          **Consumes:** f(x) -> y (T1)
  **Produces:** pkg.mod.f(a: int, b=1) -> str  ·  Class.method(ref: str) -> list[str]
  **Probe entry:** call=pkg.mod.fn · run=python -m pkg · serve=pkg.server.create_server(Workbook())
  Data contract lines (any line, anchored at the start, bullets allowed):
      POST /api/cells {"ref":"A1"} -> 200 {cells: dict, note?: str}
  Review Focus section lines:  - <package>: deep, numeric, encoding, state
  Task graph: a `%% taskgraph` block of `Tn --> Tm` edges (Tn produces for Tm).
"""  # O(1)
# Complexity notation:
# n = tasks in the plan, L = lines in the plan file, W = bytes of the plan file, H = headings, E = taskgraph edges
# P = Produces signatures, p = params in one signature, C = contract lines, M = fetch/HTTP call sites in a consumer file
# m = chars in one line/field being parsed, o = Owns entries of a task, k = brace alternatives, d = path depth, s = path segments
# F = files scanned, B = bytes in one file, A = AST nodes in one file, S = .py files under root, K = incident log entries
# R = probe results, I = items in the review packet, V = verified incidents, X = bytes of subprocess/output text
# T = server start timeout (s), Mm = mismatches, t = task ids passed in, E_p = Probe entry lines, ctx = excerpt context lines
from __future__ import annotations  # O(1)

import argparse  # O(1)
import ast  # O(1)
import http.client  # O(1)
import json  # O(1)
import os  # O(1)
import re  # O(1)
import shutil  # O(1)
import subprocess  # O(1)
import sys  # O(1)
import tempfile  # O(1)
import time  # O(1)
from dataclasses import dataclass, field  # O(1)
from pathlib import Path  # O(1)

sys.path.insert(0, str(Path(__file__).resolve().parent))  # O(1)
import _pyrun as pyrun  # noqa: E402 | O(1)

CATEGORIES = ("deep", "numeric", "encoding", "protocol", "file", "state")  # O(1)
CHARS_PER_TOKEN = 1.9  # 20k tokens ~ 38k chars (plan section 5) | O(1)
RESOLVED = {"fixed", "resolved", "closed", "wontfix", "accepted"}  # O(1)
PASS_STATUSES = {"pass", "passed", "ok"}  # O(1)
SKIP_DIRS = {".git", ".superlite", ".venv", "venv", "node_modules", "__pycache__", "tests",  # O(1)
             "test", "build", "dist", ".pytest_cache"}  # O(1)
TASK_RE = re.compile(r"^#{2,4}\s+(T\d+[a-z]?)\b(.*)$")  # O(1)
LABEL_RE = re.compile(r"^\s*(?:[-*]\s+)?\*\*([A-Za-z][A-Za-z .]*?):?\*\*:?\s*(.*)$")  # O(1)
EDGE_RE = re.compile(r"\b(T\d+[a-z]?)(?:\[[^\]]*\])?\s*-->\s*(?:\|[^|]*\|\s*)?(T\d+[a-z]?)\b")  # O(1)
HTTP_LINE_RE = re.compile(r"^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/\S*)\s*(.*)$")  # O(1)
TEST_PATH_RE = re.compile(r"(^|/)(tests?/|test_[^/]*$|[^/]*_test\.py$)")  # O(1)
CONSUMER_EXT = {".html", ".htm", ".js", ".mjs", ".ts", ".jsx", ".tsx", ".vue", ".py"}  # O(1)


class ToolError(Exception):  # O(1)
    """A usage or input error; main() prints it and returns 2."""  # O(1)


# --------------------------------------------------------------------------- plan parsing


@dataclass  # O(1)
class Task:  # O(1)
    id: str  # O(1)
    title: str  # O(1)
    start: int  # O(1)
    end: int  # O(1)
    fields: dict[str, list[str]] = field(default_factory=dict)  # label -> value lines | O(1)

    def owns(self) -> list[str]:  # O(o*m*k) total
        out: list[str] = []  # O(1)
        for line in self.fields.get("owns", []):  # O(o) iterations
            for tok in split_top(line, ",·"):  # O(m) tokens per field line
                tok = re.sub(r"\s*\(.*\)\s*$", "", tok.strip().strip("`").strip())  # O(m)
                if tok and " " not in tok and ("." in tok or "/" in tok):  # O(m)
                    out.extend(expand_braces(tok))  # O(k*m) brace expansion
        return out  # O(1)

    def produces(self) -> list[str]:  # O(p*m) total
        items: list[str] = []  # O(1)
        for line in self.fields.get("produces", []):  # O(p) iterations
            line = re.sub(r"^\s*[-*]\s+", "", line)  # O(m)
            items.extend(p.strip() for p in line.split("·") if p.strip())  # O(m) per field line
        return items  # O(1)

    def consumes_ids(self) -> set[str]:  # O(m) total
        return set(re.findall(r"\((T\d+[a-z]?)\)", " ".join(self.fields.get("consumes", []))))  # O(m) join + regex


@dataclass  # O(1)
class Plan:  # O(1)
    path: Path  # O(1)
    lines: list[str]  # O(1)
    tasks: dict[str, Task]  # O(1)
    edges: list[tuple[str, str]]  # O(1)
    headings: list[tuple[int, int, str, int]]  # (line idx, level, title, end idx) | O(1)
    probe_entries: list[str]  # O(1)

    def section_text(self, start: int, end: int) -> str:  # O(end-start) total
        return "\n".join(self.lines[start:end])  # O(end-start)

    def review_focus(self) -> str:  # O(H + section lines) total
        for idx, _lvl, title, end in self.headings:  # O(H) iterations
            if "review focus" in title.lower():  # O(len(title))
                return self.section_text(idx + 1, end).strip()  # O(section lines)
        return ""  # O(1)


def split_top(s: str, seps: str) -> list[str]:  # O(m) total
    out, depth, cur = [], 0, []  # O(1)
    for ch in s:  # O(m) iterations
        if ch in "([{":  # O(1)
            depth += 1  # O(1)
        elif ch in ")]}":  # O(1)
            depth = max(0, depth - 1)  # O(1)
        if ch in seps and depth == 0:  # O(1)
            out.append("".join(cur))  # O(1)
            cur = []  # O(len(cur)) join; O(m) over all
        else:  # O(1)
            cur.append(ch)  # O(1)
    out.append("".join(cur))  # O(len(cur)) join
    return [p for p in out if p.strip()]  # O(m)


def expand_braces(s: str) -> list[str]:  # O(k*m) total
    m = re.search(r"\{([^{}]*)\}", s)  # O(m) regex
    if not m:  # O(1)
        return [s]  # O(1)
    out: list[str] = []  # O(m)
    for alt in m.group(1).split(","):  # O(k) iterations
        out.extend(expand_braces(s[: m.start()] + alt.strip() + s[m.end():]))  # O(m) + recursion
    return out  # O(1)


def parse_plan(path: str | Path) -> Plan:  # O(W + L*m + H^2) total
    p = Path(path)  # O(1)
    if not p.is_file():  # O(1) stat
        raise ToolError(f"plan not found: {p}")  # O(1)
    lines = p.read_text(encoding="utf-8").splitlines()  # O(W) file read + split
    fence_marker = ""  # O(1)
    fence_id = [0] * len(lines)  # O(L) list alloc
    fid = 0  # O(1)
    heads: list[tuple[int, int, str]] = []  # O(1)
    for i, line in enumerate(lines):  # O(L) iterations
        m = re.match(r"^\s*(`{3,}|~{3,})", line)  # O(m) per iteration
        if m:  # O(1)
            if not fence_marker:  # O(1)
                fence_marker = m.group(1)  # O(1)
                fid += 1  # O(1)
                fence_id[i] = fid  # O(1)
                continue  # O(1)
            if line.strip().startswith(fence_marker[0] * len(fence_marker)) and set(line.strip()) <= {fence_marker[0]}:  # O(m) per iteration
                fence_id[i] = fid  # O(1)
                fence_marker = ""  # O(1)
                continue  # O(1)
        if fence_marker:  # O(1)
            fence_id[i] = fid  # O(1)
            continue  # O(1)
        hm = re.match(r"^(#{1,6})\s+(.*)$", line)  # O(m) per iteration
        if hm:  # O(1)
            heads.append((i, len(hm.group(1)), hm.group(2).strip()))  # O(m)
    headings = []  # O(1)
    for n, (i, lvl, title) in enumerate(heads):  # O(H) iterations
        end = len(lines)  # O(1)
        for j, lvl2, _t in heads[n + 1:]:  # O(H) iterations worst, plus O(H) slice copy per heading
            if lvl2 <= lvl:  # O(1)
                end = j  # O(1)
                break  # O(1)
        headings.append((i, lvl, title, end))  # O(1)

    tasks: dict[str, Task] = {}  # O(1)
    for i, lvl, title, end in headings:  # O(H) iterations
        m = TASK_RE.match(lines[i])  # O(m) per iteration
        if m:  # O(1)
            tasks[m.group(1)] = Task(m.group(1), title, i, end)  # O(1)

    probe_entries: list[str] = []  # O(1)
    for tid, t in tasks.items():  # O(n) iterations
        _collect_fields(lines, fence_id, t.start + 1, t.end, t.fields)  # O(task lines * m); O(L) over all tasks
    plan_fields: dict[str, list[str]] = {}  # O(1)
    _collect_fields(lines, fence_id, 0, len(lines), plan_fields)  # O(L*m)
    probe_entries = [v.strip().strip("`").strip() for v in plan_fields.get("probe entry", []) if v.strip()]  # O(entries*m)

    edges: list[tuple[str, str]] = []  # O(1)
    for i, line in enumerate(lines):  # O(L) iterations
        if "%% taskgraph" in line:  # O(m) per iteration
            j = i + 1  # O(1)
            while j < len(lines) and fence_id[j] == fence_id[i]:  # O(fence lines) iterations; O(L) overall
                m = EDGE_RE.search(lines[j])  # O(m) per iteration
                if m:  # O(1)
                    edges.append((m.group(1), m.group(2)))  # O(1)
                j += 1  # O(1)
    return Plan(p, lines, tasks, edges, headings, probe_entries)  # O(1)


def _collect_fields(lines: list[str], fence_id: list[int], start: int, end: int,  # O(range lines * m) total
                    out: dict[str, list[str]]) -> None:  # O(1)
    i = start  # O(1)
    while i < end:  # O(range lines) iterations
        if fence_id[i]:  # O(1) per iteration
            i += 1  # O(m)
            continue  # O(m)
        m = LABEL_RE.match(lines[i])  # O(m) per iteration
        if not m:  # O(m)
            i += 1  # O(m)
            continue  # O(m)
        label = m.group(1).strip().lower()  # O(m)
        vals = [m.group(2).strip()] if m.group(2).strip() else []  # O(m)
        j = i + 1  # O(1)
        while j < end and not fence_id[j] and lines[j].strip() and not LABEL_RE.match(lines[j]) \
                and not lines[j].startswith("#") and re.match(r"^(\s+|[-*]\s+)", lines[j]):  # O(m)
            vals.append(lines[j].strip())  # O(m)
            j += 1  # O(m)
        out.setdefault(label, []).extend(vals)  # O(1)
        i = j  # O(1)


# --------------------------------------------------------------------------- Produces signatures


@dataclass  # O(1)
class Planned:  # O(1)
    task: str  # O(1)
    raw: str  # O(1)
    qual: str  # O(1)
    args_src: str | None  # None => "(...)": params unchecked | O(1)
    ret: str | None  # O(1)
    plan_line: int = 0  # O(1)

    @property  # O(1)
    def parts(self) -> list[str]:  # O(m) total
        return self.qual.split(".")  # O(m)


def parse_sig(item: str) -> tuple[str, str | None, str | None] | None:  # O(m) total
    item = item.strip().strip("`").strip()  # O(m)
    m = re.match(r"^([A-Za-z_][\w.]*)\s*\(", item)  # O(m)
    if not m:  # O(m)
        return None  # O(m)
    depth, end = 0, -1  # O(m)
    for j in range(m.end() - 1, len(item)):  # O(m) iterations
        c = item[j]  # O(m)
        depth += (c in "([{") - (c in ")]}")  # O(m)
        if depth == 0:  # O(m)
            end = j  # O(m)
            break  # O(m)
    if end < 0:  # O(m)
        return None  # O(m)
    args = item[m.end():end].strip()  # O(m)
    rest = item[end + 1:].strip()  # O(m)
    ret = None  # O(m)
    if rest:  # O(m)
        if not rest.startswith("->"):  # O(m)
            return None  # O(m)
        ret = rest[2:].strip().strip("`").strip()  # O(m)
        try:  # O(m)
            ast.parse(ret, mode="eval")  # O(m)
        except SyntaxError:  # O(m)
            ret = None  # O(m)
    if args in ("...", "…"):  # O(m)
        return m.group(1), None, ret  # O(m)
    try:  # O(m)
        ast.parse(f"def _f({args}): pass")  # O(m)
    except SyntaxError:  # O(m)
        return None  # O(m)
    return m.group(1), args, ret  # O(m)


def planned_items(plan: Plan, task_ids: list[str] | None = None) -> list[Planned]:  # O(n*t + P*m) total
    out: list[Planned] = []  # O(1)
    for tid, t in plan.tasks.items():  # O(n) iterations
        if task_ids is not None and tid not in task_ids:  # O(t) list membership, t <= 2
            continue  # O(1)
        for item in t.produces():  # O(p) iterations per task
            parsed = parse_sig(item)  # O(m) per iteration
            if parsed:  # O(1)
                out.append(Planned(tid, item, parsed[0], parsed[1], parsed[2], t.start + 1))  # O(1)
    return out  # O(1)


def _params_from_src(args_src: str) -> list[dict]:  # O(m + p) total
    fn = ast.parse(f"def _f({args_src}): pass").body[0]  # O(m)
    a = fn.args  # type: ignore[attr-defined] | O(m)
    out: list[dict] = []  # O(m)

    def add(arg: ast.arg, kind: str, default: ast.expr | None) -> None:  # O(m) per param
        out.append({"name": arg.arg, "kind": kind,  # O(m)
                    "default": ast.unparse(default) if default is not None else None,  # O(m)
                    "ann": ast.unparse(arg.annotation) if arg.annotation else None})  # O(m)

    pos = list(a.posonlyargs) + list(a.args)  # O(m)
    defaults = [None] * (len(pos) - len(a.defaults)) + list(a.defaults)  # O(m)
    for n, (arg, d) in enumerate(zip(pos, defaults)):  # O(p) iterations
        add(arg, "POSITIONAL_ONLY" if n < len(a.posonlyargs) else "POSITIONAL_OR_KEYWORD", d)  # O(m)
    if a.vararg:  # O(m)
        add(a.vararg, "VAR_POSITIONAL", None)  # O(m)
    for arg, d in zip(a.kwonlyargs, a.kw_defaults):  # O(p) iterations
        add(arg, "KEYWORD_ONLY", d)  # O(m)
    if a.kwarg:  # O(m)
        add(a.kwarg, "VAR_KEYWORD", None)  # O(m)
    return out  # O(m)


class _Norm(ast.NodeTransformer):  # O(1)
    def visit_Attribute(self, node: ast.Attribute):  # typing.List -> List, http.server.X -> X | O(1) total
        return ast.Name(id=node.attr, ctx=ast.Load())  # O(1)

    def visit_Constant(self, node: ast.Constant):  # forward-ref strings | O(m) total
        if isinstance(node.value, str):  # O(m)
            try:  # O(m)
                return self.visit(ast.parse(node.value, mode="eval").body)  # O(m)
            except SyntaxError:  # O(m)
                return node  # O(m)
        return node  # O(m)


def norm_ann(s: str | None) -> str | None:  # O(m) total
    if s is None:  # O(m)
        return None  # O(m)
    try:  # O(m)
        return ast.unparse(_Norm().visit(ast.parse(s, mode="eval")))  # O(m)
    except SyntaxError:  # O(m)
        return s.strip()  # O(m)


def compare_sig(p: Planned, real: dict) -> list[str]:  # O(p^2 + p*m) total
    """Differences between a planned signature and the real one (empty = match)."""  # O(m)
    if real.get("skip"):  # non-Python module found; signature introspection is Python-only | O(m)
        return []  # O(m)
    if not real.get("ok"):  # O(m)
        return [f"{p.qual}: {real.get('error', 'cannot read the real signature')}"]  # O(m)
    msgs: list[str] = []  # O(m)
    if p.args_src is not None:  # O(m)
        want = _params_from_src(p.args_src)  # O(m)
        have = real["params"]  # O(m)
        wn, hn = [x["name"] for x in want], [x["name"] for x in have]  # O(m)
        for n in wn:  # O(p) iterations
            if n not in hn:  # O(p) list membership
                msgs.append(f"{p.qual}: missing parameter '{n}' (planned {p.raw})")  # O(m)
        for n in hn:  # O(p) iterations
            if n not in wn:  # O(p) list membership
                msgs.append(f"{p.qual}: extra parameter '{n}' not in the plan")  # O(m)
        if not msgs and wn != hn:  # O(m)
            msgs.append(f"{p.qual}: parameter order differs, planned ({', '.join(wn)}) real ({', '.join(hn)})")  # O(m)
        if not msgs:  # O(m)
            for w, h in zip(want, have):  # O(p) iterations
                if w["kind"] != h["kind"]:  # O(m)
                    msgs.append(f"{p.qual}: parameter '{w['name']}' is {h['kind'].lower()}, planned {w['kind'].lower()}")  # O(m)
                if w["default"] is not None:  # O(m)
                    if not h["has_default"]:  # O(m)
                        msgs.append(f"{p.qual}: parameter '{w['name']}' lost its default {w['default']}")  # O(m)
                    elif not _same_default(w["default"], h["default"]):  # O(m)
                        msgs.append(f"{p.qual}: parameter '{w['name']}' default is {h['default']}, planned {w['default']}")  # O(m)
                wa, ha = norm_ann(w["ann"]), norm_ann(h["ann"])  # O(m)
                if wa and ha and wa != ha:  # O(m)
                    msgs.append(f"{p.qual}: parameter '{w['name']}' annotated {ha}, planned {wa}")  # O(m)
    wr, hr = norm_ann(p.ret), norm_ann(real.get("returns"))  # O(m)
    if wr and hr and wr != hr:  # O(m)
        msgs.append(f"{p.qual}: returns {hr}, planned {wr}")  # O(m)
    return msgs  # O(m)


def _same_default(planned: str, real_repr: str | None) -> bool:  # O(m) total
    try:  # O(m)
        return repr(ast.literal_eval(planned)) == real_repr  # O(m)
    except (ValueError, SyntaxError):  # O(m)
        return planned.strip() == (real_repr or "").strip()  # O(m)


_INTROSPECT = r'''
import sys, json, importlib, inspect
def ann(a):
    if a is inspect.Parameter.empty:
        return None
    if isinstance(a, str):
        return a
    if isinstance(a, type):
        return a.__qualname__
    return str(a)
out = []
for mod_name, chain in json.loads(sys.argv[1]):
    r = {"ok": False}
    try:
        obj = importlib.import_module(mod_name)
        parent = None
        for i, name in enumerate(chain):
            parent = obj
            try:
                obj = getattr(obj, name)
            except AttributeError:
                raise AttributeError("%s not found in %s" % (".".join(chain[:i + 1]), mod_name))
        sig = inspect.signature(obj)
        params = list(sig.parameters.values())
        if inspect.isclass(parent) and params and params[0].name in ("self", "cls"):
            if not isinstance(inspect.getattr_static(parent, chain[-1]), staticmethod):
                params = params[1:]
        r = {"ok": True, "params": [
            {"name": p.name, "kind": p.kind.name, "has_default": p.default is not p.empty,
             "default": repr(p.default) if p.default is not p.empty else None, "ann": ann(p.annotation)}
            for p in params], "returns": ann(sig.return_annotation), "file": None, "line": 0}
        try:
            r["file"] = inspect.getsourcefile(obj)
            r["line"] = inspect.getsourcelines(obj)[1]
        except (TypeError, OSError):
            pass
    except BaseException as e:
        r = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    out.append(r)
print("@@SIG@@" + json.dumps(out))
'''  # O(1)


def project_env(root: Path, **extra: str) -> dict[str, str]:  # O(env vars) total
    env = dict(os.environ)  # O(1)
    env["PYTHONPATH"] = os.pathsep.join(  # O(1)
        [str(root / "src"), str(root)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))  # O(1)
    env["PYTHONDONTWRITEBYTECODE"] = "1"  # O(1)
    env["PYTHONIOENCODING"] = "utf-8"  # O(1)
    env.update(extra)  # O(1)
    return env  # O(1)


def module_name(root: Path, file: Path) -> str:  # O(d) path syscalls total
    file = file.resolve()  # O(d)
    for base in (root / "src", root):  # O(2) iterations
        try:  # O(d)
            rel = file.relative_to(base.resolve())  # O(d)
        except ValueError:  # O(d)
            continue  # O(d)
        parts = list(rel.with_suffix("").parts)  # O(d)
        if parts and parts[-1] == "__init__":  # O(d)
            parts.pop()  # O(d)
        return ".".join(parts)  # O(d)
    return file.stem  # O(d)


def find_module_file(root: Path, dotted: str) -> Path | None:  # O(d) stat calls (+ a bounded Swift walk)
    rel = dotted.replace(".", "/")  # O(d)
    last = dotted.rsplit(".", 1)[-1]  # O(d)
    for base in (root / "src", root / "Sources", root):  # O(3) iterations
        for cand in (base / f"{rel}.py", base / rel / "__init__.py",  # O(1)
                     base / f"{rel}.swift", base / last / f"{last}.swift", base / f"{last}.swift"):  # O(1)
            if cand.is_file():  # O(d)
                return cand  # O(d)
    # SwiftPM: Sources/<Target>/<Type>.swift — walk one level of Sources for a matching file name
    sources = root / "Sources"  # O(1)
    if sources.is_dir():  # O(1)
        for target in sources.iterdir():  # O(T)
            if not target.is_dir():  # O(1)
                continue  # O(1)
            for name in (f"{rel}.swift", f"{last}.swift"):  # O(1)
                cand = target / name  # O(1)
                if cand.is_file():  # O(1)
                    return cand  # O(1)
    return None  # O(d)


def _top_level_names(path: Path) -> set[str]:  # O(B) read + parse total
    try:  # O(1)
        tree = ast.parse(path.read_text(encoding="utf-8"))  # O(1)
    except (SyntaxError, OSError):  # O(1)
        return set()  # O(1)
    names: set[str] = set()  # O(1)
    for n in tree.body:  # O(top-level stmts) iterations
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):  # O(1)
            names.add(n.name)  # O(1)
        elif isinstance(n, ast.Assign):  # O(1)
            names.update(t.id for t in n.targets if isinstance(t, ast.Name))  # O(1)
    return names  # O(1)


def locate(root: Path, plan: Plan, p: Planned) -> tuple[str, list[str], Path | None]:  # O(d^2 + o*B) per item (re-parses owned files)
    """Return (module, attribute chain, defining file) for a planned item."""  # O(d)
    parts = p.parts  # O(d)
    for k in range(len(parts) - 1, 0, -1):  # O(d) iterations
        f = find_module_file(root, ".".join(parts[:k]))  # O(d) stats (find_module_file)
        if f:  # O(d)
            return module_name(root, f), parts[k:], f  # O(d)
    owned = [root / o for o in plan.tasks[p.task].owns()  # O(o*m)
             if (o.endswith(".py") or o.endswith(".swift")) and not TEST_PATH_RE.search(o)]  # O(d)
    for f in owned:  # O(o) iterations; short module names (`slug.slugify` -> src/sitegen/slug.py) | O(d) per module_name
        mod = module_name(root, f)  # O(d)
        for k in range(len(parts), 0, -1):  # O(d) iterations; full dotted name, or last component for k == 1
            if ".".join(parts[:k]) == mod or (k == 1 and parts[0] == mod.rsplit(".", 1)[-1]):  # O(d)
                return mod, parts[k:], f  # O(d)
    existing = [f for f in owned if f.is_file()]  # O(d)
    for f in existing:  # O(o) iterations
        if parts[0] in _top_level_names(f):  # O(B) re-reads + re-parses the owned file for every planned item
            return module_name(root, f), parts, f  # O(d)
    if existing:  # O(d)
        return module_name(root, existing[0]), parts, existing[0]  # O(d)
    return "", parts, owned[0] if owned else None  # O(d)


def read_real(root: Path, located: list[tuple[str, list[str]]]) -> list[dict]:  # O(P) + 1 subprocess spawn + import time
    out: list[dict] = []  # O(1)
    py_todo: list[tuple[str, list[str]]] = []  # O(1)
    py_slots: list[int] = []  # O(1)
    for i, (m, c) in enumerate(located):  # O(P)
        if not m:  # O(1)
            out.append({"ok": False, "error": "no module found (file missing)"})  # O(1)
            continue  # O(1)
        f = find_module_file(root, m)  # O(d)
        if f is not None and f.suffix.lower() != ".py":  # Swift/other: file exists; Python importlib cannot introspect | O(1)
            out.append({"ok": True, "skip": True, "file": str(f), "params": [], "returns": None, "line": 1,  # O(1)
                        "error": f"skipped: {f.suffix} modules are not introspected (file present)"})  # O(1)
            continue  # O(1)
        out.append({})  # placeholder filled after the Python batch (importlib may still resolve m) | O(1)
        py_todo.append((m, c))  # O(1)
        py_slots.append(i)  # O(1)
    if py_todo:  # O(1)
        try:  # O(1)
            proc = subprocess.run([sys.executable, "-c", _INTROSPECT, json.dumps(py_todo)], cwd=root,  # O(1)
                                  env=project_env(root), capture_output=True, text=True,  # O(1)
                                  encoding="utf-8", errors="replace", timeout=60)  # O(1)
            line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("@@SIG@@")), None)  # O(1)
            results = json.loads(line[7:]) if line else \
                [{"ok": False, "error": f"introspection failed: {proc.stderr.strip()[-200:]}"}] * len(py_todo)  # O(1)
        except subprocess.TimeoutExpired:  # O(1)
            results = [{"ok": False, "error": "introspection timed out"}] * len(py_todo)  # O(1)
        for slot, real in zip(py_slots, results):  # O(P)
            out[slot] = real  # O(1)
    return out  # O(1)


@dataclass  # O(1)
class Mismatch:  # O(1)
    part: int  # O(1)
    file: str  # O(1)
    line: int  # O(1)
    msg: str  # O(1)
    task: str = ""  # O(1)

    def __str__(self) -> str:  # O(m) total
        return f"MISMATCH part{self.part} {self.file}:{self.line} - {self.msg}"  # O(m)


def rel_to(root: Path, f: str | Path | None) -> str:  # O(d) path syscalls total
    if not f:  # O(d)
        return "?"  # O(d)
    try:  # O(d)
        return Path(f).resolve().relative_to(root.resolve()).as_posix()  # O(d)
    except ValueError:  # O(d)
        return str(f).replace("\\", "/")  # O(d)


def check_interfaces(plan: Plan, root: Path, task_ids: list[str] | None) -> tuple[list[Mismatch], int]:  # O(P*(d^2 + o*B + p^2)) + 1 subprocess total
    items = planned_items(plan, task_ids)  # O(1)
    locs = [locate(root, plan, p) for p in items]  # O(1)
    reals = read_real(root, [(m, c) for m, c, _f in locs])  # O(1)
    out: list[Mismatch] = []  # O(1)
    for p, (mod, chain, f), real in zip(items, locs, reals):  # O(P) iterations
        msgs = compare_sig(p, real)  # O(1)
        if msgs:  # O(1)
            file = rel_to(root, real.get("file") or f) if (real.get("file") or f) else \
                rel_to(root, plan.path)  # O(1)
            line = int(real.get("line") or 1) if real.get("file") else (1 if f else p.plan_line)  # O(1)
            for m in msgs:  # O(msgs) iterations
                out.append(Mismatch(1, file, line, f"{p.task} Produces: {m}", p.task))  # O(1)
    return out, len(items)  # O(1)


# --------------------------------------------------------------------------- data contract (part 2)


@dataclass  # O(1)
class ContractLine:  # O(1)
    method: str  # O(1)
    path: str  # O(1)
    body: object | None  # O(1)
    status: int | None  # O(1)
    shape: object | None  # parsed shape or None | O(1)
    plan_line: int  # O(1)


@dataclass  # O(1)
class Call:  # O(1)
    file: str  # O(1)
    line: int  # O(1)
    method: str  # O(1)
    path: str  # O(1)
    prefix: bool  # O(1)


def parse_shape(text: str):  # O(m*depth) total
    text = text.strip()  # O(m)
    if text.startswith("{") and text.endswith("}"):  # O(m)
        shape: dict[str, tuple[bool, object]] = {}  # O(m)
        for part in split_top(text[1:-1], ","):  # O(f) iterations over fields
            key, _, typ = part.partition(":")  # O(m)
            key = key.strip().strip("\"'")  # O(m)
            optional = key.endswith("?")  # O(m)
            shape[key.rstrip("?")] = (optional, parse_shape(typ) if typ.strip() else "any")  # O(m)
        return shape  # O(m)
    if "|" in text and not text.startswith(("{", "list[")):  # O(m)
        return ("union", [parse_shape(t) for t in text.split("|")])  # O(m)
    return text.strip("`\"'").lower()  # O(m)


_TYPE_CHECKS = {  # O(1)
    "str": lambda v: isinstance(v, str), "string": lambda v: isinstance(v, str),  # O(1)
    "int": lambda v: isinstance(v, int) and not isinstance(v, bool),  # O(1)
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),  # O(1)
    "float": lambda v: isinstance(v, float) or (isinstance(v, int) and not isinstance(v, bool)),  # O(1)
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),  # O(1)
    "bool": lambda v: isinstance(v, bool), "boolean": lambda v: isinstance(v, bool),  # O(1)
    "list": lambda v: isinstance(v, list), "array": lambda v: isinstance(v, list),  # O(1)
    "dict": lambda v: isinstance(v, dict), "object": lambda v: isinstance(v, dict),  # O(1)
    "null": lambda v: v is None, "none": lambda v: v is None, "any": lambda v: True,  # O(1)
}  # O(1)


def check_shape(value, shape, where: str = "$") -> list[str]:  # O(size of value + shape), arrays capped at 20 items
    if isinstance(shape, dict):  # O(1)
        if not isinstance(value, dict):  # O(1)
            return [f"{where} is {type(value).__name__}, expected object"]  # O(1)
        probs: list[str] = []  # O(1)
        for key, (optional, sub) in shape.items():  # O(shape keys) iterations
            if key not in value:  # O(1)
                if not optional:  # O(1)
                    probs.append(f"{where} lacks required key '{key}'")  # O(1)
                continue  # O(1)
            probs.extend(check_shape(value[key], sub, f"{where}.{key}"))  # O(1)
        return probs  # O(1)
    if isinstance(shape, tuple) and shape and shape[0] == "union":  # O(1)
        alts = [check_shape(value, s, where) for s in shape[1]]  # O(1)
        return [] if any(not a for a in alts) else [f"{where} matches none of the allowed types"]  # O(1)
    if isinstance(shape, str):  # O(1)
        m = re.match(r"^(?:list|array)\[(.+)\]$", shape)  # O(1)
        if m:  # O(1)
            if not isinstance(value, list):  # O(1)
                return [f"{where} is {type(value).__name__}, expected list"]  # O(1)
            probs = []  # O(1)
            for n, item in enumerate(value[:20]):  # O(20) iterations max
                probs.extend(check_shape(item, parse_shape(m.group(1)), f"{where}[{n}]"))  # O(1)
            return probs  # O(1)
        check = _TYPE_CHECKS.get(shape)  # O(1)
        if check and not check(value):  # O(1)
            return [f"{where} is {type(value).__name__}, expected {shape}"]  # O(1)
    return []  # O(1)


def data_contract(plan: Plan, task_ids: list[str]) -> list[ContractLine]:  # O(L*m + H) total
    ranges = [(plan.tasks[t].start, plan.tasks[t].end) for t in task_ids if t in plan.tasks]  # O(t)
    ranges += [(i, end) for i, _l, title, end in plan.headings  # O(H) iterations
               if "contract" in title.lower() and not TASK_RE.match(plan.lines[i])]  # O(m) per heading
    seen: set[int] = set()  # O(m)
    out: list[ContractLine] = []  # O(m)
    for s, e in ranges:  # O(ranges) iterations
        for i in range(s, e):  # O(range lines) iterations
            if i in seen:  # O(1) set lookup
                continue  # O(m)
            seen.add(i)  # O(m)
            raw = re.sub(r"^\s*[-*]\s+", "", plan.lines[i]).strip().strip("`").strip()  # O(m) per iteration
            m = HTTP_LINE_RE.match(raw)  # O(m) per iteration
            if not m:  # O(m)
                continue  # O(m)
            rest = m.group(3).strip().rstrip("`")  # O(m)
            left, arrow, right = re.split(r"(->|→)", rest, maxsplit=1) if re.search(r"->|→", rest) \
                else (rest, "", "")  # O(m)
            body = None  # O(m)
            if left.strip():  # O(m)
                try:  # O(m)
                    body = json.loads(left.strip())  # O(m)
                except ValueError:  # O(m)
                    body = None  # O(m)
            status, shape = None, None  # O(m)
            sm = re.match(r"^\s*(\d{3})\s*(.*)$", right)  # O(m)
            if sm:  # O(m)
                status = int(sm.group(1))  # O(m)
                if sm.group(2).strip():  # O(m)
                    shape = parse_shape(sm.group(2).strip())  # O(m)
            out.append(ContractLine(m.group(1), m.group(2), body, status, shape, i + 1))  # O(m)
    return out  # O(m)


def _path_clean(p: str) -> tuple[str, bool]:  # O(m) total
    prefix = False  # O(m)
    cut = re.search(r"\$\{|\{\{|[?#]", p)  # O(m)
    if cut:  # O(m)
        prefix = cut.group(0) in ("${", "{{")  # O(m)
        p = p[: cut.start()]  # O(m)
    return p, prefix  # O(m)


def scan_calls(root: Path, files: list[str]) -> list[Call]:  # O(F*B*M) total (line_of rescans text per match)
    calls: list[Call] = []  # O(1)
    for rel in files:  # O(F) iterations
        f = root / rel  # O(1)
        if f.suffix not in CONSUMER_EXT or TEST_PATH_RE.search(rel) or not f.is_file():  # O(d) stat + regex
            continue  # O(1)
        text = f.read_text(encoding="utf-8", errors="replace")  # O(B) file read

        def line_of(pos: int) -> int:  # O(1)
            return text.count("\n", 0, pos) + 1  # O(B) per call, rescans text from start

        for m in re.finditer(r"\bfetch\(\s*([`'\"])(/[^`'\"]*)", text):  # O(B) regex scan; M matches
            nxt = text.find("fetch(", m.end())  # O(gap to next fetch) find
            window = text[m.end(): nxt if nxt != -1 else m.end() + 300][:300]  # O(B) slice copy worst, then [:300]
            mm = re.search(r"method\s*:\s*[`'\"](\w+)", window)  # O(1)
            path, prefix = _path_clean(m.group(2))  # O(1)
            calls.append(Call(rel, line_of(m.start()), (mm.group(1) if mm else "GET").upper(), path, prefix))  # O(B) via line_of
        for m in re.finditer(r"\b(?:axios|requests|session|client|http)\.(get|post|put|patch|delete|head)\(\s*"  # O(B) regex scan; M matches
                             r"f?([`'\"])(?:https?://[^/`'\"]+)?(/[^`'\"]*)", text):  # O(1)
            path, prefix = _path_clean(m.group(3))  # O(1)
            if f.suffix == ".py" and "{" in m.group(3):  # O(1)
                path, prefix = m.group(3).split("{", 1)[0], True  # O(1)
            calls.append(Call(rel, line_of(m.start()), m.group(1).upper(), path, prefix))  # O(B) via line_of
        for m in re.finditer(r"\.request\(\s*(['\"])(\w+)\1\s*,\s*f?(['\"])(/[^'\"]*)", text):  # O(B) regex scan; M matches
            path, prefix = _path_clean(m.group(4))  # O(1)
            calls.append(Call(rel, line_of(m.start()), m.group(2).upper(), path, prefix))  # O(B) via line_of
    return calls  # O(1)


def _segments(path: str) -> list[str]:  # O(m) total
    return [s for s in path.strip("/").split("/")]  # O(m)


def _is_param(seg: str) -> bool:  # O(m) total
    return bool(re.fullmatch(r"\{[^}]*\}|:[\w]+|<[^>]*>", seg))  # O(m)


def call_in_contract(call: Call, contract: list[ContractLine]) -> bool:  # O(C*s) per call, s = path segments
    cs = _segments(call.path)  # O(C) iterations
    for c in contract:  # O(C) iterations
        if c.method != call.method:  # O(s)
            continue  # O(s)
        if call.prefix:  # the call path stops at a template literal, e.g. /api/cells/${ref} | O(s)
            if c.path.startswith(call.path):  # O(s)
                return True  # O(s)
            continue  # O(s)
        ks = _segments(c.path)  # O(s)
        if len(ks) == len(cs) and all(_is_param(k) or k == s for k, s in zip(ks, cs)):  # O(s)
            return True  # O(s)
    return False  # O(s)


_SERVE_SNIPPET = r'''
def _make_server(target, args_src):
    import importlib, inspect
    mod_name, _, fn_name = target.rpartition(".")
    mod = importlib.import_module(mod_name)
    fn = getattr(mod, fn_name)
    def _cap(*a, **k):
        return a, k
    pos, kw = eval("_cap(%s)" % args_src, dict(vars(mod), _cap=_cap)) if args_src is not None else ((), {})
    sig = inspect.signature(fn)
    if "port" in sig.parameters and "port" not in kw:
        kw["port"] = 0
    if args_src is None:
        need = [p.name for p in sig.parameters.values() if p.name != "port"
                and p.default is p.empty and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)]
        if need:
            raise RuntimeError("serve factory needs arguments %s; write serve=%s(...) in the Probe entry"
                               % (need, target))
    return fn(*pos, **kw), fn
'''  # O(1)

_SERVER_RUNNER = _SERVE_SNIPPET + r'''
import sys, json, inspect
spec = json.loads(sys.argv[1])
try:
    srv, fn = _make_server(spec["target"], spec["args"])
    host, port = srv.server_address[:2]
    try:
        f, line = inspect.getsourcefile(fn), inspect.getsourcelines(fn)[1]
    except (TypeError, OSError):
        f, line = None, 0
    print("@@READY@@" + json.dumps({"host": host, "port": port, "file": f, "line": line}), flush=True)
    srv.serve_forever()
except BaseException as e:
    print("@@ERR@@%s: %s" % (type(e).__name__, e), flush=True)
    sys.exit(3)
'''  # O(1)


def parse_serve(entry: str) -> tuple[str, str | None]:  # O(m) total
    m = re.match(r"^\s*([\w.]+)\s*(?:\((.*)\))?\s*$", entry, re.S)  # O(m)
    if not m:  # O(m)
        raise ToolError(f"bad serve entry: {entry!r}")  # O(m)
    return m.group(1), m.group(2)  # O(m)


def parse_probe_entry(text: str) -> dict[str, str]:  # O(m) total
    out: dict[str, str] = {}  # O(m)
    for part in text.split("·"):  # O(entries) iterations; O(m) total
        key, eq, val = part.strip().partition("=")  # O(m)
        key = key.strip().lower()  # O(m)
        if eq and key in ("call", "run", "serve") and val.strip():  # O(m)
            out[key] = val.strip().strip("`").strip()  # O(m)
    return out  # O(m)


class ServerHandle:  # O(1)
    """A real server in its own process (own PID), port 0; killed by PID in close()."""  # O(1)

    def __init__(self, root: Path, target: str, args: str | None, timeout: float = 20.0):  # O(T/0.05 * X) poll worst + process spawn
        self.tmp = tempfile.mkdtemp(prefix="pt_review_")  # O(1)
        self.out_path = Path(self.tmp) / "out.txt"  # O(1)
        self.err_path = Path(self.tmp) / "err.txt"  # O(1)
        self._out = open(self.out_path, "wb")  # O(1)
        self._err = open(self.err_path, "wb")  # O(1)
        self.proc = subprocess.Popen(  # O(1)
            [sys.executable, "-c", _SERVER_RUNNER, json.dumps({"target": target, "args": args})],  # O(1)
            cwd=root, env=project_env(root), stdout=self._out, stderr=self._err, stdin=subprocess.DEVNULL)  # O(1)
        self.info: dict | None = None  # O(1)
        self.error: str | None = None  # O(1)
        deadline = time.time() + timeout  # O(1)
        while time.time() < deadline:  # O(T/0.05) polls worst
            text = self.out_path.read_text(encoding="utf-8", errors="replace")  # O(X) re-reads whole output file each poll
            for ln in text.splitlines():  # O(X) re-splits all lines each poll
                if ln.startswith("@@READY@@"):  # O(1) per line
                    self.info = json.loads(ln[9:])  # O(1)
                elif ln.startswith("@@ERR@@"):  # O(1)
                    self.error = ln[7:]  # O(1)
            if self.info or self.error or self.proc.poll() is not None:  # O(1)
                break  # O(1)
            time.sleep(0.05)  # O(1) sleep 50 ms
        if not self.info and not self.error:  # O(1)
            tail = self.err_path.read_text(encoding="utf-8", errors="replace").strip()[-200:]  # O(1)
            self.error = "server did not start" + (f": {tail}" if tail else "")  # O(1)

    def request(self, method: str, path: str, body) -> tuple[int, bytes]:  # O(response size) + network round trip
        host = self.info["host"] if self.info["host"] not in ("", "0.0.0.0") else "127.0.0.1"  # O(1)
        conn = http.client.HTTPConnection(host, self.info["port"], timeout=10)  # O(1)
        try:  # O(1)
            data = None if body is None else json.dumps(body).encode()  # O(1)
            conn.request(method, path, body=data,  # O(1)
                         headers={"Content-Type": "application/json"} if data is not None else {})  # O(1)
            r = conn.getresponse()  # O(1)
            return r.status, r.read()  # O(1)
        finally:  # O(1)
            conn.close()  # O(1)

    def close(self) -> None:  # O(1) kill + rmtree of tmp dir
        try:  # O(1)
            self.proc.kill()  # O(1)
            self.proc.wait(timeout=10)  # O(1)
        except Exception:  # O(1)
            pass  # O(1)
        self._out.close()  # O(1)
        self._err.close()  # O(1)
        shutil.rmtree(self.tmp, ignore_errors=True)  # O(1)


def find_serve(plan: Plan, root: Path, producer: Task) -> tuple[str, str | None] | None:  # O(n + E_p*m + producer items*(d^2 + o*B)) total
    owned_mods = {module_name(root, root / o) for o in producer.owns() if o.endswith(".py")}  # O(o*d)
    entries = []  # O(1)
    for e in plan.probe_entries:  # O(E_p) iterations
        serve = parse_probe_entry(e).get("serve")  # O(m)
        if serve:  # O(m)
            entries.append(parse_serve(serve))  # O(m)
    for target, args in entries:  # O(E_p) iterations
        if target.rpartition(".")[0] in owned_mods:  # O(m)
            return target, args  # O(m)
    if entries:  # O(m)
        return entries[0]  # O(m)
    for p in planned_items(plan, [producer.id]):  # O(n + producer items * d^2)
        if p.ret and "Server" in p.ret:  # O(m)
            mod, chain, _f = locate(root, plan, p)  # O(m)
            if mod and len(chain) == 1:  # O(m)
                return f"{mod}.{chain[0]}", None  # O(m)
    return None  # O(m)


def check_data_contract(plan: Plan, root: Path, producer: Task, consumer: Task  # O(M*C + C*(|response| + network)) + server spawn total
                        ) -> tuple[list[Mismatch], list[str]]:  # O(1)
    notes: list[str] = []  # O(1)
    mm: list[Mismatch] = []  # O(1)
    contract = data_contract(plan, [producer.id, consumer.id])  # O(1)
    if not contract:  # O(1)
        return mm, ["part2 skipped: no data contract lines (METHOD /path -> status {shape}) in the plan"]  # O(1)
    calls = scan_calls(root, consumer.owns())  # O(1)
    for c in calls:  # O(M) iterations
        if not call_in_contract(c, contract):  # O(C*s) scan of every contract line
            shown = c.path + ("..." if c.prefix else "")  # O(1)
            mm.append(Mismatch(2, c.file, c.line, f"consumer {consumer.id} calls {c.method} {shown}, "  # O(1)
                                                  f"which is not in the data contract", consumer.id))  # O(1)
    serve = find_serve(plan, root, producer)  # O(1)
    if not serve:  # O(1)
        notes.append("part2: server not started (no serve= in a Probe entry and no Produces returning a server)")  # O(1)
        return mm, notes  # O(1)
    handle = ServerHandle(root, *serve)  # O(1)
    try:  # O(1)
        if handle.error or not handle.info:  # O(1)
            err = handle.error or "unknown"  # O(1)
            if "needs arguments" in err:  # O(1)
                notes.append(f"part2: server not started: {err}")  # O(1)
            else:  # O(1)
                mm.append(Mismatch(2, rel_to(root, plan.path), producer.start + 1,  # O(1)
                                   f"producer {producer.id} server failed to start: {err}", producer.id))  # O(1)
            return mm, notes  # O(1)
        loc_file = rel_to(root, handle.info["file"]) if handle.info.get("file") else rel_to(root, plan.path)  # O(1)
        loc_line = handle.info.get("line") or 1  # O(1)
        for c in contract:  # O(C) iterations
            path = re.sub(r"\{[^}]*\}|:\w+|<[^>]*>", "x", c.path)  # O(m)
            try:  # O(1)
                status, raw = handle.request(c.method, path, c.body)  # O(|response|) + network round trip, new connection per request
            except Exception as e:  # noqa: BLE001 | O(1)
                mm.append(Mismatch(2, loc_file, loc_line, f"{c.method} {c.path} failed: {type(e).__name__}: {e}",  # O(1)
                                   producer.id))  # O(1)
                continue  # O(1)
            if c.status is not None and status != c.status:  # O(1)
                mm.append(Mismatch(2, loc_file, loc_line,  # O(1)
                                   f"{c.method} {c.path} returned status {status}, contract says {c.status}",  # O(1)
                                   producer.id))  # O(1)
                continue  # O(1)
            if c.shape is not None:  # O(1)
                try:  # O(1)
                    value = json.loads(raw.decode("utf-8", errors="replace"))  # O(1)
                except ValueError:  # O(1)
                    mm.append(Mismatch(2, loc_file, loc_line,  # O(1)
                                       f"{c.method} {c.path} body is not JSON, contract expects a shape",  # O(1)
                                       producer.id))  # O(1)
                    continue  # O(1)
                for prob in check_shape(value, c.shape):  # O(|value| + |shape|) per contract line
                    mm.append(Mismatch(2, loc_file, loc_line, f"{c.method} {c.path}: {prob}", producer.id))  # O(1)
    finally:  # O(1)
        handle.close()  # O(1)
    return mm, notes  # O(1)


# --------------------------------------------------------------------------- consumer tests (part 3)


def rerun_consumer_tests(root: Path, consumer: Task, timeout: float) -> tuple[list[Mismatch], list[str]]:  # O(X) + 1 pytest subprocess total
    files = [o for o in consumer.owns() if o.endswith(".py") and TEST_PATH_RE.search(o) and (root / o).is_file()]  # O(1)
    if not files:  # O(1)
        return [], [f"part3 skipped: {consumer.id} owns no existing Python test files"]  # O(1)
    try:  # O(1)
        base = pyrun.pytest_cmd()  # O(1) resolves pytest interpreter (may probe)
    except pyrun.PytestUnavailable as e:  # O(1)
        return [], [f"part3 skipped: {e}"]  # O(1)
    cmd = [*base, "-q", "-x", "--no-header", "-p", "no:cacheprovider", *files]  # O(1)
    try:  # O(1)
        proc = pyrun.run_tree(cmd, cwd=root, env=project_env(root, SUPERLITE_REAL="1"), capture_output=True,  # O(X) + pytest process spawn + tree kill on timeout
                              text=True, encoding="utf-8", errors="replace", timeout=timeout)  # O(1)
    except subprocess.TimeoutExpired:  # O(1)
        return [Mismatch(3, files[0], 1, f"{consumer.id} tests hung (> {timeout:.0f}s) with SUPERLITE_REAL=1",  # O(1)
                         consumer.id)], []  # O(1)
    if proc.returncode == 0:  # O(1)
        return [], []  # O(1)
    if proc.returncode == 5:  # O(1)
        return [], [f"part3 skipped: pytest collected no tests in {', '.join(files)}"]  # O(1)
    out = proc.stdout + proc.stderr  # O(1)
    loc = re.search(r"^([^\s:]+\.py):(\d+): ", out, re.M)  # O(1)
    last = next((ln for ln in reversed(out.splitlines()) if ln.strip()), "pytest failed")  # O(1)
    file, line = (loc.group(1).replace("\\", "/"), int(loc.group(2))) if loc else (files[0], 1)  # O(1)
    return [Mismatch(3, file, line, f"{consumer.id} tests fail with the real producer "  # O(1)
                                    f"(SUPERLITE_REAL=1): {last.strip()}", consumer.id)], []  # O(1)


# --------------------------------------------------------------------------- contract command


def resolve_seam(plan: Plan, seam: str) -> tuple[Task, Task]:  # O(E + m) total
    parts = [s.strip() for s in seam.split(",")]  # O(1)
    if len(parts) != 2 or any(p not in plan.tasks for p in parts):  # O(1)
        raise ToolError(f"--seam needs two task ids that exist in the plan, got {seam!r}")  # O(1)
    a, b = parts  # O(1)
    ta, tb = plan.tasks[a], plan.tasks[b]  # O(1)
    if (a, b) in plan.edges or a in tb.consumes_ids():  # O(1)
        return ta, tb  # O(1)
    if (b, a) in plan.edges or b in ta.consumes_ids():  # O(1)
        return tb, ta  # O(1)
    raise ToolError(f"no seam between {a} and {b} (no edge in %% taskgraph, no Consumes tag)")  # O(1)


def append_log(path: Path, entry: dict) -> None:  # O(len(entry)) + file open total
    path.parent.mkdir(parents=True, exist_ok=True)  # O(1)
    with open(path, "a", encoding="utf-8") as fh:  # O(1)
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")  # O(1)


def cmd_contract(a: argparse.Namespace) -> int:  # O(parts above + Mm file opens) total
    plan = parse_plan(a.plan)  # O(1)
    root = Path(a.root).resolve()  # O(1)
    notes: list[str] = []  # O(1)
    mismatches: list[Mismatch]  # O(1)
    if a.seam:  # O(1)
        producer, consumer = resolve_seam(plan, a.seam)  # O(1)
        label = "seam " + "↔".join(s.strip() for s in a.seam.split(","))  # O(m)
        mismatches, n = check_interfaces(plan, root, [producer.id])  # O(1)
        if n == 0:  # O(1)
            notes.append(f"part1: {producer.id} has no parseable Produces signatures")  # O(1)
        m2, n2 = check_data_contract(plan, root, producer, consumer)  # O(1)
        mismatches += m2  # O(1)
        notes += n2  # O(1)
        m3, n3 = rerun_consumer_tests(root, consumer, a.timeout)  # O(1)
        mismatches += m3  # O(1)
        notes += n3  # O(1)
    else:  # O(1)
        label = "contract"  # O(1)
        mismatches, n = check_interfaces(plan, root, None)  # O(1)
        notes.append(f"part1: {n} Produces signature(s) checked")  # O(1)
    for n_ in notes:  # O(parts) iterations
        print(f"note: {n_}")  # O(1)
    for m in mismatches:  # O(Mm) iterations
        print(m)  # O(1)
    # A seam run always logs: mismatches become incidents, a pass becomes `verified`.
    log = Path(a.log) if a.log else (root / ".superlite" / "incidents.jsonl" if a.seam else None)  # O(1)
    if mismatches:  # O(1)
        print(f"FAIL: {label}: {len(mismatches)} mismatch(es)")  # O(1)
        if log:  # O(1)
            for m in mismatches:  # O(Mm) iterations
                append_log(log, {"file": m.file, "line": m.line, "context": f"{label}: {m.msg}"[:200],  # O(file open) per mismatch: append_log reopens the log each time
                                 "agent": "pt_review", "kind": "exception", "confidence": "high",  # O(1)
                                 "resolution": "open", "time": time.strftime("%H:%M:%S")})  # O(1)
        return 1  # O(1)
    print(f"PASS: verified: {label}")  # O(1)
    if log and a.seam:  # O(1)
        append_log(log, {"file": "", "line": 0, "context": f"verified: {label}", "agent": "pt_review",  # O(1)
                         "kind": "verified", "time": time.strftime("%H:%M:%S")})  # O(1)
    return 0  # O(1)


# --------------------------------------------------------------------------- adapter command

_ADAPTER_HEAD = '''"""Probe adapter, generated by pt_review.py adapter from the plan's Probe entry lines.
Do not edit; regenerate. Entry kinds: call(text) -> value, run(args, stdin) -> (rc, out, err),
request(method, path, body) -> (status, json)."""
from __future__ import annotations

import importlib
import inspect
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = (Path(__file__).resolve().parent / __REL_ROOT__).resolve()
for _p in (ROOT / "src", ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

ENTRIES = __ENTRIES__
KINDS = __KINDS__


def _entry(kind, package=None):
    for e in ENTRIES:
        if kind in e and (package is None or e["package"] == package):
            return e
    raise LookupError("no %s entry%s in the plan's Probe entry lines" % (kind, " for " + package if package else ""))
'''  # O(1)

_ADAPTER_CALL = '''

def _resolve(dotted):
    parts = dotted.split(".")
    for k in range(len(parts), 0, -1):
        prefix = ".".join(parts[:k])
        try:
            obj = importlib.import_module(prefix)
        except ModuleNotFoundError as exc:
            if exc.name and prefix.startswith(exc.name):
                continue
            raise
        parent = None
        for name in parts[k:]:
            parent, obj = obj, getattr(obj, name)
        return obj, parent
    raise ImportError(dotted)


def call(text, package=None):
    """Library entry: import the planned target and call it with `text`."""
    obj, parent = _resolve(_entry("call", package)["call"])
    if inspect.isclass(parent) and inspect.isfunction(obj) and not isinstance(
            inspect.getattr_static(parent, obj.__name__, None), (staticmethod, classmethod)):
        obj = getattr(parent(), obj.__name__)
    return obj(text)
'''  # O(1)

_ADAPTER_RUN = '''

def run(args=(), stdin="", package=None, timeout=10):
    """CLI entry: execute the planned command with extra args; returns (rc, stdout, stderr)."""
    cmd = shlex.split(_entry("run", package)["run"], posix=(os.name != "nt"))
    if cmd and cmd[0] in ("python", "python3", "py"):
        cmd[0] = sys.executable
    if isinstance(args, str):
        args = shlex.split(args, posix=(os.name != "nt"))
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONPATH=os.pathsep.join(
        [str(ROOT / "src"), str(ROOT)] + ([os.environ["PYTHONPATH"]] if os.environ.get("PYTHONPATH") else [])))
    p = subprocess.run(cmd + list(args), input=stdin, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=ROOT, env=env, timeout=timeout)
    return p.returncode, p.stdout, p.stderr
'''  # O(1)

_ADAPTER_SERVE = '''
import http.client
import threading

_SERVERS = {}
''' + _SERVE_SNIPPET + '''

def _server(package=None):
    e = _entry("serve", package)
    key = e["package"]
    if key not in _SERVERS:
        target, args = e["serve"]
        srv, _fn = _make_server(target, args)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        _SERVERS[key] = srv
    return _SERVERS[key]


def start_server(package=None):
    """For probe_run and the regression tests: start the serve entry on port 0, return (port, stop)."""
    srv = _server(package)
    key = _entry("serve", package)["package"]

    def stop():
        _SERVERS.pop(key, None)
        srv.shutdown()
        srv.server_close()

    return srv.server_address[1], stop


def request(method, path, body=None, package=None, timeout=10):
    """Server entry: one shared server per process on port 0; returns (status, json-or-text)."""
    srv = _server(package)
    host, port = srv.server_address[:2]
    conn = http.client.HTTPConnection(host if host not in ("", "0.0.0.0") else "127.0.0.1", port, timeout=timeout)
    try:
        if body is None or isinstance(body, bytes):
            data = body
        elif isinstance(body, str):
            data = body.encode("utf-8", "surrogatepass")
        else:
            data = json.dumps(body).encode()
        conn.request(method, path, body=data, headers={"Content-Type": "application/json"} if data else {})
        r = conn.getresponse()
        raw = r.read().decode("utf-8", "replace")
    finally:
        conn.close()
    try:
        return r.status, json.loads(raw)
    except ValueError:
        return r.status, raw


def close():
    for srv in list(_SERVERS.values()):
        srv.shutdown()
        srv.server_close()
    _SERVERS.clear()
'''  # O(1)


def build_adapter(plan: Plan, out: Path, root: Path) -> str:  # O(E_p*m + template size) total
    entries: list[dict] = []  # O(1)
    for text in plan.probe_entries:  # O(E_p) iterations
        parsed = parse_probe_entry(text)  # O(1)
        if not parsed:  # O(1)
            continue  # O(1)
        first = parsed.get("call") or parsed.get("serve") or parsed.get("run", "")  # O(1)
        first = re.sub(r"^(python3?|py)\s+-m\s+", "", first.split("(")[0].strip())  # O(1)
        entry: dict = {"package": first.split(".")[0].split()[0] if first else f"p{len(entries)}"}  # O(1)
        if "call" in parsed:  # O(1)
            entry["call"] = parsed["call"]  # O(1)
        if "run" in parsed:  # O(1)
            entry["run"] = parsed["run"]  # O(1)
        if "serve" in parsed:  # O(1)
            entry["serve"] = list(parse_serve(parsed["serve"]))  # O(1)
        entries.append(entry)  # O(1)
    if not entries:  # O(1)
        raise ToolError("no `Probe entry:` lines with call=/run=/serve= found in the plan")  # O(1)
    kinds = [k for k in ("call", "run", "serve") if any(k in e for e in entries)]  # O(3*E_p)
    rel_root = os.path.relpath(root, out.parent.resolve()).replace("\\", "/")  # O(1)
    src = _ADAPTER_HEAD.replace("__REL_ROOT__", repr(rel_root)).replace("__ENTRIES__", repr(entries)) \
        .replace("__KINDS__", repr(["request" if k == "serve" else k for k in kinds]))  # O(1)
    if "call" in kinds:  # O(1)
        src += _ADAPTER_CALL  # O(1)
    if "run" in kinds:  # O(1)
        src += _ADAPTER_RUN  # O(1)
    if "serve" in kinds:  # O(1)
        src += _ADAPTER_SERVE  # O(1)
    return src  # O(1)


SEAM_FILES = {  # O(1)
    "typescript": "seam.ts", "javascript": "seam.js", "go": "seam.go", "rust": "seam.rs",
    "csharp": "seam.cs", "java": "seam.java", "ruby": "seam.rb", "swift": "seam.swift",
}


def seam_path(root: Path, language: str) -> Path:  # O(1)
    """Python keeps adapter.py. Every other language gets its own seam file."""
    lang = (language or "").strip().lower()  # O(1)
    name = "adapter.py" if lang in ("", "python") else SEAM_FILES.get(lang, "seam.txt")  # O(1)
    return Path(root) / ".superlite" / "probes" / name  # O(1)


def cmd_adapter(a: argparse.Namespace) -> int:  # O(template size) + file write total
    plan = parse_plan(a.plan)  # O(1)
    root = Path(a.root).resolve()  # O(1)
    import project_config  # O(1)
    try:  # O(1)
        project_config.load(root)  # O(file)
    except project_config.ProjectConfigError as exc:  # O(1)
        print(str(exc), file=sys.stderr)  # O(1)
        return 2  # O(1)
    lang = "python" if a.out else project_config.language(root)  # explicit --out stays the Python adapter | O(file)
    if lang == "python":  # O(1)
        out = Path(a.out) if a.out else seam_path(root, "python")  # O(1)
        out.parent.mkdir(parents=True, exist_ok=True)  # O(1)
        src = build_adapter(plan, out, root)  # O(1)
        compile(src, str(out), "exec")  # O(1)
        out.write_text(src, encoding="utf-8", newline="\n")  # O(1)
        print(f"wrote {out}")  # O(1)
        return 0  # O(1)
    out = Path(a.out) if a.out else seam_path(root, lang)  # O(1)
    out.parent.mkdir(parents=True, exist_ok=True)  # O(1)
    out.write_text(f"// superlite seam: {lang}\n", encoding="utf-8", newline="\n")  # O(1)
    print(f"wrote {out}")  # O(1)
    return 0  # O(1)


# --------------------------------------------------------------------------- packet command


def read_incidents(path: Path) -> list[dict]:  # O(file size) total, single pass
    out: list[dict] = []  # O(m)
    if path.is_file():  # O(m)
        for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():  # O(K) iterations
            ln = ln.strip()  # O(m)
            if not ln:  # O(m)
                continue  # O(m)
            try:  # O(m)
                e = json.loads(ln)  # O(m) per iteration
            except ValueError:  # O(m)
                continue  # O(m)
            if isinstance(e, dict) and "file" in e:  # O(m)
                e["file"] = str(e.get("file", "")).replace("\\", "/")  # O(m)
                try:  # O(m)
                    e["line"] = int(e.get("line") or 0)  # O(m)
                except (TypeError, ValueError):  # O(m)
                    e["line"] = 0  # O(m)
                out.append(e)  # O(m)
    return out  # O(m)


def excerpt(root: Path, file: str, line: int, ctx: int = 12) -> str:  # O(B) per call (re-reads whole file every call)
    f = root / file  # O(1)
    if not f.is_file():  # O(1)
        f = root / "src" / file  # O(1)
    if not f.is_file():  # O(1)
        return "(file not found)"  # O(1)
    lines = f.read_text(encoding="utf-8", errors="replace").splitlines()  # O(B) read whole file + splitlines
    if line <= 0:  # O(1)
        lo, hi = 1, min(len(lines), 2 * ctx + 1)  # O(1)
    else:  # O(1)
        lo, hi = max(1, line - ctx), min(len(lines), line + ctx)  # O(1)
    rows = []  # O(1)
    for n in range(lo, hi + 1):  # O(ctx) iterations
        mark = ">" if n == line else " "  # O(1)
        rows.append(f"{mark}{n:>4} | {lines[n - 1][:160]}")  # O(1)
    return "\n".join(rows) if rows else "(empty file)"  # O(1)


@dataclass  # O(1)
class Item:  # O(1)
    section: str  # incidents | probes | uncovered | contract | O(1)
    rank: tuple  # O(1)
    title: str  # O(1)
    body: str  # O(1)
    file: str = ""  # O(1)
    line: int = 0  # O(1)


def incident_items(entries: list[dict]) -> list[Item]:  # O(K) total
    groups: dict[tuple[str, int], list[dict]] = {}  # O(1)
    for e in entries:  # O(K) iterations
        if e.get("kind") != "verified":  # O(1)
            groups.setdefault((e["file"], e["line"]), []).append(e)  # O(1)
    items: list[Item] = []  # O(1)
    for (file, line), es in groups.items():  # O(groups) iterations
        repeated = len(es) >= 2  # O(1)
        unresolved = any(str(e.get("resolution", "")).lower() not in RESOLVED for e in es)  # O(entries at location)
        low = any(str(e.get("confidence", "")).lower() == "low" for e in es)  # O(1)
        if not (repeated or unresolved or low):  # O(1)
            continue  # O(1)
        why = [w for w, on in (("repeated", repeated), ("unresolved", unresolved), ("low confidence", low)) if on]  # O(1)
        tier = 0 if repeated else 1 if unresolved else 2  # O(1)
        lines = []  # O(1)
        for e in es[:3]:  # O(1) at most 3 iterations
            tags = "/".join(str(e[k]) for k in ("kind", "resolution", "confidence", "agent") if e.get(k))  # O(1)
            lines.append(f"- [{tags}] {str(e.get('context', ''))[:200]}")  # O(1)
        if len(es) > 3:  # O(1)
            lines.append(f"- (+{len(es) - 3} more entries at this location)")  # O(1)
        items.append(Item("incidents", (1, tier, -len(es), file, line),  # O(1)
                          f"{file}:{line}  ({', '.join(why)}; {len(es)} entr{'y' if len(es) == 1 else 'ies'})",  # O(1)
                          "\n".join(lines), file, line))  # O(1)
    return items  # O(1)


def read_probe_failures(probes: Path) -> list[Item]:  # O(R) total
    f = probes if probes.is_file() else probes / "probe-results.json"  # O(1)
    if not f.is_file():  # O(1)
        return []  # O(1)
    try:  # O(1)
        data = json.loads(f.read_text(encoding="utf-8"))  # O(1)
    except ValueError:  # O(1)
        return []  # O(1)
    results = data.get("results", []) if isinstance(data, dict) else data  # O(1)
    items: list[Item] = []  # O(1)
    for r in results if isinstance(results, list) else []:  # O(R) iterations
        if not isinstance(r, dict) or str(r.get("status", "pass")).lower() in PASS_STATUSES:  # O(1)
            continue  # O(1)
        file = str(r.get("file", "")).replace("\\", "/")  # O(1)
        line = int(r.get("line") or 0)  # O(1)
        detail = str(r.get("detail") or r.get("message") or r.get("probe") or "")[:200]  # O(1)
        items.append(Item("probes", (3, str(r.get("category", "")), file, line),  # O(1)
                          f"probe {r.get('category', '?')} {r.get('status')}: {file}:{line}",  # O(1)
                          f"- {detail}" if detail else "", file, line))  # O(1)
    return items  # O(1)


def focus_packages(plan: Plan) -> list[tuple[str, list[str]]]:  # O(focus lines * m) total
    text = plan.review_focus()  # O(m)
    out: list[tuple[str, list[str]]] = []  # O(m)
    for ln in text.splitlines():  # O(focus lines) iterations
        m = re.match(r"^\s*(?:[-*]\s*)?\**`?([\w./-]+)`?\**\s*[:—-]\s*(.+)$", ln)  # O(m)
        if not m:  # O(m)
            continue  # O(m)
        cats = [c for c in re.split(r"[,·/;\s]+", m.group(2).lower().replace("**", "")) if c in CATEGORIES]  # O(m)
        if cats:  # O(m)
            out.append((Path(m.group(1)).name, list(dict.fromkeys(cats))))  # O(m)
    return out  # O(m)


def package_dir(root: Path, pkg: str) -> Path | None:  # O(1) stat calls total
    for cand in (root / "src" / pkg, root / pkg):  # O(2) iterations
        if cand.is_dir():  # O(1)
            return cand  # O(1)
    return None  # O(1)


def uncovered_items(plan: Plan, root: Path, entries: list[dict]) -> list[Item]:  # O(pkgs*cats*(V + F_pkg*stat + B)) total (rglob + parse repeated per category)
    verified = [e for e in entries if e.get("kind") == "verified"]  # O(K)
    items: list[Item] = []  # O(1)
    for pkg, cats in focus_packages(plan):  # O(pkgs) iterations
        files = [e for e in verified if pkg in re.split(r"[/.]", e["file"].replace(".py", ""))  # O(V*m) filter, once per package
                 or e["file"].replace(".py", "") == pkg]  # O(1)
        for cat in cats:  # O(cats) iterations
            hit = any(str(e.get("category", "")).lower() == cat  # O(|files|) with regex per entry
                      or re.search(rf"\b{cat}\b", str(e.get("context", "")).lower()) for e in files)  # O(1)
            if hit:  # O(1)
                continue  # O(1)
            file, line = "", 0  # O(1)
            d = package_dir(root, pkg)  # O(1) stats, repeated per category
            if d:  # O(1)
                cands = sorted((p for p in d.rglob("*.py") if p.name != "__init__.py"  # O(files in package dir incl. SKIP_DIRS) rglob + stat per file + sort, repeated per category
                                and not set(p.relative_to(d).parts) & SKIP_DIRS),  # O(1)
                               key=lambda p: -p.stat().st_size)  # O(1)
                if cands:  # O(1)
                    file = rel_to(root, cands[0])  # O(1)
                    try:  # O(1)
                        tree = ast.parse(cands[0].read_text(encoding="utf-8"))  # O(B) read + parse of largest file, repeated per category
                        line = next((n.lineno for n in tree.body  # O(top-level stmts)
                                     if isinstance(n, (ast.FunctionDef, ast.ClassDef))), 1)  # O(1)
                    except (SyntaxError, OSError):  # O(1)
                        line = 1  # O(1)
            items.append(Item("uncovered", (4, pkg, cat),  # O(1)
                              f"package {pkg}: no `verified` entry for category `{cat}`",  # O(1)
                              "- Unverified: judge from the excerpt or list it as a finding.", file, line))  # O(1)
    return items  # O(1)


def package_of(root: Path, f: Path) -> str | None:  # O(d) stat/resolve syscalls total
    try:  # O(d)
        rel = f.resolve().relative_to(root.resolve())  # O(d) resolve syscalls
    except ValueError:  # O(d)
        return None  # O(d)
    parts = list(rel.parts)  # O(d)
    if parts and parts[0] == "src":  # O(d)
        parts = parts[1:]  # O(d)
    elif parts and not (root / parts[0] / "__init__.py").is_file():  # O(d)
        return None  # O(d)
    return parts[0] if len(parts) > 1 else None  # O(d)


def source_files(root: Path) -> list[Path]:  # O(tree size after pruning) total
    out: list[Path] = []  # O(1)
    for dirpath, dirnames, filenames in os.walk(root):  # O(dirs) iterations
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]  # O(dirs in this level)
        out.extend(Path(dirpath) / f for f in filenames if f.endswith(".py"))  # O(files in this dir)
    return out  # O(1)


def cross_package_calls(root: Path) -> list[str]:  # O(S*B) total, reads+parses every source file
    files = source_files(root)  # O(S*d) syscalls
    pkgs = {p for p in (package_of(root, f) for f in files) if p}  # O(S*d) syscalls
    rows: set[tuple[str, int, str, str, str]] = set()  # O(1)
    for f in files:  # O(S) iterations
        own = package_of(root, f)  # O(d) syscalls per iteration
        if not own:  # O(1)
            continue  # O(1)
        try:  # O(1)
            tree = ast.parse(f.read_text(encoding="utf-8"))  # O(B) read + parse
        except (SyntaxError, OSError):  # O(1)
            continue  # O(1)
        alias: dict[str, str] = {}  # O(1)
        for n in ast.walk(tree):  # O(A) iterations over AST nodes
            if isinstance(n, ast.Import):  # O(1)
                for al in n.names:  # O(names) iterations
                    alias[(al.asname or al.name).split(".")[0]] = al.name if al.asname else al.name.split(".")[0]  # O(1)
            elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:  # O(1)
                for al in n.names:  # O(names) iterations
                    alias[al.asname or al.name] = f"{n.module}.{al.name}"  # O(1)
        for n in ast.walk(tree):  # O(A) iterations over AST nodes
            if not isinstance(n, ast.Call):  # O(1)
                continue  # O(1)
            chain, cur = [], n.func  # O(1)
            while isinstance(cur, ast.Attribute):  # O(chain length) iterations
                chain.append(cur.attr)  # O(1)
                cur = cur.value  # O(1)
            if isinstance(cur, ast.Name) and cur.id in alias:  # O(1)
                full = ".".join([alias[cur.id]] + chain[::-1])  # O(1)
                target = full.split(".")[0]  # O(1)
                if target in pkgs and target != own:  # O(1)
                    rows.add((rel_to(root, f), n.lineno, own, target, full))  # O(d) resolve syscalls
    return [f"- {f}:{ln}  {a} -> {b}: {full}()" for f, ln, a, b, full in sorted(rows)]  # O(R log R) sort of rows


def git_diff_stat(root: Path, base: str | None) -> str:  # O(X) + up to 2 git subprocess spawns total
    def git(*args: str) -> subprocess.CompletedProcess | None:  # O(X) + 1 git subprocess spawn
        try:  # O(X)
            return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,  # O(X) + git process spawn
                                  encoding="utf-8", errors="replace", timeout=30)  # O(X)
        except (OSError, subprocess.TimeoutExpired):  # O(X)
            return None  # O(X)

    if not (root / ".git").exists():  # O(1)
        return "(not a git repository)"  # O(1)
    if base is None:  # O(1)
        first = git("rev-list", "--max-parents=0", "HEAD")  # O(1)
        base = first.stdout.split()[0] if first and first.returncode == 0 and first.stdout.split() else "HEAD"  # O(1)
    r = git("diff", "--stat", base)  # O(1)
    if not r or r.returncode != 0:  # O(1)
        return "(diff stat unavailable)"  # O(1)
    return r.stdout.strip() or "(no changes)"  # O(1)


EXCERPT_FILE_LINES = 120  # O(1) cap on diff lines per hot-spot file
EXCERPT_TOTAL_LINES = 600  # O(1) cap on diff lines over the whole section


def source_excerpts(root: Path, base: str | None, files: list[str],  # O(X) + one git spawn per file shown
                    per_file: int = EXCERPT_FILE_LINES, total: int = EXCERPT_TOTAL_LINES) -> str:
    """Changed hunks (`git diff <base> -- file`) of the hot-spot files, bounded per file and in total."""
    if not (root / ".git").exists():  # O(1)
        return "- (not a git repository)"  # O(1)
    if base is None:  # O(1)
        first = subprocess.run(["git", "-C", str(root), "rev-list", "--max-parents=0", "HEAD"],  # O(1) + git spawn
                               capture_output=True, text=True, encoding="utf-8", errors="replace")  # O(1)
        roots = first.stdout.split() if first.returncode == 0 else []  # O(1)
        base = roots[0] if roots else "HEAD"  # O(1)
    out: list[str] = []  # O(1)
    left = total  # O(1)
    for n, f in enumerate(files):  # O(files) iterations
        if left <= 0:  # O(1)
            out.append(f"- (excerpts stopped at the {total}-line total cap; {len(files) - n} more file(s) omitted)")  # O(1)
            break  # O(1)
        try:  # O(1)
            r = subprocess.run(["git", "-C", str(root), "diff", "-U3", base, "--", f],  # O(X) + git spawn
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)  # O(1)
        except (OSError, subprocess.TimeoutExpired):  # O(1)
            continue  # O(1)
        lines = r.stdout.splitlines() if r.returncode == 0 else []  # O(X)
        if not lines:  # O(1)
            continue  # O(1)
        take = lines[:min(per_file, left)]  # O(per_file)
        left -= len(take)  # O(1)
        out.append(f"### {f}\n```diff\n" + "\n".join(take) + "\n```")  # O(per_file)
        if len(take) < len(lines):  # O(1)
            why = "per-file" if len(take) == per_file else "total"  # O(1)
            out.append(f"(truncated: {len(take)} of {len(lines)} diff lines shown, {why} cap)")  # O(1)
    return "\n".join(out) or "- none (no changed hunks in the hot-spot files)"  # O(output)


def read_test_summary(path: Path) -> tuple[str, int]:  # O(file size) total
    if not path.is_file():  # O(1)
        return "- no test summary recorded (`run_tests.py --full` runs alongside this review)", 0  # O(1)
    try:  # O(1)
        d = json.loads(path.read_text(encoding="utf-8"))  # O(1)
    except ValueError:  # O(1)
        return "- test summary unreadable", 0  # O(1)
    failed = d.get("failures") or d.get("failed_tests") or []  # O(1)
    skipped = d.get("skipped_tests") or []  # O(1)
    nfail = int(d["failed"]) if isinstance(d.get("failed"), int) else len(failed)  # O(1)
    rows = [f"- passed {d.get('passed', '?')} · failed {nfail} · skipped {d.get('skipped', len(skipped))}"]  # O(1)
    rows += [f"- FAILING: {t}" for t in failed[:30]]  # O(min(30, failed))
    rows += [f"- SKIPPED: {t}" for t in skipped[:30]]  # O(min(30, skipped))
    return "\n".join(rows), nfail  # O(1)


def render_item(root: Path, it: Item, shown: set[tuple[str, int]], idx: str) -> str:  # O(B) per item (excerpt re-reads file)
    out = [f"### {idx} {it.title}"]  # O(1)
    if it.body:  # O(1)
        out.append(it.body)  # O(1)
    if it.file:  # O(1)
        key = (it.file, it.line)  # O(1)
        if key in shown:  # O(1)
            out.append("(excerpt shown with the item for the same location)")  # O(1)
        else:  # O(1)
            shown.add(key)  # O(1)
            out.append("```\n" + excerpt(root, it.file, it.line) + "\n```")  # O(1)
    return "\n".join(out) + "\n"  # O(1)


def cap_text(s: str, limit: int, note: str = "(truncated)") -> str:  # O(len(s)) total
    if len(s) <= limit:  # O(len(s))
        return s  # O(len(s))
    return s[: max(0, limit - len(note) - 1)].rstrip() + "\n" + note  # O(len(s))


def cmd_packet(a: argparse.Namespace) -> int:  # O(S*B + I*B + I log I) + git/python subprocesses total
    plan = parse_plan(a.plan)  # O(1)
    root = Path(a.root).resolve()  # O(1)
    sl = root / ".superlite"  # O(1)
    log = Path(a.log) if a.log else sl / "incidents.jsonl"  # O(1)
    probes = Path(a.probes) if a.probes else sl / "probes"  # O(1)
    out = Path(a.out) if a.out else sl / "review-packet.md"  # O(1)
    tests = Path(a.tests) if a.tests else sl / "test-summary.json"  # O(1)
    cap = int(a.max_tokens * CHARS_PER_TOKEN)  # O(1)

    entries = read_incidents(log)  # O(file size)
    items = incident_items(entries) + read_probe_failures(probes) + uncovered_items(plan, root, entries)  # O(K + R + pkgs*cats*...)
    mismatches, n_checked = check_interfaces(plan, root, None)  # O(P) + 1 subprocess
    for m in mismatches:  # O(Mm) iterations
        items.append(Item("contract", (2, m.file, m.line, m.msg), f"{m.file}:{m.line}  part1 {m.task}",  # O(1)
                          f"- {m.msg}", m.file, m.line))  # O(1)
    items.sort(key=lambda i: i.rank)  # O(I log I)
    counts = {s: sum(1 for i in items if i.section == s) for s in ("incidents", "probes", "uncovered", "contract")}  # O(4*I)
    tests_text, nfail = read_test_summary(tests)  # O(1)
    n_open = sum(counts.values()) + (1 if nfail else 0)  # O(1)
    fast = n_open == 0  # O(1)

    seams = cross_package_calls(root)  # O(S*B)
    seam_text = "\n".join(seams) if seams else "- none"  # O(1)
    seam_text = cap_text(seam_text, 5000, f"- (+ more calls omitted; {len(seams)} total)")  # O(1)
    focus = cap_text(plan.review_focus() or "(plan has no Review Focus section)", 3000)  # O(1)
    diff = cap_text(git_diff_stat(root, a.base), 2000)  # O(X) + git spawns
    hot: list[str] = []  # hot-spot files: those the ranked items point at, in rank order | O(1)
    for it in items:  # O(I) iterations
        rel = rel_to(root, root / it.file) if it.file else None  # O(d)
        if rel and rel not in hot:  # O(H)
            hot.append(rel)  # O(1)
    excerpts = cap_text(source_excerpts(root, a.base, hot), max(2000, cap // 3))  # O(X) + git spawns

    head = [  # O(1)
        f"# Review packet: {'FAST PATH' if fast else 'REVIEW REQUIRED'}",  # O(1)
        f"plan: {plan.path.name}",  # O(1)
        "Answer in one turn, no tools: a verdict, then findings as `file:line - problem - fix`. "  # O(1)
        "Anything marked unverified is judged from its excerpt or listed as a finding.",  # O(1)
        ("Nothing is open: no open incidents, probe failures, unverified categories, contract mismatches or "  # O(1)
         "failing tests. Reply with a short APPROVED (about 10 s)." if fast else  # O(1)
         f"Open items: {n_open} (incidents {counts['incidents']}, probe failures {counts['probes']}, "  # O(1)
         f"unverified categories {counts['uncovered']}, contract mismatches {counts['contract']}, "  # O(1)
         f"failing tests {nfail}). Excerpts are +-12 lines."),  # O(1)
        "",  # O(1)
    ]  # O(1)
    tail = ["## 4. Test summary", tests_text, "",  # O(1)
            "## 5. Cross-package seams (calls from one package into another)", seam_text, "",  # O(1)
            "## 6. Diff stat", "```\n" + diff + "\n```", "",  # O(1)
            "## 7. Review Focus", focus, "",  # O(1)
            "## 8. Source excerpts (changed hunks of the hot-spot files)", excerpts, ""]  # O(1)
    sec_titles = {  # O(1)
        "incidents": "## 1. Open incidents (repeated file:line first, then unresolved, then low confidence)",  # O(1)
        "probes": "## 2. Probe results: failures still open and unverified categories",  # O(1)
        "contract": f"## 3. Contract check: real signatures vs the plan's Produces ({n_checked} checked, "  # O(1)
                    f"{len(mismatches)} mismatch(es))",  # O(1)
    }  # O(1)
    fixed_len = len("\n".join(head + tail)) + 400 + sum(len(t) + 40 for t in sec_titles.values())  # O(sum of text sizes)
    budget = cap - fixed_len  # O(1)
    chosen: list[tuple[Item, str]] = []  # O(1)
    used, shown = 0, set()  # O(1)
    omitted = {s: 0 for s in counts}  # O(1)
    stopped = False  # O(1)
    for n, it in enumerate(items, 1):  # O(I) iterations
        text = render_item(root, it, shown, f"[{n}]")  # O(B) per item, runs even after budget exhausted
        if stopped or used + len(text) > budget:  # O(1)
            stopped = True  # O(1)
            omitted[it.section] += 1  # O(1)
            continue  # O(1)
        chosen.append((it, text))  # O(1)
        used += len(text)  # O(1)
    parts = list(head)  # O(1)
    for sec in ("incidents", "probes", "contract"):  # O(3) iterations
        parts.append(sec_titles[sec])  # O(1)
        sec_items = [t for it, t in chosen if it.section == sec or (sec == "probes" and it.section == "uncovered")]  # O(I) per section
        parts.extend(sec_items or ["- none\n"])  # O(1)
        om = omitted[sec] + (omitted["uncovered"] if sec == "probes" else 0)  # O(1)
        if om:  # O(1)
            parts.append(f"- ({om} lower-ranked item(s) omitted to stay within the size cap)\n")  # O(1)
    parts.extend(tail)  # O(1)
    text = "\n".join(parts)  # O(output size)
    if len(text) > cap:  # O(1)
        text = cap_text(text, cap, "(packet truncated at the size cap)")  # O(output size)
    out.parent.mkdir(parents=True, exist_ok=True)  # O(1)
    out.write_text(text, encoding="utf-8", newline="\n")  # O(1)
    print(f"wrote {out} ({len(text)} chars, ~{int(len(text) / CHARS_PER_TOKEN)} tokens) - "  # O(1)
          f"{'FAST PATH' if fast else f'{n_open} open item(s)'}")  # O(1)
    return 0  # O(1)


# --------------------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:  # O(1) total
    ap = argparse.ArgumentParser(prog="pt_review.py", description="Review-side plan tools.")  # O(1)
    sub = ap.add_subparsers(dest="cmd", required=True)  # O(1)
    for name in ("adapter", "packet", "contract"):  # O(3) iterations
        sp = sub.add_parser(name)  # O(1)
        sp.add_argument("--plan", required=True)  # O(1)
        sp.add_argument("--root", default=".")  # O(1)
        if name == "adapter":  # O(1)
            sp.add_argument("--out")  # O(1)
        elif name == "packet":  # O(1)
            sp.add_argument("--log")  # O(1)
            sp.add_argument("--probes")  # O(1)
            sp.add_argument("--out")  # O(1)
            sp.add_argument("--max-tokens", type=int, default=20000)  # O(1)
            sp.add_argument("--tests", help="test summary JSON (default .superlite/test-summary.json)")  # O(1)
            sp.add_argument("--base", help="git ref for the diff stat and source excerpts (default: the root commit)")  # O(1)
        else:  # O(1)
            sp.add_argument("--seam", help="Ta,Tb: run parts 1-3 for that seam")  # O(1)
            sp.add_argument("--log", help="append a verified/incident entry to this JSONL log "  # O(1)
                                          "(--seam default: <root>/.superlite/incidents.jsonl)")  # O(1)
            sp.add_argument("--timeout", type=float, default=300.0, help="consumer test timeout, seconds")  # O(1)
    return ap  # O(1)


def main(argv: list[str]) -> int:  # O(1) + dispatched command
    try:  # O(1)
        a = build_parser().parse_args(argv)  # O(1)
    except SystemExit as e:  # O(1)
        return int(e.code) if isinstance(e.code, int) else 2  # O(1)
    try:  # O(1)
        return {"adapter": cmd_adapter, "packet": cmd_packet, "contract": cmd_contract}[a.cmd](a)  # O(1)
    except ToolError as e:  # O(1)
        print(f"error: {e}", file=sys.stderr)  # O(1)
        return 2  # O(1)


if __name__ == "__main__":  # O(1)
    for stream in (sys.stdout, sys.stderr):  # O(2) iterations
        try:  # O(1)
            stream.reconfigure(encoding="utf-8")  # O(1)
        except (AttributeError, ValueError):  # O(1)
            pass  # O(1)
    sys.exit(main(sys.argv[1:]))  # O(1)
