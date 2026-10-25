#!/usr/bin/env python3
"""plan_tools: mechanical tooling for superlite plans (D17).

Subcommands implemented here: taskgraph, stubs, schedule, lint.
Subcommands adapter, packet and contract live in pt_review.py (lazy import).

Exit codes: 0 ok, 1 findings (stubs import failure, lint issues), 2 bad plan or usage.
Stdlib only.
"""
# Complexity notation:
# n = tasks in plan, e = dependency edges, L = lines in plan, C = chars in plan (<= 15k), b = fenced blocks
# s = chars in one line/field/signature, k = field lines per task, o = owned paths per task, A = brace expansions
# G = taskgraph lines, R = node mentions in taskgraph, M = distinct ids mentioned, d = deps of one task, c = lane cap
# q = Produces/Consumes items per task, E = stub entries per file, f = stub files, S = lines in spec
# B = chars in a mermaid block, P = placeholder patterns (7), w = warnings, r = ready tasks, cands = candidates
# p = cost of one predict.predict_task call (external module)
from __future__ import annotations  # O(1)

import argparse  # O(1)
import hashlib  # O(1)
import importlib.util  # O(1)
import json  # O(1)
import re  # O(1)
import sys  # O(1)
from dataclasses import dataclass, field  # O(1)
from datetime import datetime  # O(1)
from pathlib import Path  # O(1)

SCRIPT_DIR = Path(__file__).resolve().parent  # O(1)
CONFIG_DIR = SCRIPT_DIR.parent / "config"  # O(1)
REVIEW_COMMANDS = ("adapter", "packet", "contract")  # O(1)
IMPLEMENTER_PREFIX = 18000  # O(1)
CTX_CEILING = 80000  # O(1)
MERGE_MIN_VARIABLE = 18000  # O(1) taskgraph advisory only
MERGE_MIN_MINUTES = 1.0  # O(1) taskgraph advisory only
DEFAULT_CAP = 10  # O(1)
DEFAULT_SIZE_BAND = {"low_min": 1.5, "high_min": 6.0}  # O(1) used when predictor.json has no size_band
DEFAULT_MAX_CHARS = 15000  # O(1)

TASK_ID = r"T\d+[a-z]*"  # O(1)
TASK_ID_RE = re.compile(rf"^{TASK_ID}$")  # O(1)
HEADING_RE = re.compile(rf"^#{{3,4}}\s+({TASK_ID})\b(.*)$")  # O(1)
ANY_HEADING_RE = re.compile(r"^#{1,4}\s")  # O(1)
FIELD_RE = re.compile(r"^\*\*([^*:]+?)(?:\s*\([^)]*\))?:\*\*\s*(.*)$")  # O(1)
EG_RE = re.compile(r"^\s*(?:[-*]\s+)?(?:\*\*)?e\.g\.(?:\*\*)?:?\s*(.+?)\s*$", re.I)  # an example line under Produces | O(1)
FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})(.*)$")  # O(1)


class PlanError(Exception):  # O(1)
    def __init__(self, message: str, code: int = 2):  # O(1)
        super().__init__(message)  # O(1)
        self.code = code  # O(1)


def nkey(tid: str):  # O(1)
    m = re.match(r"T(\d+)([a-z]*)$", tid)  # O(1)
    return (int(m.group(1)), m.group(2)) if m else (10**9, tid)  # O(1)


# --------------------------------------------------------------------------
# Plan parsing
# --------------------------------------------------------------------------

@dataclass  # O(1)
class FenceBlock:  # O(1)
    start: int  # index of opening fence line | O(1)
    end: int  # index of closing fence line (or last line if unterminated) | O(1)
    info: str  # O(1)
    content: list[str]  # O(1)


def scan_fences(lines: list[str]):  # O(L+C) total
    """Return (fenced flags per line, top-level fence blocks)."""  # O(1)
    flags: list[bool] = []  # O(1)
    blocks: list[FenceBlock] = []  # O(1)
    opened = None  # (char, length, info, start) | O(1)
    for i, ln in enumerate(lines):  # O(L) iterations
        m = FENCE_RE.match(ln)  # O(len line)
        if opened is None:  # O(1)
            if m:  # O(1)
                opened = (m.group(1)[0], len(m.group(1)), m.group(2).strip(), i)  # O(len line)
                flags.append(True)  # O(1) amortized
            else:  # O(1)
                flags.append(False)  # O(1)
        else:  # O(1)
            flags.append(True)  # O(1)
            if m and m.group(1)[0] == opened[0] and len(m.group(1)) >= opened[1] and not m.group(2).strip():  # O(len line)
                blocks.append(FenceBlock(opened[3], i, opened[2], lines[opened[3] + 1:i]))  # O(block len) slice copy; O(L) over all blocks
                opened = None  # O(1)
    if opened is not None:  # O(1)
        blocks.append(FenceBlock(opened[3], len(lines) - 1, opened[2], lines[opened[3] + 1:]))  # O(L) slice copy
    return flags, blocks  # O(1)


def expand_braces(s: str) -> list[str]:  # O(s*A) total, A = expansions
    m = re.search(r"\{([^{}]*)\}", s)  # O(s) regex scan
    if not m:  # O(1)
        return [s]  # O(1)
    out: list[str] = []  # O(1)
    for alt in m.group(1).split(","):  # O(alts) iterations
        out.extend(expand_braces(s[:m.start()] + alt.strip() + s[m.end():]))  # O(s) per alt + recursion
    return out  # O(A)


def split_top_level(s: str, sep: str = ",") -> list[str]:  # O(s) total (CPython in-place concat)
    parts, depth, cur = [], 0, ""  # O(1)
    for ch in s:  # O(s) iterations
        if ch in "{([":  # O(1)
            depth += 1  # O(1)
        elif ch in "})]":  # O(1)
            depth -= 1  # O(1)
        if ch == sep and depth == 0:  # O(1)
            parts.append(cur)  # O(1)
            cur = ""  # O(1)
        else:  # O(1)
            cur += ch  # O(1) amortized
    parts.append(cur)  # O(1)
    return [p.strip() for p in parts if p.strip()]  # O(s)


def strip_ticks(s: str) -> str:  # O(s)
    return s.strip().strip("`").strip()  # O(s)


@dataclass  # O(1)
class Task:  # O(1)
    id: str  # O(1)
    title: str = ""  # O(1)
    heading: str = ""  # O(1)
    line: int = 0  # O(1)
    fields: dict[str, list[str]] = field(default_factory=dict)  # O(1)
    text: str = ""  # whole section text | O(1)
    synthetic: bool = False  # O(1)

    @property  # O(1)
    def status(self) -> str:  # O(k*s)
        """`draft` when the block has `**Status:** draft` (any case); anything else is `approved`."""
        return "draft" if self.field_text("status").strip().lower() == "draft" else "approved"  # O(k*s)

    def field_text(self, name: str) -> str:  # O(k*s)
        return " ".join(x for x in self.fields.get(name, []) if x).strip()  # O(k*s)

    @property  # O(1)
    def owns(self) -> list[str]:  # O(k*s*A)
        out: list[str] = []  # O(1)
        for raw in self.fields.get("owns", []):  # O(k) iterations
            for part in split_top_level(raw):  # O(parts) iterations; split O(s)
                part = strip_ticks(part.lstrip("-* ").strip())  # O(s)
                if part:  # O(1)
                    out.extend(expand_braces(part))  # O(s*A)
        return out  # O(1)

    @property  # O(1)
    def owned_sources(self) -> list[str]:  # O(k*s*A + o*s) total
        res = []  # O(1)
        for p in self.owns:  # O(o) iterations (+ owns O(k*s*A))
            q = p.replace("\\", "/")  # O(s)
            name = q.rsplit("/", 1)[-1]  # O(s)
            if q.endswith(".py") and not q.startswith("tests/") and "/tests/" not in q and not name.startswith("test_"):  # O(s)
                res.append(q)  # O(1)
        return res  # O(1)

    def _items(self, name: str) -> list[str]:  # O(k*s)
        items: list[str] = []  # O(1)
        for raw in self.fields.get(name, []):  # O(k) iterations
            raw = raw.strip()  # O(s)
            if not raw:  # O(1)
                continue  # O(1)
            for part in re.split(r"\s+·\s+", raw):  # O(parts) iterations; split O(s)
                part = strip_ticks(part.lstrip("-* ").strip())  # O(s)
                if part:  # O(1)
                    items.append(part)  # O(1)
        return items  # O(1)

    @property  # O(1)
    def produces(self) -> list[str]:  # O(k*s)
        return self._items("produces")  # O(k*s)

    @property  # O(1)
    def examples(self) -> list[str]:  # O(k*s)
        """`e.g.` lines: hand-built examples of what the Produces interfaces return (a call and its result)."""
        return [strip_ticks(x.strip()) for x in self.fields.get("e.g.", []) if x.strip()]  # O(k*s)

    @property  # O(1)
    def consumes(self) -> list[str]:  # O(k*s)
        return self._items("consumes")  # O(k*s)

    @property  # O(1)
    def contract_text(self) -> str:  # O(k*s)
        return self.field_text("contract") or self.field_text("notes")  # O(k*s)

    @property  # O(1)
    def stated_time(self) -> float | None:  # O(k*s)
        m = re.search(r"Est\.\s*time[:\s]+~?\s*(\d+(?:\.\d+)?)\s*min", self.heading)  # O(s)
        if m:  # O(1)
            return float(m.group(1))  # O(1)
        m = re.match(r"~?\s*(\d+(?:\.\d+)?)\s*min", self.field_text("est. time"))  # O(k*s)
        return float(m.group(1)) if m else None  # O(1)

    @property  # O(1)
    def stated_context(self) -> int | None:  # O(k*s)
        m = re.search(r"Est\.\s*context[:\s]+~?\s*(\d+(?:\.\d+)?)\s*k", self.heading)  # O(s)
        if not m:  # O(1)
            m = re.match(r"~?\s*(\d+(?:\.\d+)?)\s*k", self.field_text("est. context"))  # O(k*s)
        return int(round(float(m.group(1)) * 1000)) if m else None  # O(1)

    @property  # O(1)
    def complexity(self) -> int | None:  # O(s)
        m = re.search(r"Complexity\s+(\d+)", self.heading)  # O(s)
        return int(m.group(1)) if m else None  # O(1)

    @property  # O(1)
    def features(self) -> dict:  # O(k*s)
        """Measurable features incl. the legacy Est. time parenthetical as a fallback."""  # O(1)
        return self._features(True)  # O(k*s)

    @property  # O(1)
    def explicit_features(self) -> dict:  # O(k*s)
        """Only what the plan states as fields: **Features:** or **Components/LOC owned/Tests/Model/Kind:**."""  # O(1)
        return self._features(False)  # O(k*s)

    @property  # O(1)
    def has_features(self) -> bool:  # O(k*s)
        """True when the coordinator wrote features; then the predictor's value wins over Est. time."""  # O(1)
        return bool(set(self.explicit_features) & {"components", "loc", "tests"})  # O(k*s)

    def _features(self, parenthetical: bool) -> dict:  # O(k*s) total
        f: dict = {}  # O(1)

        def take(text: str):  # O(s) per call
            for key, pat in (  # O(1) iterations (4 patterns)
                ("components", r"components?\s*[=:]?\s*(\d+)|(\d+)\s+components?"),  # O(1)
                ("loc", r"\bloc(?:\s+owned)?\s*[=:]?\s*~?(\d+)|(?:about\s+|~)?(\d+)\s*loc\b"),  # O(1)
                ("docs_chars", r"docs?(?:\s+chars)?\s*[=:]\s*(\d+)"),  # O(1)
                ("reads_loc", r"reads?(?:\s+loc)?\s*[=:]\s*(\d+)"),  # O(1)
            ):  # O(1)
                if key in f:  # O(1)
                    continue  # O(1)
                m = re.search(pat, text, re.I)  # O(s)
                if m:  # O(1)
                    f[key] = int(next(g for g in m.groups() if g))  # O(1)
            if "tests" not in f:  # O(1)
                m = re.search(r"tests?\s*[=:]\s*(fast|slow)|\b(fast|slow)\s+tests?\b", text, re.I)  # O(s)
                if m:  # O(1)
                    f["tests"] = (m.group(1) or m.group(2)).lower()  # O(1)
            if "model" not in f:  # O(1)
                m = re.search(r"model\s*[=:]\s*([A-Za-z][\w.\-]*)", text, re.I)  # O(s)
                if m:  # O(1)
                    f["model"] = m.group(1).lower()  # O(1)

        take(self.field_text("features"))  # O(k*s)
        km = re.search(r"\b(mechanical|design)\b", self.field_text("kind"), re.I)  # O(k*s)
        if km:  # O(1)
            f["kind"] = km.group(1).lower()  # O(1) absent line means design
        for name, key in (("components", "components"), ("loc owned", "loc"), ("loc", "loc"),  # O(1) iterations (6 pairs)
                          ("model", "model"), ("tests", "tests"), ("test speed", "tests")):  # O(1)
            val = self.field_text(name)  # O(k*s)
            if val and key not in f:  # O(1)
                take(f"{key}={val}")  # O(k*s)
        if parenthetical and ("loc" not in f or "components" not in f or "tests" not in f):  # O(1)
            m = re.search(r"\(([^)]*)\)", self.field_text("est. time"))  # O(k*s)
            if m:  # O(1)
                take(m.group(1))  # O(s)
        return f  # O(1)


class Plan:  # O(1)
    def __init__(self, text: str, path: Path | None = None):  # O(C+L) total
        self.path = path  # O(1)
        self.eol = "\r\n" if "\r\n" in text else "\n"  # O(C)
        self.text = text.replace("\r\n", "\n")  # O(C)
        self.lines = self.text.split("\n")  # O(C)
        self.fenced, self.blocks = scan_fences(self.lines)  # O(C+L)
        self.tasks: list[Task] = self._parse_tasks()  # O(C+L)
        self.by_id = {t.id: t for t in self.tasks}  # O(n)

    @classmethod  # O(1)
    def load(cls, path: str) -> "Plan":  # O(C+L) total + file I/O
        p = Path(path)  # O(1)
        if not p.is_file():  # O(1) stat I/O
            raise PlanError(f"plan not found: {path}")  # O(1)
        return cls(p.read_bytes().decode("utf-8-sig"), p)  # O(C) file read + decode + parse

    def _parse_tasks(self) -> list[Task]:  # O(C+L) total
        tasks: list[Task] = []  # O(1)
        cur: Task | None = None  # O(1)
        cur_field: str | None = None  # O(1)
        body: list[str] = []  # O(1)

        def close():  # O(body chars)
            if cur is not None:  # O(1)
                cur.text = "\n".join(body)  # O(body chars); O(C) over all calls

        for i, ln in enumerate(self.lines):  # O(L) iterations
            if not self.fenced[i]:  # O(1)
                m = HEADING_RE.match(ln)  # O(s)
                if m:  # O(1)
                    close()  # O(body chars)
                    title = m.group(2).strip().lstrip("·").strip()  # O(s)
                    title = re.split(r"\s+·\s+|\s{2,}", title)[0].strip()  # O(s)
                    cur = Task(id=m.group(1), title=title, heading=ln, line=i)  # O(1)
                    tasks.append(cur)  # O(1)
                    cur_field, body = None, [ln]  # O(1)
                    continue  # O(1)
                if ANY_HEADING_RE.match(ln):  # O(s)
                    close()  # O(body chars)
                    cur, cur_field, body = None, None, []  # O(1)
                    continue  # O(1)
            if cur is None:  # O(1)
                continue  # O(1)
            body.append(ln)  # O(1) amortized
            if self.fenced[i]:  # O(1)
                continue  # O(1)
            em = EG_RE.match(ln)  # O(s)
            if em:  # an example line stays with its task and keeps its own field | O(1)
                cur.fields.setdefault("e.g.", []).append(em.group(1).strip())  # O(s)
                cur_field = "e.g."  # O(1)
                continue  # O(1)
            fm = FIELD_RE.match(ln.strip())  # O(s)
            if fm:  # O(1)
                cur_field = fm.group(1).strip().lower()  # O(s)
                cur.fields.setdefault(cur_field, []).append(fm.group(2).strip())  # O(s)
            elif cur_field and re.match(r"^\s*[-*]\s+", ln):  # O(s)
                cur.fields[cur_field].append(ln.strip())  # O(s)
            elif not ln.strip():  # O(s)
                cur_field = None  # O(1)
            elif cur_field and ln.startswith((" ", "\t")):  # O(1)
                cur.fields[cur_field].append(ln.strip())  # O(1)
            else:  # O(1)
                cur_field = None  # O(1)
        close()  # O(body chars)
        return tasks  # O(1)

    def mermaid_blocks(self) -> list[FenceBlock]:  # O(b)
        return [b for b in self.blocks if b.info.lower().startswith("mermaid")]  # O(b)

    def header_target(self) -> float | None:  # O(C)
        m = re.search(r"^\*\*Target(?:[-\s]*(?:min|minutes|time))?:\*\*\s*~?\s*(\d+(?:\.\d+)?)", self.text, re.M)  # O(C)
        return float(m.group(1)) if m else None  # O(1)

    def header_ignore(self) -> list[str] | None:  # O(L)
        """Entries of the `**Ignore:** a/, b, *.c` header line (comma-separated paths or globs); None without the line."""
        for ln, f in zip(self.lines, self.fenced):  # O(L)
            m = None if f else re.match(r"^\*\*Ignore:\*\*\s*(.*)$", ln)  # O(len line)
            if m:  # O(1)
                return [e.strip().strip("`").strip() for e in m.group(1).split(",") if e.strip().strip("`").strip()]  # O(len line)
        return None  # O(1)

    def header_execution(self) -> dict:  # O(L)
        """Parse `**Execution:** host=cursor, parallel=multitask, swarm_by=tier` (empty dict if absent)."""
        import host_models as hm  # O(1) lazy
        return hm.parse_execution(self.non_fenced_text())  # O(L)

    def non_fenced_text(self) -> str:  # O(L)
        return "\n".join(ln for ln, f in zip(self.lines, self.fenced) if not f)  # O(L)

    def to_text(self) -> str:  # O(C)
        return "\n".join(self.lines).replace("\n", self.eol) if self.eol != "\n" else "\n".join(self.lines)  # O(C)


# --------------------------------------------------------------------------
# Task graph
# --------------------------------------------------------------------------

def find_taskgraph_blocks(plan: Plan) -> list[tuple[FenceBlock, int]]:  # O(L) total
    found = []  # O(1)
    for b in plan.mermaid_blocks():  # O(b) iterations
        for j, ln in enumerate(b.content):  # O(block lines) iterations
            if re.match(r"^\s*%%\s*taskgraph\b", ln):  # O(s)
                found.append((b, j))  # O(1)
                break  # O(1)
    return found  # O(1)


def _node_token(part: str) -> str:  # O(s)
    cleaned = re.sub(r'\[[^\]]*\]|\([^)]*\)|\{[^}]*\}|"[^"]*"', "", part).strip()  # O(s)
    m = re.match(r"[A-Za-z_][\w\-]*", cleaned)  # O(s)
    return m.group(0) if m else cleaned  # O(1)


def parse_taskgraph(plan: Plan) -> dict[str, list[str]]:  # O(L*s + R*M + e log n) total
    blocks = find_taskgraph_blocks(plan)  # O(L) scan
    if not blocks:  # O(1)
        raise PlanError("no '%% taskgraph' block found in plan")  # O(1)
    deps: dict[str, set[str]] = {}  # O(1)
    mentioned: list[str] = []  # O(1)
    skip = ("flowchart", "graph", "subgraph", "end", "direction", "classdef", "style", "class", "click", "linkstyle")  # O(1)
    for block, j in blocks:  # O(blocks) iterations
        for raw in block.content[j + 1:]:  # O(G) iterations
            ln = raw.strip()  # O(s)
            if not ln or ln.startswith("%%"):  # O(1)
                continue  # O(1)
            if ln.split()[0].lower() in skip:  # O(s)
                continue  # O(1)
            parts = re.split(r"\s*-->\s*(?:\|[^|]*\|\s*)?", ln)  # O(s)
            groups: list[list[str]] = []  # O(1)
            for part in parts:  # O(parts) iterations
                ids = [_node_token(p) for p in part.split("&") if p.strip()]  # O(s)
                groups.append(ids)  # O(1)
            for ids in groups:  # O(parts) iterations
                for tid in ids:  # O(ids) iterations
                    if not TASK_ID_RE.match(tid):  # O(s)
                        raise PlanError(f"unknown task id {tid!r} in taskgraph")  # O(1)
                    deps.setdefault(tid, set())  # O(1)
                    if tid not in mentioned:  # O(M) x in list scan; hotspot
                        mentioned.append(tid)  # O(1)
            for left, right in zip(groups, groups[1:]):  # O(parts) iterations
                for b_ in right:  # O(right) iterations
                    for a_ in left:  # O(left) iterations; O(1) per edge
                        deps[b_].add(a_)  # O(1)
    known = set(plan.by_id)  # O(n)
    if known:  # O(1)
        for tid in mentioned:  # O(M) iterations
            if tid not in known:  # O(1)
                raise PlanError(f"unknown task id {tid!r} in taskgraph (no such task in plan)")  # O(1)
    order = [t.id for t in plan.tasks] + [t for t in mentioned if t not in known]  # O(n+M)
    graph: dict[str, list[str]] = {}  # O(1)
    for tid in order:  # O(n) iterations
        graph[tid] = sorted(deps.get(tid, set()), key=nkey)  # O(d log d)
    cycle = find_cycle(graph)  # O(n+e)
    if cycle:  # O(1)
        raise PlanError("cycle in taskgraph: " + " → ".join(cycle))  # O(n)
    return graph  # O(1)


def find_cycle(graph: dict[str, list[str]]) -> list[str] | None:  # O(n+e) total
    state: dict[str, int] = {}  # O(1)
    stack: list[str] = []  # O(1)

    def visit(n: str):  # O(deg) + recursion
        state[n] = 1  # O(1)
        stack.append(n)  # O(1)
        for d in graph.get(n, []):  # O(deg) iterations
            if state.get(d, 0) == 1:  # O(1)
                return stack[stack.index(d):] + [d]  # O(n) once, only on cycle found
            if state.get(d, 0) == 0:  # O(1)
                r = visit(d)  # O(1)
                if r:  # O(1)
                    return r  # O(1)
        stack.pop()  # O(1)
        state[n] = 2  # O(1)
        return None  # O(1)

    for n in graph:  # O(n) iterations
        if state.get(n, 0) == 0:  # O(1)
            r = visit(n)  # O(1)
            if r:  # O(1)
                return list(reversed(r))  # O(n) once
    return None  # O(1)


def ancestors(graph: dict[str, list[str]]) -> dict[str, set[str]]:  # O(e*n) time, O(n^2) space
    memo: dict[str, set[str]] = {}  # O(1)

    def anc(n: str) -> set[str]:  # O(deg*n) per call
        if n in memo:  # O(1)
            return memo[n]  # O(1)
        memo[n] = set()  # O(1)
        s: set[str] = set()  # O(1)
        for d in graph.get(n, []):  # O(deg) iterations
            s.add(d)  # O(1)
            s |= anc(d)  # O(n) set union
        memo[n] = s  # O(1)
        return s  # O(1)

    for n in graph:  # O(n) iterations
        anc(n)  # O(1)
    return memo  # O(1)


def seams_of(graph: dict[str, list[str]]) -> list[str]:  # O(n+e)
    out = []  # O(1)
    for t in graph:  # O(n) iterations
        for d in graph[t]:  # O(deg) iterations
            out.append(f"{d}↔{t}")  # O(1) per edge
    return out  # O(n+e)


# --------------------------------------------------------------------------
# Predictor glue and scheduling
# --------------------------------------------------------------------------

def _predict():  # O(1)
    if str(SCRIPT_DIR) not in sys.path:  # O(1)
        sys.path.insert(0, str(SCRIPT_DIR))  # O(1)
    import predict  # noqa: WPS433 (lazy: predict imports this module) | O(1) import (cached after first)
    return predict  # O(1)


def effective_times(plan: Plan, graph: dict[str, list[str]], args) -> tuple[dict[str, float], dict[str, str]]:  # O(n*p) total + config I/O
    """Per-task minutes. Features present: the predictor's value wins. A stated `Est. time` is used only
    for a task with no features, or for every task with --stated."""
    predict = _predict()  # O(1)
    models = predict.load_models(getattr(args, "models", None))  # O(config size) file I/O
    coef = predict.load_coef(getattr(args, "coef", None))  # O(config size) file I/O
    force_stated = bool(getattr(args, "stated", False))  # O(1)
    times: dict[str, float] = {}  # O(1)
    source: dict[str, str] = {}  # O(1)
    for tid in graph:  # O(n) iterations
        task = plan.by_id.get(tid) or Task(id=tid, synthetic=True)  # O(1)
        stated = task.stated_time if (force_stated or not task.has_features) else None  # O(k*s)
        if stated is not None:  # O(1)
            times[tid], source[tid] = float(stated), "stated"  # O(1)
        else:  # O(1)
            p = predict.predict_task(task, models, coef)  # O(p)
            times[tid], source[tid] = p["est_time_min"], p["source"]  # O(1)
    return times, source  # O(1)


def effective_context(task: Task, models: dict, coef: dict, force_stated: bool = False) -> tuple[int | None, str]:  # O(k*s + p)
    """Per-task context tokens. Features present: the predictor's value wins, as for Est. time. A stated
    `Est. context` is used only for a task with no features, or with --stated. (None, "none") if neither."""
    predict = _predict()  # O(1)
    stated = task.stated_context  # O(k*s)
    if stated is not None and (force_stated or not task.has_features):  # O(k*s)
        return stated, "stated"  # O(1)
    if task.has_features:  # O(k*s)
        return predict.predict_task(task, models, coef)["est_context"], "predicted"  # O(p)
    return None, "none"  # O(1)


def simulate(times: dict[str, float], graph: dict[str, list[str]], cap: int) -> dict:  # O(n*(n+e) + n*c) total
    order = {t: i for i, t in enumerate(sorted(graph, key=nkey))}  # O(n log n)
    done: dict[str, float] = {}  # O(1)
    start: dict[str, float] = {}  # O(1)
    lane: dict[str, int] = {}  # O(1)
    running: list[tuple[float, int, str, int]] = []  # O(1)
    free = list(range(1, max(1, cap) + 1))  # O(c)
    remaining = set(graph)  # O(n)
    now = 0.0  # O(1)
    while remaining or running:  # O(n) iterations
        ready = [t for t in remaining if all(d in done for d in graph[t])]  # O(n+e) per iteration
        ready.sort(key=lambda t: (-times[t], order[t]))  # O(r log r)
        while ready and free:  # O(started) iterations
            t = ready.pop(0)  # O(r) list.pop(0)
            ln = min(free)  # O(c) min
            free.remove(ln)  # O(c) list.remove
            start[t], lane[t] = now, ln  # O(1)
            running.append((now + times[t], order[t], t, ln))  # O(1)
            remaining.discard(t)  # O(1)
        if not running:  # O(1)
            break  # O(1)
        running.sort()  # O(c log c)
        now = running[0][0]  # O(1)
        while running and running[0][0] <= now + 1e-12:  # O(finished) iterations
            end, _, t, ln = running.pop(0)  # O(c) list.pop(0)
            done[t] = end  # O(1)
            free.append(ln)  # O(1)
    events = []  # O(1)
    for t in start:  # O(n) iterations
        if times[t] > 0:  # O(1)
            events.append((start[t], 1))  # O(1)
            events.append((done[t], -1))  # O(1)
    events.sort(key=lambda e: (e[0], e[1]))  # O(n log n)
    width = peak = 0  # O(1)
    for _, d in events:  # O(n) iterations
        width += d  # O(1)
        peak = max(peak, width)  # O(1)
    # critical path on the dependency graph (unlimited lanes)
    ef: dict[str, float] = {}  # O(1)
    pred: dict[str, str | None] = {}  # O(1)
    anc_all = ancestors(graph)  # O(e*n) ancestors only to order by depth
    for t in sorted(graph, key=lambda x: (len(anc_all[x]), nkey(x))):  # O(n) iterations; sort O(n log n)
        best, bp = 0.0, None  # O(1)
        for d in sorted(graph[t], key=nkey):  # O(d) iterations; sorted O(d log d)
            if ef[d] > best + 1e-12:  # O(1)
                best, bp = ef[d], d  # O(1)
        ef[t] = best + times[t]  # O(1)
        pred[t] = bp  # O(1)
    if ef:  # O(1)
        endt = max(sorted(ef, key=nkey), key=lambda x: ef[x])  # O(n log n) sort + max
        path = [endt]  # O(1)
        while pred[path[-1]]:  # O(path) iterations
            path.append(pred[path[-1]])  # O(1)
        path.reverse()  # O(path)
        cp_len = ef[endt]  # O(1)
    else:  # O(1)
        path, cp_len = [], 0.0  # O(1)
    return {  # O(1)
        "start": start, "end": done, "lane": lane,  # O(1)
        "build_min": max(done.values()) if done else 0.0,  # O(n)
        "peak_width": peak,  # O(1)
        "critical_path": path,  # O(1)
        "critical_path_min": cp_len,  # O(1)
    }  # O(1)


def schedule_report(plan: Plan, args) -> dict:  # O(n*(n+e) + n*p) total
    graph = parse_taskgraph(plan)  # O(L*s + R*M + e log n)
    drafts = draft_ids(plan)  # O(n*k*s) draft tasks are not scheduled
    graph = {t: [d for d in deps if d not in drafts] for t, deps in graph.items() if t not in drafts}  # O(n+e)
    times, source = effective_times(plan, graph, args)  # O(n*p)
    cap = args.cap  # O(1)
    sim = simulate(times, graph, cap)  # O(n*(n+e))
    predict = _predict()  # O(1)
    coef = predict.load_coef(getattr(args, "coef", None))  # O(config size) file I/O
    sched = coef.get("schedule") or {}  # O(1)
    # Run clock: setup + scaled build + wrap-up (replaces build + flat tail).
    pre = float(sched.get("pre_build_min", 0.0))  # O(1)
    post = float(sched.get("post_build_min", sched.get("tail_min", 3.5)))  # O(1)
    scale = float(sched.get("build_scale", 1.0))  # O(1)
    if getattr(args, "tail_min", None) is not None:  # --tail-min overrides wrap-up only | O(1)
        post = float(args.tail_min)  # O(1)
    target = args.target_min if getattr(args, "target_min", None) is not None else plan.header_target()  # O(C)
    build = float(sim["build_min"])  # O(1)
    total = pre + scale * build + post  # O(1)
    sim.update({  # O(n) dict update
        "graph": graph, "times": times, "source": source,  # O(1)
        "pre_build_min": pre, "build_scale": scale, "post_build_min": post,  # O(1)
        "tail_min": post,  # alias: wrap-up (old name kept for JSON consumers) | O(1)
        "total_min": total, "target_min": target,  # O(1)
        "target_met": None if target is None else total <= target + 1e-9,  # O(1)
        "cap": cap,  # O(1)
    })  # O(1)
    return sim  # O(1)


def summary_line(rep: dict) -> str:  # O(path)
    path = " → ".join(rep["critical_path"]) or "-"  # O(path)
    line = (f"critical path: {path} ({rep['critical_path_min']:.1f} min) · peak width {rep['peak_width']} · "  # O(path)
            f"predicted build {rep['build_min']:.1f} min · predicted total ≈ {rep['total_min']:.1f} min")  # O(1)
    if rep["target_min"] is not None:  # O(1)
        line += " (target ✓)" if rep["target_met"] else f" (target ✗ {rep['target_min']:.1f})"  # O(1)
    return line  # O(1)


def gantt_block(plan: Plan, rep: dict) -> list[str]:  # O(n log n + n*s) total
    lines = ["```mermaid", "%% schedule", "gantt", "  title Predicted schedule (mm:ss)",  # O(1)
             "  dateFormat X", "  axisFormat %M:%S"]  # O(1)
    by_lane: dict[int, list[str]] = {}  # O(1)
    for t in sorted(rep["start"], key=lambda x: (rep["lane"][x], rep["start"][x], nkey(x))):  # O(n log n)
        by_lane.setdefault(rep["lane"][t], []).append(t)  # O(1)
    for ln in sorted(by_lane):  # O(lanes log lanes)
        lines.append(f"  section lane {ln}")  # O(1)
        for t in by_lane[ln]:  # O(n) total
            task = plan.by_id.get(t)  # O(1)
            title = re.sub(r"[:;#,]", " ", task.title if task else "").strip()  # O(s)
            label = f"{t} {title}".strip()[:40]  # O(1)
            s = int(round(rep["start"][t] * 60))  # O(1)
            d = max(1, int(round(rep["times"][t] * 60)))  # O(1)
            lines.append(f"  {label} :{t.lower()}, {s}, {d}s")  # O(1)
    lines.append("```")  # O(1)
    return lines  # O(1)


def insert_schedule(plan: Plan, rep: dict) -> None:  # O(L+C+n log n) total + file I/O
    new = gantt_block(plan, rep) + ["", summary_line(rep)]  # O(n log n)
    old = None  # O(1)
    for b in plan.mermaid_blocks():  # O(b) iterations
        if any(re.match(r"^\s*%%\s*schedule\b", ln) for ln in b.content):  # O(block lines*s)
            old = b  # O(1)
            break  # O(1)
    if old is not None:  # O(1)
        s, e = old.start, old.end + 1  # O(1)
        k = e  # O(1)
        while k < len(plan.lines) and not plan.lines[k].strip():  # O(blank lines) iterations
            k += 1  # O(1)
        if k < len(plan.lines) and plan.lines[k].startswith("critical path:"):  # O(1)
            e = k + 1  # O(1)
        plan.lines[s:e] = new  # O(L) slice assign
    else:  # O(1)
        blocks = find_taskgraph_blocks(plan)  # O(L)
        if not blocks:  # O(1)
            raise PlanError("no '%% taskgraph' block found in plan")  # O(1)
        at = blocks[-1][0].end + 1  # O(1)
        plan.lines[at:at] = [""] + new  # O(L) list insert
    if plan.path is None:  # O(1)
        return  # O(1)
    plan.path.write_bytes(plan.to_text().encode("utf-8"))  # O(C) encode + file write I/O


# --------------------------------------------------------------------------
# Signatures and stubs
# --------------------------------------------------------------------------

# A trailing "(T4)" / "(T4, T5)" / "(T14, names only)" names the producer; the note after the ids is ignored.
REF_TAIL = r"\s+\((T\d+[a-z]*(?:\s*[,/]\s*T\d+[a-z]*)*)(?:\s*[,;:]\s*[^()]*)?\)\s*$"  # O(1)


def parse_signature(item: str) -> dict | None:  # O(s) total
    s = strip_ticks(item)  # O(1)
    s = re.sub(REF_TAIL, "", s)  # O(s)
    m = re.match(r"^([A-Za-z_][\w.]*)\s*\(", s)  # O(1)
    if not m:  # O(1)
        return None  # O(1)
    depth, close = 0, None  # O(1)
    for i in range(m.end() - 1, len(s)):  # O(s) iterations
        if s[i] in "([{":  # O(1)
            depth += 1  # O(1)
        elif s[i] in ")]}":  # O(1)
            depth -= 1  # O(1)
            if depth == 0:  # O(1)
                close = i  # O(1)
                break  # O(1)
    if close is None:  # O(1)
        return None  # O(1)
    args = s[m.end():close].strip()  # O(s)
    rest = s[close + 1:].strip()  # O(1)
    ret = None  # O(1)
    if rest.startswith("->"):  # O(1)
        ret = strip_ticks(rest[2:]).rstrip(".;,").strip() or None  # O(s)
    segs = m.group(1).split(".")  # O(s)
    member = segs[-1]  # O(1)
    cls, mod = None, segs[:-1]  # O(1)
    if len(segs) >= 2 and segs[-2][:1].isupper():  # O(1)
        cls, mod = segs[-2], segs[:-2]  # O(1)
    elif member[:1].isupper():  # O(1)
        cls, member, mod = member, "__init__", segs[:-1]  # O(1)
    return {"name": m.group(1), "args": args, "ret": ret, "module": mod, "cls": cls, "member": member, "item": s}  # O(1)


def param_names(args: str) -> list[str]:  # O(s) total
    names = []  # O(1)
    for p in split_top_level(args):  # O(params) iterations
        p = p.strip()  # O(s)
        if p in ("/", "*"):  # O(1)
            continue  # O(1)
        p = p.lstrip("*")  # O(s)
        names.append(re.split(r"[:=\s]", p, 1)[0])  # O(s)
    return names  # O(1)


def resolve_module(prefix: list[str], owned: list[str]) -> tuple[str | None, str | None]:  # O(o*s) total
    """Return (relative path, warning)."""  # O(1)
    if not prefix:  # O(1)
        if len(owned) == 1:  # O(1)
            return owned[0], None  # O(1)
        non_init = [o for o in owned if not o.endswith("__init__.py")]  # O(o*s)
        if len(non_init) == 1:  # O(1)
            return non_init[0], None  # O(1)
        return None, "cannot tell which owned file holds it (name a module in the Produces line)"  # O(1)
    p = "/".join(prefix)  # O(1)
    cands = [o for o in owned if o[:-3] == p or o[:-3].endswith("/" + p)]  # O(o*s)
    if len(cands) == 1:  # O(1)
        return cands[0], None  # O(1)
    stem = prefix[-1]  # O(1)
    cands = [o for o in owned if o.rsplit("/", 1)[-1][:-3] == stem]  # O(o*s)
    if len(cands) == 1:  # O(1)
        return cands[0], None  # O(1)
    return p + ".py", f"module {'.'.join(prefix)!r} is not in the task's Owns; using {p}.py"  # O(s)


def _doc(text: str, indent: str) -> str:  # O(s)
    text = text.replace("\\", "\\\\").replace('"""', "'''")  # O(s)
    return f'{indent}"""{text}"""'  # O(s)


def _def_source(sig: dict, indent: str, tid: str, contract: str, is_method: bool) -> str:  # O(s) total
    args = sig["args"]  # O(1)
    if is_method:  # O(1)
        first = param_names(args)[:1]  # O(s)
        if not first or first[0] not in ("self", "cls"):  # O(1)
            args = "self" + (", " + args if args else "")  # O(s)
    ret = f" -> {sig['ret']}" if sig["ret"] else ""  # O(1)
    doc = f"Contract: {contract}" if contract else f"Contract: see plan task {tid}."  # O(1)
    body = f'{indent}def {sig["member"]}({args}){ret}:\n{_doc(doc, indent + "    ")}\n{indent}    raise NotImplementedError("{tid}")\n'  # O(s)
    return body  # O(1)


def build_stub_defs(task: Task, warnings: list[str]):  # O(q*(k*s + s)) total
    """Yield (relative path, sig, source-for-def, contract) for each Produces signature."""  # O(1)
    out = []  # O(1)
    for item in task.produces:  # O(q) iterations
        sig = parse_signature(item)  # O(s)
        if sig is None:  # O(1)
            continue  # O(1)
        path, warn = resolve_module(sig["module"], task.owned_sources)  # O(k*s*A + o*s) owned_sources recomputed per item
        if warn:  # O(1)
            warnings.append(f"{task.id}: {sig['name']}: {warn}")  # O(1)
        if path is None:  # O(1)
            continue  # O(1)
        for attempt in (sig, dict(sig, ret=None)):  # O(1) iterations (2 attempts)
            try:  # O(1)
                compile("from __future__ import annotations\n" + _def_source(attempt, "", task.id, "", False), "<stub>", "exec")  # O(s) compile
                sig = attempt  # O(1)
                break  # O(1)
            except SyntaxError:  # O(1)
                sig = None  # O(1)
        if sig is None:  # O(1)
            warnings.append(f"{task.id}: cannot parse signature {item!r}; skipped")  # O(s)
            continue  # O(1)
        out.append((path, sig))  # O(1)
    return out  # O(1)


def render_module(path: str, entries: list[tuple[Task, dict]], warnings: list[str]) -> str:  # O(E*s) total
    funcs: list[tuple[Task, dict]] = []  # O(1)
    classes: dict[str, list[tuple[Task, dict]]] = {}  # O(1)
    seen = set()  # O(1)
    for task, sig in entries:  # O(E) iterations
        key = (sig["cls"], sig["member"])  # O(1)
        if key in seen:  # O(1)
            warnings.append(f"{path}: duplicate Produces for {sig['name']} ({task.id}); kept the first")  # O(1)
            continue  # O(1)
        seen.add(key)  # O(1)
        if sig["cls"]:  # O(1)
            classes.setdefault(sig["cls"], []).append((task, sig))  # O(1)
        else:  # O(1)
            funcs.append((task, sig))  # O(1)
    names = [s["member"] for _, s in funcs] + list(classes)  # O(E)
    out = ['"""Generated interface stubs (plan_tools.py stubs). The stub is the contract:',  # O(E)
           'do not change a signature without a coordinator ruling."""',  # O(1)
           "from __future__ import annotations", "",  # O(1)
           "__all__ = [" + ", ".join(json.dumps(n) for n in names) + "]", ""]  # O(E) json.dumps per name
    for task, sig in funcs:  # O(funcs) iterations
        out.append("")  # O(1)
        out.append(_def_source(sig, "", task.id, task.contract_text, False).rstrip("\n"))  # O(s)
        out.append("")  # O(1)
    for cname, items in classes.items():  # O(classes) iterations
        first = items[0][0]  # O(1)
        doc = f"Contract: {first.contract_text}" if first.contract_text else f"Contract: see plan task {first.id}."  # O(1)
        out.append("")  # O(1)
        out.append(f"class {cname}:")  # O(1)
        out.append(_doc(doc, "    "))  # O(1)
        for task, sig in items:  # O(members) iterations
            out.append("")  # O(1)
            out.append(_def_source(sig, "    ", task.id, task.contract_text, True).rstrip("\n"))  # O(s)
        out.append("")  # O(1)
    return "\n".join(out).rstrip("\n") + "\n"  # O(output size)


def import_check(path: Path) -> str | None:  # O(module size) + exec
    name = f"_stubcheck_{abs(hash(str(path)))}"  # O(s)
    try:  # O(1)
        spec = importlib.util.spec_from_file_location(name, path)  # O(1) stat I/O
        mod = importlib.util.module_from_spec(spec)  # O(1)
        spec.loader.exec_module(mod)  # O(module size) exec
    except Exception as exc:  # noqa: BLE001 | O(1)
        return f"{type(exc).__name__}: {exc}"  # O(1)
    finally:  # O(1)
        sys.modules.pop(name, None)  # O(1)
    return None  # O(1)


def draft_ids(plan: Plan) -> set[str]:  # O(n*k*s)
    return {t.id for t in plan.tasks if t.status == "draft"}  # O(n*k*s)


def _overlay_rc(root) -> int | None:  # O(file)
    """2 when superlite.json is present and unusable; None when it is missing or valid."""
    if str(SCRIPT_DIR) not in sys.path:  # O(1)
        sys.path.insert(0, str(SCRIPT_DIR))  # O(1)
    import project_config  # O(1)
    try:  # O(1)
        project_config.load(Path(root).resolve())  # O(file)
    except project_config.ProjectConfigError as exc:  # O(1)
        print(str(exc), file=sys.stderr)  # O(1)
        return 2  # O(1)
    return None  # O(1)


def _cmd_stub_spec(plan: Plan) -> int:  # O(n*q*s)
    """Non-Python: print one stub line and one contract line per Produces signature. Write nothing."""
    n = 0  # O(1)
    for task in plan.tasks:  # O(n)
        if task.id in draft_ids(plan):  # O(n) recomputed; n is small
            continue  # O(1)
        for item in task.produces:  # O(q)
            sig = parse_signature(item)  # O(s)
            if sig is None:  # O(1)
                print(f"warning: {task.id}: cannot parse signature {item!r}; skipped", file=sys.stderr)  # O(s)
                continue  # O(1)
            print(f"stub: {sig['item']}")  # O(s)
            print(f"contract: {task.contract_text}")  # O(k*s)
            n += 1  # O(1)
    if n == 0:  # O(1)
        print("no stubbable Produces lines found")  # O(1)
    return 0  # O(1)


def cmd_stubs(args) -> int:  # O(C+L + E*s) total + file I/O
    root = Path(args.root)  # O(1)
    bad = _overlay_rc(root)  # O(file)
    if bad:  # O(1)
        return bad  # O(1)
    if str(SCRIPT_DIR) not in sys.path:  # O(1)
        sys.path.insert(0, str(SCRIPT_DIR))  # O(1)
    import project_config  # O(1)
    plan = Plan.load(args.plan)  # O(C+L) + file read
    if project_config.language(root) != "python":  # O(file)
        return _cmd_stub_spec(plan)  # O(n*q*s)
    warnings: list[str] = []  # O(1)
    per_file: dict[str, list[tuple[Task, dict]]] = {}  # O(1)
    drafts = draft_ids(plan)  # O(n*k*s)
    for task in plan.tasks:  # O(n) iterations
        if task.id in drafts:  # O(1) draft tasks get no stubs
            continue  # O(1)
        for path, sig in build_stub_defs(task, warnings):  # O(q*k*s)
            per_file.setdefault(path, []).append((task, sig))  # O(1)
    rc = 0  # O(1)
    for rel in sorted(per_file):  # O(f) iterations; sorted O(f log f)
        target = root / rel  # O(1)
        if target.exists():  # O(1) stat I/O
            print(f"skipped {rel} (exists)")  # O(1)
            continue  # O(1)
        src = render_module(rel, per_file[rel], warnings)  # O(E*s)
        if args.dry_run:  # O(1)
            print(f"would write {rel}")  # O(1)
            continue  # O(1)
        target.parent.mkdir(parents=True, exist_ok=True)  # O(1) mkdir I/O
        target.write_text(src, encoding="utf-8", newline="\n")  # O(size) file write I/O
        print(f"wrote {rel}")  # O(1)
        err = import_check(target)  # O(module size) exec
        if err:  # O(1)
            print(f"import check failed for {rel}: {err}", file=sys.stderr)  # O(1)
            rc = 1  # O(1)
    for w in warnings:  # O(w) iterations
        print(f"warning: {w}", file=sys.stderr)  # O(1)
    if not per_file:  # O(1)
        print("no stubbable Produces lines found")  # O(1)
    return rc  # O(1)


# --------------------------------------------------------------------------
# taskgraph
# --------------------------------------------------------------------------

def mergeable_tasks(plan: Plan, graph, times, args) -> list[dict]:  # O(n*p + e*n + n*(n+e) + n^2*k*s) total
    predict = _predict()  # O(1)
    models = predict.load_models(getattr(args, "models", None))  # O(config size) file I/O
    coef = predict.load_coef(getattr(args, "coef", None))  # O(config size) file I/O
    ctx: dict[str, int | None] = {}  # O(1)
    known_time: dict[str, float | None] = {}  # O(1)
    for tid in graph:  # O(n) iterations
        task = plan.by_id.get(tid)  # O(1)
        if task is None:  # O(1)
            ctx[tid], known_time[tid] = None, None  # O(1)
            continue  # O(1)
        ctx[tid], _ = effective_context(task, models, coef, bool(getattr(args, "stated", False)))  # O(k*s + p)
        known_time[tid] = times.get(tid)  # O(1)
    anc = ancestors(graph)  # O(e*n)
    cp = set(simulate(times, graph, 1000)["critical_path"])  # O(n*(n+e)) full simulation just for critical path
    res = []  # O(1)
    for tid in graph:  # O(n) iterations
        reasons = []  # O(1)
        c, tm = ctx[tid], known_time[tid]  # O(1)
        if c is not None and c - IMPLEMENTER_PREFIX < MERGE_MIN_VARIABLE:  # O(1)
            reasons.append(f"variable context {(c - IMPLEMENTER_PREFIX) / 1000:.0f}k < {MERGE_MIN_VARIABLE // 1000}k")  # O(1)
        if tm is not None and tm < MERGE_MIN_MINUTES:  # O(1)
            reasons.append(f"predicted {tm:.1f} min < {MERGE_MIN_MINUTES:.0f} min")  # O(1)
        if not reasons:  # O(1)
            continue  # O(1)
        task = plan.by_id[tid]  # O(1)
        my_dirs = {o.rsplit("/", 1)[0] for o in task.owns if "/" in o}  # O(k*s*A)
        cands = []  # O(1)
        for other in graph:  # O(n) iterations; n^2 pairs total
            if other == tid:  # O(1)
                continue  # O(1)
            adjacent = other in graph[tid] or tid in graph[other]  # O(d) two list scans
            ot = plan.by_id.get(other)  # O(1)
            same_pkg = bool(ot and my_dirs & {o.rsplit("/", 1)[0] for o in ot.owns if "/" in o})  # O(k*s*A) re-parses other.owns per pair; hotspot
            if not (adjacent or same_pkg):  # O(1)
                continue  # O(1)
            independent = other not in anc[tid] and tid not in anc[other]  # O(1)
            if independent and tid in cp and other in cp:  # O(1)
                continue  # O(1)
            merged = (c or IMPLEMENTER_PREFIX) + max(0, (ctx[other] or IMPLEMENTER_PREFIX) - IMPLEMENTER_PREFIX)  # O(1)
            if merged > CTX_CEILING:  # O(1)
                continue  # O(1)
            cands.append((0 if adjacent else 1, merged, nkey(other), other))  # O(1)
        cands.sort()  # O(n log n)
        res.append({"task": tid, "reasons": reasons, "merge_with": cands[0][3] if cands else None})  # O(1)
    return res  # O(1)


def cmd_taskgraph(args) -> int:  # O(C+L + parse + n*p + merge) total
    plan = Plan.load(args.plan)  # O(C+L)
    graph = parse_taskgraph(plan)  # O(L*s + R*M + e log n)
    seams = seams_of(graph)  # O(n+e)
    times, _ = effective_times(plan, graph, args)  # O(n*p)
    merges = mergeable_tasks(plan, graph, times, args)  # O(n^2*k*s)
    status = {tid: (plan.by_id[tid].status if tid in plan.by_id else "approved") for tid in graph}  # O(n*k*s)
    kinds = {tid: (plan.by_id[tid].explicit_features.get("kind", "design") if tid in plan.by_id else "design")  # O(n*k*s)
             for tid in graph}  # O(n)
    if args.format == "full":  # O(1)
        print(json.dumps({"graph": graph, "seams": seams, "mergeable": merges, "status": status, "kind": kinds},  # O(n+e) output
                         ensure_ascii=False, indent=2))  # O(1)
    else:  # O(1)
        print(json.dumps(graph, ensure_ascii=False, indent=2))  # O(n+e) output
        for s in seams:  # O(e) iterations
            print(f"seam {s}", file=sys.stderr)  # O(1)
        for tid, st in status.items():  # O(n) iterations
            if st == "draft":  # O(1)
                print(f"status {tid} draft", file=sys.stderr)  # O(1)
        for m in merges:  # O(n) iterations
            to = f"merge with {m['merge_with']}" if m["merge_with"] else "no safe merge partner"  # O(1)
            print(f"warning: mergeable {m['task']} ({'; '.join(m['reasons'])}); {to}", file=sys.stderr)  # O(1)
    return 0  # O(1)


# --------------------------------------------------------------------------
# schedule
# --------------------------------------------------------------------------

def cmd_schedule(args) -> int:  # O(C+L + schedule) total
    plan = Plan.load(args.plan)  # O(1)
    rep = schedule_report(plan, args)  # O(n*(n+e) + n*p)
    if args.insert:  # O(1)
        insert_schedule(plan, rep)  # O(L+C) + file write I/O
    if args.json:  # O(1)
        out = {k: rep[k] for k in ("critical_path", "critical_path_min", "peak_width", "build_min",  # O(1)
                                   "pre_build_min", "build_scale", "post_build_min", "tail_min",  # O(1)
                                   "total_min", "target_min", "target_met", "cap") if k in rep}  # O(1)
        out["tasks"] = {t: {"min": round(rep["times"][t], 3), "start": round(rep["start"][t], 3),  # O(n)
                            "end": round(rep["end"][t], 3), "lane": rep["lane"][t], "source": rep["source"][t]}  # O(1)
                        for t in rep["times"]}  # O(1)
        import shape  # O(1)
        out["shape"] = shape.shape_line(plan, rep["graph"], rep)  # O(e*n*(n + e))
        print(json.dumps(out, ensure_ascii=False, indent=2))  # O(n) output
    else:  # O(1)
        print(summary_line(rep))  # O(path)
        try:  # advisory: chain length, width, removable edges, build with and without them | O(e*n*(n + e))
            import shape  # O(1)
            print(shape.shape_line(plan, rep["graph"], rep))  # O(e*n*(n + e))
        except Exception:  # noqa: BLE001 | O(1)
            pass  # O(1)
        for t in sorted(rep["start"], key=lambda x: (rep["start"][x], nkey(x))):  # O(n log n) sort; O(n) iterations
            print(f"  {t}: lane {rep['lane'][t]}, {rep['start'][t]:.1f} to {rep['end'][t]:.1f} min ({rep['source'][t]})")  # O(1)
        if args.insert:  # O(1)
            print(f"inserted schedule into {args.plan}")  # O(1)
    return 0  # O(1)


# --------------------------------------------------------------------------
# lint
# --------------------------------------------------------------------------

PLACEHOLDERS = [  # O(1)
    (r"\bTBD\b", "TBD"),  # O(1)
    (r"\bTODO\b", "TODO"),  # O(1)
    (r"handle edge cases", "'handle edge cases'"),  # O(1)
    (r"add (?:appropriate )?error handling", "'add error handling'"),  # O(1)
    (r"similar to task\s+\w+", "'similar to Task N'"),  # O(1)
    (r"tests? for the above", "'tests for the above'"),  # O(1)
    (r"\bfill in later\b|\bto be decided\b", "deferred detail"),  # O(1)
]  # O(1)
DIAGRAM_TYPES = ("flowchart", "graph", "sequencediagram", "statediagram", "statediagram-v2", "gantt", "classdiagram",  # O(1)
                 "erdiagram", "journey", "pie", "mindmap", "timeline", "gitgraph", "quadrantchart", "requirementdiagram")  # O(1)
SPEC_SKIP = {"overview", "introduction", "background", "goals", "non-goals", "glossary", "contents",  # O(1)
             "table of contents", "references", "open questions", "appendix", "summary", "status"}  # O(1)


def check_diagram(block: FenceBlock) -> str | None:  # O(B) total, B = block chars
    lines = [ln for ln in block.content if ln.strip() and not ln.strip().startswith("%%")]  # O(B)
    if not lines:  # O(1)
        return "empty mermaid block"  # O(1)
    first = lines[0].strip().split()[0].lower()  # O(s)
    if first not in DIAGRAM_TYPES:  # O(1)
        return f"unknown diagram type {lines[0].strip().split()[0]!r}"  # O(1)
    if first in ("flowchart", "graph"):  # O(1)
        subs = sum(1 for ln in lines if ln.strip().lower().startswith("subgraph"))  # O(B)
        ends = sum(1 for ln in lines if ln.strip().lower() == "end")  # O(B)
        if subs != ends:  # O(1)
            return "unbalanced subgraph/end"  # O(1)
    body = "\n".join(lines)  # O(B)
    body = re.sub(r'"[^"\n]*"', "", body)  # O(B)
    body = re.sub(r"<[^>\n]*>", "", body)  # O(B)
    for o, c in (("[", "]"), ("{", "}")):  # O(1) iterations (2 pairs)
        if body.count(o) != body.count(c):  # O(B)
            return f"unbalanced {o}{c}"  # O(1)
    if first in ("flowchart", "graph") and body.count("(") != body.count(")"):  # O(B)
        return "unbalanced ()"  # O(1)
    return None  # O(1)


def sig_key(sig: dict) -> str:  # O(1)
    return f"{sig['cls']}.{sig['member']}" if sig["cls"] else sig["member"]  # O(1)


def review_focus_cases(plan: Plan) -> dict[str, set[str]]:  # O(L)
    """{package: {cases}} from the plan's Review Focus section lines `- <package>: deep, numeric, ...`."""
    out: dict[str, set[str]] = {}  # O(1)
    inside = False  # O(1)
    for i, ln in enumerate(plan.lines):  # O(L) iterations
        if plan.fenced[i]:  # O(1)
            continue  # O(1)
        if re.match(r"^#{1,6}\s", ln):  # O(s)
            inside = "review focus" in ln.lower()  # O(s)
            continue  # O(1)
        m = re.match(r"^\s*[-*]\s+`?([\w./-]+?)`?\s*:\s*(.+)$", ln) if inside else None  # O(s)
        if m:  # O(1)
            out.setdefault(m.group(1).lower(), set()).update(re.findall(r"[a-z]+", m.group(2).lower()))  # O(s)
    return out  # O(1)


def not_mechanical_reasons(task: Task, focus: dict[str, set[str]]) -> list[str]:  # O(k*s + pkgs)
    feats = task.explicit_features  # O(k*s)
    why: list[str] = []  # O(1)
    if feats.get("loc", 0) > 150:  # O(1)
        why.append(f"LOC owned {feats['loc']} > 150")  # O(1)
    if feats.get("components", 0) > 2:  # O(1)
        why.append(f"Components {feats['components']} > 2")  # O(1)
    if feats.get("tests") == "slow":  # O(1)
        why.append("Tests: slow")  # O(1)
    parts = {seg.lower() for o in task.owns for seg in re.split(r"[/.]", o)}  # O(k*s*A)
    hard = sorted({c for pkg, cases in focus.items() if pkg in parts for c in cases}  # O(pkgs)
                  & {"deep", "numeric", "state", "protocol"})  # O(1)
    if hard:  # O(1)
        why.append(f"Review Focus has {', '.join(hard)}")  # O(1)
    txt = task.text  # O(1)
    named = (re.search(r"\btest_\w+", txt)  # O(len text)
             and re.search(r"==|\bassert\b|\bexpect|->|\breturns?\b", txt, re.I))  # O(len text)
    if not (re.search(r"test code", txt, re.I) or named):  # O(len text)
        why.append("no test code or contract with exact inputs and outputs")  # O(1)
    return why  # O(1)


class Issue(str):  # O(1) a lint message that also records which tasks it is about
    """A str, so callers and tests that print or match messages are unaffected. `tasks` names the
    subject tasks structurally; `scope` is "task" (blocks those tasks) or "plan" (never blocks a
    stable task by itself); `chain` marks a flagged CHAIN, whose member tasks are unstable."""
    tasks: tuple
    scope: str
    chain: bool

    def __new__(cls, msg: str, tasks=(), scope: str = "task", chain: bool = False):  # O(s)
        o = super().__new__(cls, msg)  # O(s)
        o.tasks, o.scope, o.chain = tuple(tasks), scope, chain  # O(t)
        return o  # O(1)


def size_band(coef: dict, args=None) -> tuple[float, float]:  # O(1)
    b = coef.get("size_band") or {}  # O(1)
    low = float(b.get("low_min", DEFAULT_SIZE_BAND["low_min"]))  # O(1)
    high = float(b.get("high_min", DEFAULT_SIZE_BAND["high_min"]))  # O(1)
    override = getattr(args, "max_task_min", None)  # O(1)
    if override is not None:  # O(1)
        high = float(override)  # O(1)
    return low, high  # O(1)


def lint_plan(plan: Plan, args) -> tuple[list[str], list[str]]:  # O(n^2*k*s + e*n + C*P + n*p + merge/simulate) total
    issues: list[str] = []  # O(1)
    warnings: list[str] = []  # O(1)
    ids = [t.id for t in plan.tasks]  # O(n)
    for tid in sorted({i for i in ids if ids.count(i) > 1}, key=nkey):  # O(n^2) ids.count per distinct id; hotspot
        issues.append(Issue(f"duplicate task heading {tid}", [tid]))  # O(1)
    if not plan.tasks:  # O(1)
        issues.append(Issue("no task headings (### Tn ...) found", scope="plan"))  # O(1)

    # placeholders (outside code fences and draft task blocks)
    draft_lines: set[int] = set()  # O(1)
    for t in plan.tasks:  # O(n) iterations
        if t.status == "draft":  # O(k*s)
            draft_lines.update(range(t.line, t.line + t.text.count("\n") + 1))  # O(lines of the block)
    for i, ln in enumerate(plan.lines):  # O(L) iterations
        if plan.fenced[i] or i in draft_lines:  # O(1)
            continue  # O(1)
        for pat, label in PLACEHOLDERS:  # O(P) iterations, P=7 patterns
            if re.search(pat, ln, re.I):  # O(s) regex per pattern
                owner = [t.id for t in plan.tasks if t.line <= i + 1 <= t.line + t.text.count("\n") + 1]  # O(n)
                issues.append(Issue(f"placeholder {label} at line {i + 1}", owner, "task" if owner else "plan"))  # O(1)

    if plan.header_ignore() is None:  # O(L)
        warnings.append("NOTE: no **Ignore:** header line; .gitignore gets only the language defaults "  # O(1)
                        "(list .env, local databases, .next/, out/ and other generated paths your tooling creates)")
    for t in plan.tasks:  # O(n) iterations
        if not t.has_features and t.stated_time is None:  # O(k*s)
            warnings.append(f"WARNING: {t.id} has no features (Components, LOC owned, Tests) and no Est. time; "  # O(1)
                            "the predictor will use defaults")  # O(1)

    # Owns present, parallel tasks do not share files
    graph = None  # O(1)
    try:  # O(1)
        graph = parse_taskgraph(plan)  # O(L*s + R*M + e log n)
    except PlanError as exc:  # O(1)
        issues.append(Issue(f"taskgraph: {exc}", scope="plan"))  # O(1)
    for t in plan.tasks:  # O(n) iterations
        if not t.owns:  # O(k*s*A)
            issues.append(Issue(f"{t.id}: no **Owns:** line", [t.id]))  # O(1)
    if graph:  # O(1)
        anc = ancestors(graph)  # O(e*n)
        for a_i, a in enumerate(plan.tasks):  # O(n) iterations
            for b in plan.tasks[a_i + 1:]:  # O(n) iterations; n^2 pairs total
                if a.id in graph and b.id in graph and a.id not in anc[b.id] and b.id not in anc[a.id]:  # O(1)
                    shared = sorted(set(a.owns) & set(b.owns))  # O(k*s*A) re-parses owns per pair; hotspot
                    if shared:  # O(1)
                        issues.append(Issue(f"{a.id} and {b.id} can run in parallel but both own {', '.join(shared)}", [a.id, b.id]))  # O(1)

    # Consumes / Produces consistency
    produced: dict[str, list[tuple[str, dict]]] = {}  # O(1)
    for t in plan.tasks:  # O(n) iterations
        for item in t.produces:  # O(q) iterations; produces O(k*s)
            sig = parse_signature(item)  # O(s)
            if sig:  # O(1)
                produced.setdefault(sig_key(sig), []).append((t.id, sig))  # O(1)
    for t in plan.tasks:  # O(1)
        for item in t.consumes:  # O(q) iterations
            sig = parse_signature(item)  # O(s)
            if not sig:  # O(1)
                continue  # O(1)
            ref = re.search(REF_TAIL, strip_ticks(item))  # O(s)
            refs = re.findall(TASK_ID, ref.group(1)) if ref else []  # O(s)
            cands = produced.get(sig_key(sig), [])  # O(1)
            if refs:  # O(1)
                cands = [c for c in cands if c[0] in refs]  # O(cands*refs)
            if not cands:  # O(1)
                where = f" from {'/'.join(refs)}" if refs else ""  # O(1)
                issues.append(Issue(f"{t.id} consumes {sig['name']}{where}, but no Produces line defines it", [t.id]))  # O(1)
                continue  # O(1)
            ok = False  # O(1)
            for pid, psig in cands:  # O(cands) iterations
                same_params = param_names(psig["args"]) == param_names(sig["args"])  # O(s)
                same_ret = not (psig["ret"] and sig["ret"]) or re.sub(r"\s+", "", psig["ret"]) == re.sub(r"\s+", "", sig["ret"])  # O(s)
                if same_params and same_ret:  # O(1)
                    ok = True  # O(1)
                    break  # O(1)
            if not ok:  # O(1)
                pid, psig = cands[0]  # O(1)
                issues.append(Issue(f"{t.id} consumes {sig['name']}({sig['args']}) but {pid} produces "  # O(s)
                                    f"{psig['name']}({psig['args']})" + (f" -> {psig['ret']}" if psig["ret"] else ""), [t.id]))  # O(1)

    import shape  # lazy: shape imports this module | O(1)
    issues.extend(shape.example_issues(plan))  # a consumer of structured data without examples is a fix task | O(n*q)
    warnings.extend(shape.split_notes(plan))  # O(n*q)

    # diagrams
    for b in plan.mermaid_blocks():  # O(b) iterations
        err = check_diagram(b)  # O(B)
        if err:  # O(1)
            issues.append(Issue(f"mermaid block at line {b.start + 1}: {err}", scope="plan"))  # O(1)

    # size caps
    max_chars = getattr(args, "max_chars", DEFAULT_MAX_CHARS)  # O(1)
    if len(plan.text) > max_chars:  # O(1)
        issues.append(Issue(f"plan is {len(plan.text)} chars, over the {max_chars} cap", scope="plan"))  # O(1)
    predict = _predict()  # O(1)
    models = predict.load_models(getattr(args, "models", None))  # O(config size) file I/O
    coef = predict.load_coef(getattr(args, "coef", None))  # O(config size) file I/O
    for t in plan.tasks:  # O(n) iterations
        c, src = effective_context(t, models, coef, bool(getattr(args, "stated", False)))  # O(k*s + p)
        if c is not None and c > CTX_CEILING:  # O(1)
            what = "Est. context" if src == "stated" else "predicted context"  # O(1)
            issues.append(Issue(f"{t.id}: {what} {c // 1000}k is over the {CTX_CEILING // 1000}k ceiling; split it", [t.id]))  # O(1)
        if src == "predicted" and t.stated_context:  # O(1)
            if abs(t.stated_context - c) / c > 0.25:  # O(1)
                warnings.append(f"NOTE: {t.id} states Est. context {t.stated_context // 1000}k but the predictor "  # O(s)
                                f"says {c // 1000}k (over 25% apart); the predicted value is used")  # O(1)

    # size band (predicted minutes), CHAIN and the time-target warning
    low_min, high_min = size_band(coef, args)  # O(1)
    miss: str | None = None  # O(1)
    if graph is not None and not find_cycle(graph):  # O(n+e) redundant, parse_taskgraph already checked
        try:  # O(1)
            times, _ = effective_times(plan, graph, args)  # O(n*p) recomputed in schedule_report
        except PlanError as exc:  # O(1)
            issues.append(Issue(str(exc), scope="plan"))  # O(1)
            times = None  # O(1)
        if times is not None:  # O(1)
            for t in sorted(times, key=nkey):  # O(n log n)
                if times[t] > high_min + 1e-9:  # O(1)
                    issues.append(Issue(f"SIZE: {t} predicted {times[t]:.1f} min > {high_min:g} max; split it along its components", [t]))  # O(1)
                elif times[t] < low_min - 1e-9 and t in plan.by_id:  # O(1)
                    warnings.append(f"NOTE: {t} predicted {times[t]:.1f} min < {low_min:g}; consider merging with a related task")  # O(1)
            rep = schedule_report(plan, args)  # O(n*(n+e) + n*p) re-parses graph and times
            cp, ctimes = rep["critical_path"], [rep["times"][t] for t in rep["critical_path"]]  # O(n) draft tasks are not in rep
            work = sum(rep["times"].values())  # total predicted work | O(n)
            base = max(max(rep["times"].values(), default=0.0), work / max(1, rep["peak_width"]))  # longest task or work / width | O(n)
            if len(cp) >= 3 and ctimes and sum(ctimes) > 2.5 * base + 1e-9:  # O(n)
                cid = shape.chain_id(cp, ctimes)  # short hash of the chain's tasks and minutes | O(n)
                rulings = re.findall(r"^.*Ruling:\s*sequential\s*\[(chain-[0-9a-f]+)\]\s*-\s*(.*)$", plan.text, re.M | re.I)  # O(C)
                hit = [r for r in rulings if r[0].lower() == cid]  # a ruling counts only if it carries the id lint printed | O(r)
                if hit:  # O(1)
                    warnings.append(f"NOTE: accepted Ruling: sequential [{cid}] chain {' -> '.join(cp)} {sum(ctimes):.1f} min "  # O(n)
                                    f"vs baseline {base:.1f} min (costs {sum(ctimes) - base:.1f} min): {hit[0][1].strip()}")
                else:  # O(1)
                    ignored = " (a Ruling: sequential without this id was ignored)" if re.search(r"Ruling:\s*sequential", plan.text, re.I) else ""  # O(C)
                    issues.append(Issue(f"CHAIN [{cid}]: critical path {' -> '.join(cp)} is {sum(ctimes):.1f} min, over 2.5x "  # O(n)
                                        f"max(longest task, work / peak width) = {base:.1f} min{ignored}; give producers e.g. examples and "
                                        f"run prune, or add Ruling: sequential [{cid}] - <why>", cp, "plan", chain=True))  # O(1)
            warnings.extend(shape.edge_notes(plan, graph, rep))  # EDGE: removable edges, with the path after each | O(e*n*(n + e))
            if rep["target_min"] is not None and not rep["target_met"]:  # O(1)
                miss = (f"MISSED TARGET: predicted {rep['total_min']:.1f} min vs target {rep['target_min']:.1f} min "  # O(1)
                        f"— critical path {' → '.join(rep['critical_path'])}")  # O(1)
    if miss:  # O(1)
        warnings.append(miss)  # O(1)

    # no Opus implementers (approved tasks only)
    for t in plan.tasks:  # O(n) iterations
        if t.status != "draft" and re.search(r"\bopus\b", t.field_text("model"), re.I):  # O(k*s)
            issues.append(Issue(f"OPUS-TASK: {t.id} uses Model: opus; split it, or leave it on Sonnet and let a fixer-opus "  # O(1)
                                "handle what Sonnet can't", [t.id]))  # O(1)

    # Kind / Model: haiku is for mechanical tasks only (approved tasks only)
    focus = review_focus_cases(plan)  # O(L)
    for t in plan.tasks:  # O(n) iterations
        if t.status == "draft":  # O(k*s)
            continue  # O(1)
        feats = t.explicit_features  # O(k*s)
        kind = feats.get("kind", "design")  # O(1)
        if re.search(r"\bhaiku\b", feats.get("model") or "", re.I) and kind != "mechanical":  # O(1)
            issues.append(Issue(f"KIND: {t.id} uses Model: haiku but is not Kind: mechanical", [t.id]))  # O(1)
        if kind == "mechanical":  # O(1)
            why = not_mechanical_reasons(t, focus)  # O(k*s + pkgs)
            if why:  # O(1)
                issues.append(Issue(f"KIND: {t.id} is not mechanical: {'; '.join(why)}", [t.id]))  # O(1)

    # SPEC coverage
    if getattr(args, "spec", None):  # O(1)
        sp = Path(args.spec)  # O(1)
        if not sp.is_file():  # O(1)
            issues.append(Issue(f"spec not found: {args.spec}", scope="plan"))  # O(1)
        else:  # O(1)
            stext = sp.read_text(encoding="utf-8-sig").replace("\r\n", "\n")  # O(S) file read
            sflags, _ = scan_fences(stext.split("\n"))  # O(S)
            plan_l = plan.text.lower()  # O(C)
            for ln, fenced in zip(stext.split("\n"), sflags):  # O(S) iterations; split repeated
                m = None if fenced else re.match(r"^#{2,3}\s+(.*?)\s*#*$", ln)  # O(s)
                if not m:  # O(1)
                    continue  # O(1)
                raw = m.group(1)  # O(1)
                nm = re.match(r"^(?:§\s*)?(\d+(?:\.\d+)*)\.?\s+(.*)$", raw)  # O(s)
                num, title = (nm.group(1), nm.group(2)) if nm else (None, raw)  # O(1)
                title = title.strip().strip("`")  # O(1)
                if title.lower() in SPEC_SKIP:  # O(1)
                    continue  # O(1)
                covered = title.lower() in plan_l  # O(C) substring search
                if not covered and num:  # O(1)
                    covered = bool(re.search(rf"(?:§|section\s+|sec\.\s*)\s*{re.escape(num)}\b", plan_l))  # O(C) regex
                if not covered:  # O(1)
                    issues.append(Issue(f"SPEC section {raw!r} has no task", scope="plan"))  # O(1)
    return issues, warnings  # O(1)


def sig_text(item: str) -> str:  # O(s)
    sig = parse_signature(item)  # O(s)
    if not sig:  # O(1)
        return re.sub(r"\s+", " ", strip_ticks(item)).strip()  # O(s)
    return f"{sig['name']}({sig['args']})" + (f" -> {sig['ret']}" if sig["ret"] else "")  # O(s)


def task_interface(task: Task) -> dict:  # O(k*s)
    return {"consumes": [sig_text(i) for i in task.consumes], "produces": [sig_text(i) for i in task.produces]}  # O(k*s)


def stable_partition(plan: Plan, issues: list, warnings: list[str]):  # O(n + issues + e*n)
    """STABLE: no task-scoped issue of its own, no edge to an unstable task, not on a flagged CHAIN.
    Issues carry their subject tasks (Issue.tasks); plan-scoped ones never block a stable task."""
    ids = [t.id for t in plan.tasks]  # O(n)
    reasons: dict[str, list[str]] = {i: [] for i in ids}  # O(n)
    plan_issues: list[str] = []  # O(1)
    for iss in list(issues) + [w for w in warnings if w.startswith("MISSED TARGET")]:  # O(issues)
        scope = getattr(iss, "scope", "plan")  # O(1)
        subj = [x for x in getattr(iss, "tasks", ()) if x in reasons]  # O(t)
        if scope == "plan" or not subj:  # O(1)
            plan_issues.append(str(iss))  # O(1)
        if getattr(iss, "chain", False):  # O(1)
            for x in subj:  # O(t)
                reasons[x].append("on flagged CHAIN")  # O(1)
        elif scope == "task":  # O(1)
            for x in subj:  # O(t)
                reasons[x].append(str(iss))  # O(1)
    try:  # O(1)
        graph = parse_taskgraph(plan)  # O(L*s + e log n)
    except PlanError:  # O(1)
        graph = {}  # O(1)
    changed = True  # O(1)
    while changed:  # O(n) rounds worst case
        changed = False  # O(1)
        for t in ids:  # O(n)
            bad = [d for d in graph.get(t, []) if reasons.get(d)]  # O(deg)
            if bad and not any(r.startswith("depends on unstable") for r in reasons[t]):  # O(r)
                reasons[t].append("depends on unstable " + ", ".join(bad))  # O(deg)
                changed = True  # O(1)
    stable = [t for t in ids if not reasons[t]]  # O(n)
    fix = [{"task": t, "reasons": reasons[t]} for t in ids if reasons[t]]  # O(n)
    return stable, fix, plan_issues  # O(1)


def stable_root(plan_path: str) -> Path:  # O(1) directory that holds .superlite/
    root = Path(plan_path).resolve().parent  # O(1)
    if root.name == ".superlite":  # O(1)
        return root.parent  # O(1)
    if root.name == "plans" and root.parent.name == "docs":  # O(1)
        return root.parent.parent  # O(1)
    return root  # O(1)


def dispatched_tasks(sl_dir: Path) -> set[str]:  # O(c + pool)
    out: set[str] = set()  # O(1)
    ledger = sl_dir / "progress.md"  # O(1)
    if ledger.is_file():  # O(1)
        from checkpoint import LEDGER_LINE  # O(1) lazy: the one ledger regex (checkpoint or old commit lines)
        out.update(m.group(1) for ln in ledger.read_text(encoding="utf-8", errors="replace").splitlines() if (m := LEDGER_LINE.match(ln)))  # O(c)
        for line in re.findall(r"^[-*+ ]*unblocked:\s*dispatched\s+((?:T\d+[A-Za-z]?\s*)+)", ledger.read_text(encoding="utf-8", errors="replace"), re.M):  # O(c)
            out.update(line.split())  # O(tasks)
    pool = sl_dir / "pool.json"  # O(1)
    if pool.is_file():  # O(1)
        try:  # O(1)
            for ag in json.loads(pool.read_text(encoding="utf-8")).get("agents", []):  # O(a)
                out.update(x for x in ag.get("tasks") or [] if isinstance(x, str))  # O(tasks)
                cur = ag.get("current") or []  # "Tn", a batch "Tn,Tm", or a list | O(1)
                out.update(x.strip() for x in (cur if isinstance(cur, list) else str(cur).split(",")) if x.strip())  # O(batch)
        except (ValueError, AttributeError):  # O(1)
            pass  # O(1)
    return out  # O(1)


def _iface_str(i: dict) -> str:  # O(s)
    return f"Produces: {', '.join(i.get('produces') or []) or '-'}; Consumes: {', '.join(i.get('consumes') or []) or '-'}"  # O(s)


def block_hash(task: Task) -> str:  # O(block bytes) whitespace-insensitive digest of one task block
    return hashlib.sha1(" ".join(task.text.split()).encode("utf-8")).hexdigest()[:12]  # O(block bytes)


def cmd_lint_stable(args) -> int:  # O(lint + n*k*s)
    bad = _overlay_rc(".")  # a bad superlite.json fails closed | O(file)
    if bad:  # O(1)
        return bad  # O(1)
    import orchestrator  # human mode: no lint --stable (and so no dispatch) before approval | O(1)
    blocked = orchestrator.require(args.plan, "lint --stable")  # O(file)
    if blocked:  # O(1)
        return blocked  # O(1)
    plan = Plan.load(args.plan)  # O(C+L)
    issues, warnings = lint_plan(plan, args)  # O(lint)
    stable, fix, plan_issues = stable_partition(plan, issues, warnings)  # O(n + e*n)
    sl_dir = stable_root(args.plan) / ".superlite"  # O(1)
    out = sl_dir / "stable.json"  # O(1)
    interfaces = {t: task_interface(plan.by_id[t]) for t in stable if t in plan.by_id}  # O(n*k*s)
    old: dict = {}  # O(1)
    if out.is_file():  # O(1)
        try:  # O(1)
            old = json.loads(out.read_text(encoding="utf-8"))  # O(S)
        except ValueError:  # O(1)
            old = {}  # O(1)
    old_if = old.get("interfaces") or {}  # O(1)
    old_blocks = old.get("blocks") or {}  # O(1)
    sent = dispatched_tasks(sl_dir)  # O(c)
    frozen_ids = set(old.get("stable") or []) | sent  # O(c)
    frozen: list[str] = []  # O(1)
    for t in sorted(frozen_ids, key=nkey):  # O(n log n)
        if t in sent and t in old_if and t not in plan.by_id:  # a rewrite dropped a dispatched block | O(1)
            frozen.append(f"FROZEN: {t} dispatched block was removed or renumbered; restore it unchanged")  # O(1)
            continue  # O(1)
        if t not in old_if or t not in plan.by_id:  # O(1)
            continue  # O(1)
        new_i = task_interface(plan.by_id[t])  # O(k*s)
        if t in sent and t in old_blocks and block_hash(plan.by_id[t]) != old_blocks[t] and new_i == old_if[t]:  # O(block)
            frozen.append(f"FROZEN: {t} block changed after dispatch; restore it unchanged (a rewrite must keep dispatched blocks identical)")  # O(1)
            if t not in stable:  # O(n)
                stable.append(t)  # O(1)
                fix = [f for f in fix if f["task"] != t]  # O(n)
            continue  # O(1)
        if new_i != old_if[t]:  # O(1)
            frozen.append(f"FROZEN: {t} interface changed after dispatch: {_iface_str(old_if[t])} -> {_iface_str(new_i)}; add a follow-up fix task")  # O(s)
            if (sl_dir / "briefs" / "wave0.md").is_file():  # Wave 0 already sent: stubs may be stale | O(1)
                print(f"STUB UPDATE: {t}: {_iface_str(old_if[t])} -> {_iface_str(new_i)}")  # O(s)
            interfaces[t] = old_if[t]  # keep the old entry | O(1)
            if t not in stable:  # O(n)
                stable.append(t)  # O(1)
                fix = [f for f in fix if f["task"] != t]  # O(n)
    stable.sort(key=nkey)  # O(n log n)
    blocks = {t: (old_blocks[t] if t in sent and t in old_blocks else block_hash(plan.by_id[t]))  # O(n*block)
              for t in stable if t in plan.by_id}
    pending = [t for t in stable if t not in sent]  # O(n)
    tiers: dict[str, str] = {}  # per-task tier from config/routing.json, computed here once | O(1)
    cp_ids: list[str] = []  # critical path, saved for dispatch's batching (critical-path tasks run alone) | O(1)
    try:  # O(1)
        import routing  # O(1) lazy
        rep = schedule_report(plan, args)  # O(n*(n+e))
        cp_ids = list(rep["critical_path"])  # O(n)
        import history  # O(1)
        stats = history.project_stats(sl_dir)  # O(file)
        gated: list[str] = []  # O(1)
        tiers = {t: v for t, v in routing.compute_tiers(plan, rep["critical_path"], rep["times"], None, stats, gated).items() if t in stable}  # O(n)
        stats_line = f"stats: n={stats.get('rows', 0)}" + (f" (history gate kept {' '.join(sorted(set(gated)))} off haiku)" if gated else "")  # O(n)
        pred = f"predicted: {rep['total_min']:.1f} min"  # O(1)
        try:  # cost from the predictor (history-refit coefficients when enough rows) | O(n)
            import predict  # O(1)
            cost = predict.predict_plan(plan, predict.load_models(), predict.load_coef())["totals"]["cost_usd"]["total"]  # O(n*M)
            pred += f", ${cost:.2f}"  # O(1)
        except Exception:  # noqa: BLE001 | O(1)
            pass  # O(1)
        pred += " (history-backed)" if stats.get("rows", 0) >= 30 else " (shipped coefficients)"  # O(1)
    except Exception as e:  # tiers never block a lint | O(1)
        print(f"routing: tiers not computed ({e})", file=sys.stderr)  # O(1)
    plan_issues = list(plan_issues) + frozen  # O(1)
    print("STABLE: " + " ".join(stable))  # O(n)
    print("FIX:")  # O(1)
    for f in fix:  # O(n)
        print(f"  {f['task']}: " + " | ".join(f["reasons"]))  # O(r)
    for pi in plan_issues:  # O(issues)
        print(f"  PLAN: {pi}")  # O(1)
    for w in warnings:  # O(w)
        if w.startswith(("NOTE: ", "WARNING", "EDGE: ")):  # O(1)
            print(w)  # O(1)
    brief = sl_dir / "briefs" / "wave0.md"  # O(1)
    first = []  # T0 agent line, printed once | O(1)
    if pending and not brief.is_file() and "T0" not in sent:  # O(1)
        try:  # the skeleton brief comes with the first lint: one command, nothing to author | O(1)
            import wave0  # O(1) lazy (wave0 imports this module)
            line, tid, _, _ = wave0.write_brief(args.plan)  # O(L + q*s)
            if tid not in pending and tid not in sent:  # O(n)
                first = [line]  # O(1)
        except Exception as e:  # never blocks the lint | O(1)
            print(f"wave0: brief not written ({e})", file=sys.stderr)  # O(1)
    for line in first:  # O(1)
        print("START FIRST (Wave 0 skeleton, T0): " + line)  # O(1)
    if pending and fix:  # O(1)
        print("SEND BEFORE ANY PLAN EDIT: " + " ".join((["T0"] if first else []) + pending)  # O(n)
              + f" (send them in this turn; only then patch {' '.join(f['task'] for f in fix)})")
    if pending:  # the progress line, ready to run in the same message as the Agent calls | O(n)
        print('THEN LOG (same message): echo "- unblocked: dispatched ' + " ".join((["T0"] if first else []) + pending)
              + ' at $(date +%H:%M:%S); waiting on fixes: ' + (" ".join(f["task"] for f in fix) or "none") + '" >> .superlite/progress.md')
    try:  # run-time control line, only when something changed and metrics exist | O(compute)
        import budget  # O(1)
        budget.maybe_print(args.plan, str(sl_dir))  # O(compute)
    except Exception:  # noqa: BLE001 | O(1)
        pass  # O(1)
    if tiers:  # O(n)
        print(stats_line + "; " + pred)  # O(1)
        print("TIERS: " + " ".join(f"{t}={tiers[t]}" for t in stable if t in tiers))  # O(n)
    print("DISPATCH NOW: " + (" ".join(pending) if pending else "none"))  # O(n)
    accepted = [w[len("NOTE: "):] for w in warnings if w.startswith("NOTE: accepted Ruling: sequential")]  # for finish | O(w)
    (sl_dir / "chain-rulings.json").parent.mkdir(parents=True, exist_ok=True)  # O(1)
    (sl_dir / "chain-rulings.json").write_text(json.dumps(accepted, indent=1), encoding="utf-8")  # O(w)
    out.parent.mkdir(parents=True, exist_ok=True)  # O(1)
    out.write_text(json.dumps({"stable": stable, "fix": fix, "plan_issues": plan_issues, "interfaces": interfaces, "blocks": blocks, "tiers": tiers, "critical_path": cp_ids}, indent=1), encoding="utf-8")  # O(S)
    entry = {"time": datetime.now().isoformat(timespec="seconds"), "stable": len(stable), "fix": len(fix),  # O(n)
             "stable_ids": stable, "newly_stable": [t for t in stable if t not in set(old.get("stable") or [])],
             "dispatched": sorted(sent & set(stable), key=nkey), "pending": pending, "frozen": len(frozen)}
    with (sl_dir / "lint-log.jsonl").open("a", encoding="utf-8") as fh:  # O(1)
        fh.write(json.dumps(entry) + "\n")  # O(n)
    if stable and not frozen:  # the fixed forecast `report` compares against, written once | O(1) after the first
        try:  # O(1)
            import report  # O(1) lazy (report imports this module)
            report.save_projection(args.plan, sl_dir)  # O(n*(n + e)) first time
        except Exception as e:  # never blocks a lint | O(1)
            print(f"report: projection not saved ({e})", file=sys.stderr)  # O(1)
    return 0 if stable and not frozen else 1  # O(1)


def cmd_prune(args) -> int:  # O(prune)
    import shape  # O(1)
    edges = shape.prune(args.plan, None, getattr(args, "dir", None))  # O(e*n*(n + e))
    print("pruned: " + (", ".join(f"{a}->{b}" for a, b in edges) if edges else "no removable edges"))  # O(e)
    return 0  # O(1)


def cmd_lint(args) -> int:  # O(lint) total
    if getattr(args, "stable", False):
        return cmd_lint_stable(args)
    bad = _overlay_rc(".")  # a bad superlite.json fails closed | O(file)
    if bad:  # O(1)
        return bad  # O(1)
    plan = Plan.load(args.plan)  # O(C+L)
    issues, warnings = lint_plan(plan, args)  # O(lint)
    for w in warnings:  # O(w) iterations
        print(w)  # O(1)
    for i in issues:  # O(issues) iterations
        print(f"ISSUE: {i}")  # O(1)
    if issues:  # O(1)
        print(f"lint: {len(issues)} issue(s)")  # O(1)
        return 1  # O(1)
    print("lint: ok")  # O(1)
    return 0  # O(1)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def add_predictor_args(p):  # O(1)
    p.add_argument("--models", help="models.json (default: config/models.json)")  # O(1)
    p.add_argument("--coef", help="predictor.json (default: config/predictor.json)")  # O(1)
    p.add_argument("--stated", action="store_true",  # O(1)
                   help="use stated Est. time lines even where features exist (default: features win)")  # O(1)


def build_parser():  # O(1) total
    ap = argparse.ArgumentParser(prog="plan_tools.py", description=__doc__.splitlines()[0])  # O(1)
    sub = ap.add_subparsers(dest="cmd", required=True)  # O(1)

    p = sub.add_parser("taskgraph", help="print task -> dependencies JSON; seams and mergeable warnings on stderr")  # O(1)
    p.add_argument("--plan", required=True)  # O(1)
    p.add_argument("--format", choices=("graph", "full"), default="graph",  # O(1)
                   help="full: {graph, seams, mergeable, status, kind} on stdout")  # O(1)
    add_predictor_args(p)  # O(1)
    p.set_defaults(func=cmd_taskgraph)  # O(1)

    p = sub.add_parser("stubs", help="write interface stubs from Produces lines")  # O(1)
    p.add_argument("--plan", required=True)  # O(1)
    p.add_argument("--root", default=".")  # O(1)
    p.add_argument("--dry-run", action="store_true")  # O(1)
    p.set_defaults(func=cmd_stubs)  # O(1)

    p = sub.add_parser("schedule", help="simulate the ready queue; optionally insert a Gantt into the plan")  # O(1)
    p.add_argument("--plan", required=True)  # O(1)
    p.add_argument("--cap", type=int, default=DEFAULT_CAP)  # O(1)
    p.add_argument("--target-min", type=float, default=None)  # O(1)
    p.add_argument("--tail-min", type=float, default=None, help="minutes after the build (default from predictor.json)")  # O(1)
    p.add_argument("--insert", action="store_true")  # O(1)
    p.add_argument("--json", action="store_true")  # O(1)
    add_predictor_args(p)  # O(1)
    p.set_defaults(func=cmd_schedule)  # O(1)

    p = sub.add_parser("prune", help="drop taskgraph edges whose interfaces all have e.g. examples (consumer not dispatched), then schedule --insert")  # O(1)
    p.add_argument("--plan", required=True)  # O(1)
    p.add_argument("--dir", help=".superlite folder (default: next to the plan's root)")  # O(1)
    p.set_defaults(func=cmd_prune)  # O(1)

    p = sub.add_parser("lint", help="check the plan; MISSED TARGET is a warning (exit 0)")  # O(1)
    p.add_argument("--plan", required=True)  # O(1)
    p.add_argument("--spec")  # O(1)
    p.add_argument("--cap", type=int, default=DEFAULT_CAP)  # O(1)
    p.add_argument("--target-min", type=float, default=None)  # O(1)
    p.add_argument("--tail-min", type=float, default=None)  # O(1)
    p.add_argument("--max-task-min", type=float, default=None, help="override size_band.high_min for this run")  # O(1)
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)  # O(1)
    p.add_argument("--stable", action="store_true", help="partition tasks into STABLE and FIX; writes .superlite/stable.json")
    add_predictor_args(p)  # O(1)
    p.set_defaults(func=cmd_lint)  # O(1)

    for name in REVIEW_COMMANDS:  # O(1)
        sub.add_parser(name, help="implemented in pt_review.py")  # O(1)
    return ap  # O(1)


def _utf8_streams():  # O(1)
    for s in (sys.stdout, sys.stderr):  # O(1)
        if hasattr(s, "reconfigure"):  # O(1)
            try:  # O(1)
                s.reconfigure(encoding="utf-8")  # O(1)
            except Exception:  # noqa: BLE001 | O(1)
                pass  # O(1)


def main(argv=None):  # O(1) + dispatch
    """Dispatch. adapter/packet/contract go to pt_review.main(argv) with the subcommand name kept in argv."""  # O(1)
    argv = list(sys.argv[1:] if argv is None else argv)  # O(1)
    _utf8_streams()  # O(1)
    if argv and argv[0] in REVIEW_COMMANDS:  # O(1)
        # pt_review.main receives the FULL argv, including the subcommand name as argv[0].
        if str(SCRIPT_DIR) not in sys.path:  # O(1)
            sys.path.insert(0, str(SCRIPT_DIR))  # O(1)
        import pt_review  # O(1)
        return pt_review.main(argv)  # O(1)
    args = build_parser().parse_args(argv)  # O(1)
    try:  # O(1)
        return args.func(args)  # O(1)
    except PlanError as exc:  # O(1)
        print(f"error: {exc}", file=sys.stderr)  # O(1)
        return exc.code  # O(1)


if __name__ == "__main__":  # O(1)
    sys.exit(main())  # O(1)
