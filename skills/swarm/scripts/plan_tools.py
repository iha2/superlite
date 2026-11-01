#!/usr/bin/env python3
"""plan_tools: mechanical tooling for superlite plans.

This module is the entry and the plan model (Plan, Task, fences). The subcommands live in
plan_schedule.py (taskgraph, schedule), plan_lint.py (lint, lint --stable), plan_stubs.py (stubs and
signatures) and pt_review.py (adapter, packet, contract; imported on demand). Every name those modules
define is also reachable here (pt.simulate, pt.parse_signature, ...), imported on first use.

The plan file is read, never rewritten.
Exit codes: 0 ok, 1 findings (stubs import failure, lint issues), 2 bad plan or usage.
Stdlib only.
"""
from __future__ import annotations

import argparse
import importlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if __name__ == "__main__":  # one module object, so PlanError is one class for every importer
    sys.modules.setdefault("plan_tools", sys.modules["__main__"])

import planfile  # noqa: E402
from planfile import REF_TAIL, TASK_ID  # noqa: E402,F401

CONFIG_DIR = SCRIPT_DIR.parent / "config"
REVIEW_COMMANDS = ("adapter", "packet", "contract")
DEFAULT_CAP = 10
DEFAULT_MAX_CHARS = 15000

TASK_ID_RE = re.compile(rf"^{TASK_ID}$")
HEADING_RE = planfile.HEADING
ANY_HEADING_RE = planfile.ANY_HEADING
FIELD_RE = planfile.FIELD
EG_RE = planfile.EG
FENCE_RE = planfile.FENCE
strip_ticks = planfile._ticks
split_top_level = planfile._split_top
expand_braces = planfile._braces

# name -> module that defines it; looked up on first use so no module imports another at load time
_MOVED = {
    "plan_schedule": ("find_taskgraph_blocks parse_taskgraph find_cycle ancestors seams_of waited_graph unwaited_edges "
                      "effective_times effective_context simulate schedule_report summary_line mergeable_tasks "
                      "cmd_taskgraph cmd_schedule chain_id edge_notes shape_line example_issues split_notes stable_partition dispatched_tasks block_hash task_interface sig_text IMPLEMENTER_PREFIX CTX_CEILING "
                      "MERGE_MIN_VARIABLE MERGE_MIN_MINUTES DEFAULT_SIZE_BAND"),
    "plan_stubs": ("parse_signature param_names sig_key resolve_module build_stub_defs render_module import_check "
                   "cmd_stubs draft_ids"),
    "plan_lint": ("Issue lint_plan check_diagram review_focus_cases not_mechanical_reasons size_band "
                  "cmd_lint cmd_lint_stable "
                  "PLACEHOLDERS"),
}
_HOME = {name: mod for mod, names in _MOVED.items() for name in names.split()}


def __getattr__(name: str):
    mod = _HOME.get(name)
    if mod is None:
        raise AttributeError(f"module 'plan_tools' has no attribute {name!r}")
    return getattr(importlib.import_module(mod), name)


class PlanError(planfile.PlanError):
    def __init__(self, message: str, code: int = 2):
        super().__init__(message)
        self.code = code


def nkey(tid: str):
    m = re.match(r"T(\d+)([a-z]*)$", tid)
    return (int(m.group(1)), m.group(2)) if m else (10**9, tid)


def stable_root(plan_path: str) -> Path:
    """The project root a plan belongs to: the folder above docs/plans, else the plan's own folder."""
    import state
    root = Path(plan_path).resolve().parent
    if root.name == state._DIR:  # a plan kept inside the run-state folder
        return root.parent
    if root.name == "plans" and root.parent.name == "docs":
        return root.parent.parent
    return root


def overlay_rc(root) -> int | None:
    """2 when superlite.json is present and unusable (the reason goes to stderr); None when it is missing or valid."""
    import project_config
    try:
        project_config.load(Path(root).resolve())
    except project_config.ProjectConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return None


def run_dir(plan_path: str) -> Path:
    """The run-state folder of the plan's project."""
    import state
    return state.run_dir(stable_root(plan_path))


@dataclass
class FenceBlock:
    start: int  # index of opening fence line
    end: int  # index of closing fence line (or last line if unterminated)
    info: str
    content: list[str]


def scan_fences(lines: list[str]):  # O(L+C) total
    """Return (fenced flags per line, top-level fence blocks)."""
    flags: list[bool] = []
    blocks: list[FenceBlock] = []
    opened = None  # (char, length, info, start)
    for i, ln in enumerate(lines):
        m = FENCE_RE.match(ln)
        if opened is None:
            if m:
                opened = (m.group(1)[0], len(m.group(1)), m.group(2).strip(), i)
            flags.append(bool(m))
            continue
        flags.append(True)
        if m and m.group(1)[0] == opened[0] and len(m.group(1)) >= opened[1] and not m.group(2).strip():
            blocks.append(FenceBlock(opened[3], i, opened[2], lines[opened[3] + 1:i]))
            opened = None
    if opened is not None:
        blocks.append(FenceBlock(opened[3], len(lines) - 1, opened[2], lines[opened[3] + 1:]))
    return flags, blocks


@dataclass
class Task(planfile.Task):
    heading: str = ""
    line: int = 0
    text: str = ""  # whole section text
    synthetic: bool = False

    @property
    def status(self) -> str:  # O(k*s)
        """`draft` when the block has `**Status:** draft` (any case); anything else is `approved`."""
        return "draft" if self.field_text("status").strip().lower() == "draft" else "approved"

    def field_text(self, name: str) -> str:  # O(k*s)
        return " ".join(x for x in self.fields.get(name, []) if x).strip()

    @property
    def owned_sources(self) -> list[str]:  # O(k*s*A + o*s) total
        res = []
        for p in self.owns:
            q = p.replace("\\", "/")
            name = q.rsplit("/", 1)[-1]
            if q.endswith(".py") and not q.startswith("tests/") and "/tests/" not in q and not name.startswith("test_"):
                res.append(q)
        return res

    @property
    def contract_text(self) -> str:  # O(k*s)
        return self.field_text("contract") or self.field_text("notes")

    @property
    def stated_time(self) -> float | None:  # O(k*s)
        m = re.search(r"Est\.\s*time[:\s]+~?\s*(\d+(?:\.\d+)?)\s*min", self.heading)
        if m:
            return float(m.group(1))
        m = re.match(r"~?\s*(\d+(?:\.\d+)?)\s*min", self.field_text("est. time"))
        return float(m.group(1)) if m else None

    @property
    def stated_context(self) -> int | None:  # O(k*s)
        m = re.search(r"Est\.\s*context[:\s]+~?\s*(\d+(?:\.\d+)?)\s*k", self.heading)
        if not m:
            m = re.match(r"~?\s*(\d+(?:\.\d+)?)\s*k", self.field_text("est. context"))
        return int(round(float(m.group(1)) * 1000)) if m else None

    @property
    def complexity(self) -> int | None:
        m = re.search(r"Complexity\s+(\d+)", self.heading)
        return int(m.group(1)) if m else None

    @property
    def features(self) -> dict:  # O(k*s)
        """Measurable features incl. the Est. time parenthetical as a fallback."""
        return self._features(True)

    @property
    def explicit_features(self) -> dict:  # O(k*s)
        """Only what the plan states as fields: **Features:** or **Components/LOC owned/Tests/Model/Kind:**."""
        return self._features(False)

    @property
    def has_features(self) -> bool:  # O(k*s)
        """True when the coordinator wrote features; then the predictor's value wins over Est. time."""
        return bool(set(self.explicit_features) & {"components", "loc", "tests"})

    def _features(self, parenthetical: bool) -> dict:  # O(k*s) total
        f: dict = {}
        _take_features(f, self.field_text("features"))
        km = re.search(r"\b(mechanical|design)\b", self.field_text("kind"), re.I)
        if km:
            f["kind"] = km.group(1).lower()
        for name, key in (("components", "components"), ("loc owned", "loc"), ("loc", "loc"),
                          ("model", "model"), ("tests", "tests"), ("test speed", "tests")):
            val = self.field_text(name)
            if val and key not in f:
                _take_features(f, f"{key}={val}")
        if parenthetical and ("loc" not in f or "components" not in f or "tests" not in f):
            m = re.search(r"\(([^)]*)\)", self.field_text("est. time"))
            if m:
                _take_features(f, m.group(1))
        return f


_NUMERIC_FEATURES = (
    ("components", r"components?\s*[=:]?\s*(\d+)|(\d+)\s+components?"),
    ("loc", r"\bloc(?:\s+owned)?\s*[=:]?\s*~?(\d+)|(?:about\s+|~)?(\d+)\s*loc\b"),
    ("docs_chars", r"docs?(?:\s+chars)?\s*[=:]\s*(\d+)"),
    ("reads_loc", r"reads?(?:\s+loc)?\s*[=:]\s*(\d+)"),
)


def _take_features(f: dict, text: str) -> None:  # O(s)
    """Fill f with the numeric, tests and model features that text states and f does not have yet."""
    for key, pat in _NUMERIC_FEATURES:
        m = None if key in f else re.search(pat, text, re.I)
        if m:
            f[key] = int(next(g for g in m.groups() if g))
    if "tests" not in f:
        m = re.search(r"tests?\s*[=:]\s*(fast|slow)|\b(fast|slow)\s+tests?\b", text, re.I)
        if m:
            f["tests"] = (m.group(1) or m.group(2)).lower()
    if "model" not in f:
        m = re.search(r"model\s*[=:]\s*([A-Za-z][\w.\-]*)", text, re.I)
        if m:
            f["model"] = m.group(1).lower()


class Plan:
    def __init__(self, text: str, path: Path | None = None):  # O(C+L) total
        self.path = path
        self.text = text.replace("\r\n", "\n")
        self.lines = self.text.split("\n")
        self.fenced, self.blocks = scan_fences(self.lines)
        self.tasks: list[Task] = self._parse_tasks()
        self.by_id = {t.id: t for t in self.tasks}
        self._base: planfile.Plan | None = None

    @classmethod
    def load(cls, path: str) -> "Plan":  # O(C+L) total + file I/O
        p = Path(path)
        if not p.is_file():
            raise PlanError(f"plan not found: {path}")
        return cls(p.read_bytes().decode("utf-8-sig"), p)

    @property
    def base(self) -> planfile.Plan:
        """The shared plan reader's view of the same text: the task graph edges and which are waited on."""
        if self._base is None:
            self._base = planfile.Plan(self.text, self.path)
        return self._base

    def waited_edges(self) -> list[tuple[str, str]]:
        return self.base.waited_edges()

    def _parse_tasks(self) -> list[Task]:  # O(C+L) total
        tasks: list[Task] = []
        cur: Task | None = None
        cur_field: str | None = None
        for i, ln in enumerate(self.lines):
            fenced = self.fenced[i]
            if not fenced and ANY_HEADING_RE.match(ln):
                cur_field = None
                cur = self._open_task(tasks, ln, i)
                continue
            if cur is None:
                continue
            cur.text += "\n" + ln
            if not fenced:
                cur_field = self._field_line(cur, cur_field, ln)
        return tasks

    @staticmethod
    def _open_task(tasks: list, ln: str, i: int) -> Task | None:
        """A task heading starts a new task; any other heading ends the current one (None)."""
        m = HEADING_RE.match(ln)
        if not m:
            return None
        title = m.group(2).strip().lstrip("·").strip()
        title = re.split(r"\s+·\s+|\s{2,}", title)[0].strip()
        task = Task(id=m.group(1), title=title, heading=ln, line=i, text=ln)
        tasks.append(task)
        return task

    @staticmethod
    def _field_line(cur: Task, cur_field: str | None, ln: str) -> str | None:
        """Record ln on cur; return the field a following continuation line belongs to."""
        em = EG_RE.match(ln)
        if em:  # an example line stays with its task and keeps its own field
            cur.fields.setdefault("e.g.", []).append(em.group(1).strip())
            return "e.g."
        fm = FIELD_RE.match(ln.strip())
        if fm:
            name = fm.group(1).strip().lower()
            cur.fields.setdefault(name, []).append(fm.group(2).strip())
            return name
        if cur_field and (re.match(r"^\s*[-*]\s+", ln) or (ln.strip() and ln.startswith((" ", "\t")))):
            cur.fields[cur_field].append(ln.strip())
            return cur_field
        return None

    def mermaid_blocks(self) -> list[FenceBlock]:
        return [b for b in self.blocks if b.info.lower().startswith("mermaid")]

    def header_target(self) -> float | None:  # O(C)
        m = re.search(r"^\*\*Target(?:[-\s]*(?:min|minutes|time))?:\*\*\s*~?\s*(\d+(?:\.\d+)?)", self.text, re.M)
        return float(m.group(1)) if m else None

    def header_ignore(self) -> list[str] | None:  # O(L)
        """Entries of the `**Ignore:** a/, b, *.c` header line (comma-separated paths or globs); None without the line."""
        for ln, f in zip(self.lines, self.fenced):
            m = None if f else re.match(r"^\*\*Ignore:\*\*\s*(.*)$", ln)
            if m:
                return [e.strip().strip("`").strip() for e in m.group(1).split(",") if e.strip().strip("`").strip()]
        return None

    def header_execution(self) -> dict:  # O(L)
        """Parse `**Execution:** host=cursor, parallel=multitask, swarm_by=tier` (empty dict if absent)."""
        import host_models as hm
        return hm.parse_execution(self.non_fenced_text())

    def non_fenced_text(self) -> str:  # O(L)
        return "\n".join(ln for ln, f in zip(self.lines, self.fenced) if not f)


def add_predictor_args(p):
    p.add_argument("--models", help="models.json (default: config/models.json)")
    p.add_argument("--coef", help="predictor.json (default: config/predictor.json)")
    p.add_argument("--stated", action="store_true",
                   help="use stated Est. time lines even where features exist (default: features win)")


def build_parser():
    ap = argparse.ArgumentParser(prog="plan_tools.py", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("taskgraph", help="print task -> dependencies JSON; seams and mergeable warnings on stderr")
    p.add_argument("--plan", required=True)
    p.add_argument("--format", choices=("graph", "full"), default="graph",
                   help="full: {graph, seams, mergeable, status, kind} on stdout")
    add_predictor_args(p)
    p.set_defaults(func="cmd_taskgraph")

    p = sub.add_parser("stubs", help="write interface stubs from Produces lines")
    p.add_argument("--plan", required=True)
    p.add_argument("--root", default=".")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func="cmd_stubs")

    p = sub.add_parser("schedule", help="simulate the ready queue and print the critical path")
    p.add_argument("--plan", required=True)
    p.add_argument("--cap", type=int, default=DEFAULT_CAP)
    p.add_argument("--target-min", type=float, default=None)
    p.add_argument("--tail-min", type=float, default=None, help="minutes after the build (default from predictor.json)")
    p.add_argument("--json", action="store_true")
    add_predictor_args(p)
    p.set_defaults(func="cmd_schedule")

    p = sub.add_parser("lint", help="check the plan; MISSED TARGET is a warning (exit 0)")
    p.add_argument("--plan", required=True)
    p.add_argument("--spec")
    p.add_argument("--cap", type=int, default=DEFAULT_CAP)
    p.add_argument("--target-min", type=float, default=None)
    p.add_argument("--tail-min", type=float, default=None)
    p.add_argument("--max-task-min", type=float, default=None, help="override size_band.high_min for this run")
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    p.add_argument("--stable", action="store_true", help="partition tasks into STABLE and FIX; writes stable.json")
    add_predictor_args(p)
    p.set_defaults(func="cmd_lint")

    for name in REVIEW_COMMANDS:
        sub.add_parser(name, help="implemented in pt_review.py")
    return ap


def _utf8_streams():
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            try:
                s.reconfigure(encoding="utf-8")
            except (OSError, ValueError) as exc:
                print(f"plan_tools: stream stays as is ({exc})", file=sys.stderr)


def main(argv=None):
    """Dispatch. adapter/packet/contract go to pt_review.main(argv) with the subcommand name kept in argv."""
    argv = list(sys.argv[1:] if argv is None else argv)
    _utf8_streams()
    if argv and argv[0] in REVIEW_COMMANDS:
        import pt_review  # pt_review.main receives the FULL argv, subcommand name included
        return pt_review.main(argv)
    args = build_parser().parse_args(argv)
    try:
        return __getattr__(args.func)(args)
    except PlanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code
    except planfile.PlanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
