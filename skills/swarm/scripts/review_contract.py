"""Seam checks that read the plan against the real code: Produces signatures and the data contract.

Plan conventions read here (per task section `### Tn ...`):
  **Owns:** a.py, tests/test_a.py          **Consumes:** f(x) -> y (T1)
  **Produces:** pkg.mod.f(a: int, b=1) -> str  ·  Class.method(ref: str) -> list[str]
      A dotted name with no Python module resolves to a TypeScript file (`model.parseRegistry` ->
      hooks/model.ts, the task's Owns first): there only the export itself is checked.
  Data contract lines (any line, anchored at the start, bullets allowed):
      POST /api/cells {"ref":"A1"} -> 200 {cells: dict, note?: str}
  Task graph: a `%% taskgraph` block of `Tn --> Tm` edges (Tn produces for Tm).
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import planfile  # noqa: E402
import review_ts  # noqa: E402

HTTP_LINE_RE = re.compile(r"^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/\S*)\s*(.*)$")
TEST_PATH_RE = re.compile(r"(^|/)(tests?/|test_[^/]*$|[^/]*_test\.py$)")
CONSUMER_EXT = {".html", ".htm", ".js", ".mjs", ".ts", ".jsx", ".tsx", ".vue", ".py"}


class ToolError(Exception):
    """A usage or input error; main() prints it and returns 2."""


# --------------------------------------------------------------------------- plan layout


class Layout:
    """Headings and fenced lines of a plan, so sections can be sliced by line range."""

    def __init__(self, lines: list[str]):  # O(L*m + H^2) total
        self.lines = lines
        self.fenced: set[int] = set()
        heads: list[tuple[int, int, str]] = []
        fence = None
        for i, line in enumerate(lines):
            fence = self._step(i, line, fence, heads)
        self.heads = [(i, lvl, title, self._end(heads, n)) for n, (i, lvl, title) in enumerate(heads)]

    def _step(self, i: int, line: str, fence, heads: list):
        m = planfile.FENCE.match(line)
        if fence is not None:
            self.fenced.add(i)
            closes = m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1] and not m.group(2).strip()
            return None if closes else fence
        if m:
            self.fenced.add(i)
            return (m.group(1)[0], len(m.group(1)))
        hm = re.match(r"^(#{1,6})\s+(.*)$", line)
        if hm:
            heads.append((i, len(hm.group(1)), hm.group(2).strip()))
        return None

    def _end(self, heads: list, n: int) -> int:  # O(H) total
        lvl = heads[n][1]
        return next((j for j, lvl2, _t in heads[n + 1:] if lvl2 <= lvl), len(self.lines))

    def task_range(self, tid: str) -> tuple[int, int] | None:
        for i, _lvl, _title, end in self.heads:
            m = planfile.HEADING.match(self.lines[i])
            if m and m.group(1) == tid:
                return i, end
        return None


def layout(plan: planfile.Plan) -> Layout:
    """The plan's Layout, built once and kept on the plan."""
    if not hasattr(plan, "layout"):
        plan.layout = Layout(plan.lines)
    return plan.layout


def task_start(plan: planfile.Plan, tid: str) -> int:
    rng = layout(plan).task_range(tid)
    return rng[0] if rng else 0


def review_focus(plan: planfile.Plan) -> str:
    lay = layout(plan)
    for idx, _lvl, title, end in lay.heads:
        if "review focus" in title.lower():
            return "\n".join(plan.lines[idx + 1:end]).strip()
    return ""


def probe_entries(plan: planfile.Plan) -> list[str]:
    """The text of every `**Probe entry:**` line outside code fences."""
    lay = layout(plan)
    out = []
    for i, line in enumerate(plan.lines):
        m = re.match(r"^\s*(?:[-*]\s+)?\*\*Probe entry:?\*\*:?\s*(.*)$", line, re.I)
        if m and i not in lay.fenced and m.group(1).strip():
            out.append(m.group(1).strip().strip("`").strip())
    return out


def owned_files(task: planfile.Task) -> list[str]:
    """Owns entries that look like file paths."""
    out = []
    for tok in task.owns:
        for part in tok.split("·"):
            part = re.sub(r"\s*\(.*\)\s*$", "", part.strip().strip("`").strip())
            if part and " " not in part and ("." in part or "/" in part):
                out.append(part)
    return out


def consumes_ids(task: planfile.Task) -> set[str]:
    return set(re.findall(r"\((T\d+[a-z]?)\)", " ".join(task.consumes)))


# --------------------------------------------------------------------------- Produces signatures


@dataclass
class Planned:
    task: str
    raw: str
    qual: str
    args_src: str | None  # None => "(...)": params unchecked
    ret: str | None
    plan_line: int = 0
    ts_only: bool = False  # not a Python signature (`lines: string[]`): checked only when it resolves to TypeScript

    @property
    def parts(self) -> list[str]:  # O(m) total
        return self.qual.split(".")


def parse_sig(item: str) -> tuple[str, str | None, str | None] | None:  # O(m) total
    item = item.strip().strip("`").strip()
    m = re.match(r"^([A-Za-z_][\w.]*)\s*\(", item)
    if not m:
        return None
    depth, end = 0, -1
    for j in range(m.end() - 1, len(item)):
        c = item[j]
        depth += (c in "([{") - (c in ")]}")
        if depth == 0:
            end = j
            break
    if end < 0:
        return None
    args = item[m.end():end].strip()
    rest = item[end + 1:].strip()
    ret = _parse_ret(rest)
    if ret is False:
        return None
    if args in ("...", "…"):
        return m.group(1), None, ret
    try:
        ast.parse(f"def _f({args}): pass")
    except SyntaxError:
        return None
    return m.group(1), args, ret


def _parse_ret(rest: str):
    """The return annotation after `->`, None when absent or unparsable, False when `rest` is not `-> x`."""
    if not rest:
        return None
    if not rest.startswith("->"):
        return False
    ret = rest[2:].strip().strip("`").strip()
    try:
        ast.parse(ret, mode="eval")
    except SyntaxError:
        return None
    return ret


def planned_items(plan: planfile.Plan, task_ids: list[str] | None = None) -> list[Planned]:  # O(n*t + P*m) total
    out: list[Planned] = []
    for tid, t in plan.tasks.items():
        if task_ids is not None and tid not in task_ids:
            continue
        for item in t.produces:
            parsed = parse_sig(item)
            name = re.match(r"^([A-Za-z_]\w*(?:\.\w+)+)\s*\(", item.strip().strip("`").strip())
            if parsed:
                out.append(Planned(tid, item, parsed[0], parsed[1], parsed[2], task_start(plan, tid) + 1))
            elif name:  # TypeScript syntax the Python parser refuses: keep the name for the export check
                out.append(Planned(tid, item, name.group(1), None, None, task_start(plan, tid) + 1, ts_only=True))
    return out


def _params_from_src(args_src: str) -> list[dict]:  # O(m + p) total
    fn = ast.parse(f"def _f({args_src}): pass").body[0]
    a = fn.args  # type: ignore[attr-defined]
    out: list[dict] = []

    def add(arg: ast.arg, kind: str, default: ast.expr | None) -> None:  # O(m) per param
        out.append({"name": arg.arg, "kind": kind,
                    "default": ast.unparse(default) if default is not None else None,
                    "ann": ast.unparse(arg.annotation) if arg.annotation else None})

    pos = list(a.posonlyargs) + list(a.args)
    defaults = [None] * (len(pos) - len(a.defaults)) + list(a.defaults)
    for n, (arg, d) in enumerate(zip(pos, defaults)):
        add(arg, "POSITIONAL_ONLY" if n < len(a.posonlyargs) else "POSITIONAL_OR_KEYWORD", d)
    if a.vararg:
        add(a.vararg, "VAR_POSITIONAL", None)
    for arg, d in zip(a.kwonlyargs, a.kw_defaults):
        add(arg, "KEYWORD_ONLY", d)
    if a.kwarg:
        add(a.kwarg, "VAR_KEYWORD", None)
    return out


class _Norm(ast.NodeTransformer):
    def visit_Attribute(self, node: ast.Attribute):  # typing.List -> List, http.server.X -> X
        return ast.Name(id=node.attr, ctx=ast.Load())

    def visit_Constant(self, node: ast.Constant):  # forward-ref strings
        if not isinstance(node.value, str):
            return node
        try:
            return self.visit(ast.parse(node.value, mode="eval").body)
        except SyntaxError:
            return node


def norm_ann(s: str | None) -> str | None:  # O(m) total
    if s is None:
        return None
    try:
        return ast.unparse(_Norm().visit(ast.parse(s, mode="eval")))
    except SyntaxError:
        return s.strip()


def _same_default(planned: str, real_repr: str | None) -> bool:  # O(m) total
    try:
        return repr(ast.literal_eval(planned)) == real_repr
    except (ValueError, SyntaxError):
        return planned.strip() == (real_repr or "").strip()


def _param_msgs(qual: str, w: dict, h: dict) -> list[str]:  # O(m) total
    msgs = []
    if w["kind"] != h["kind"]:
        msgs.append(f"{qual}: parameter '{w['name']}' is {h['kind'].lower()}, planned {w['kind'].lower()}")
    if w["default"] is not None and not h["has_default"]:
        msgs.append(f"{qual}: parameter '{w['name']}' lost its default {w['default']}")
    elif w["default"] is not None and not _same_default(w["default"], h["default"]):
        msgs.append(f"{qual}: parameter '{w['name']}' default is {h['default']}, planned {w['default']}")
    wa, ha = norm_ann(w["ann"]), norm_ann(h["ann"])
    if wa and ha and wa != ha:
        msgs.append(f"{qual}: parameter '{w['name']}' annotated {ha}, planned {wa}")
    return msgs


def _names_msgs(p: Planned, want: list[dict], have: list[dict]) -> list[str]:  # O(p^2) total
    wn, hn = [x["name"] for x in want], [x["name"] for x in have]
    msgs = [f"{p.qual}: missing parameter '{n}' (planned {p.raw})" for n in wn if n not in hn]
    msgs += [f"{p.qual}: extra parameter '{n}' not in the plan" for n in hn if n not in wn]
    if not msgs and wn != hn:
        msgs.append(f"{p.qual}: parameter order differs, planned ({', '.join(wn)}) real ({', '.join(hn)})")
    return msgs


def compare_sig(p: Planned, real: dict) -> list[str]:  # O(p^2 + p*m) total
    """Differences between a planned signature and the real one (empty = match)."""
    if not real.get("ok"):
        return [f"{p.qual}: {real.get('error', 'cannot read the real signature')}"]
    msgs: list[str] = []
    if p.args_src is not None:
        want, have = _params_from_src(p.args_src), real["params"]
        msgs = _names_msgs(p, want, have)
        if not msgs:
            for w, h in zip(want, have):
                msgs += _param_msgs(p.qual, w, h)
    wr, hr = norm_ann(p.ret), norm_ann(real.get("returns"))
    if wr and hr and wr != hr:
        msgs.append(f"{p.qual}: returns {hr}, planned {wr}")
    return msgs


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
'''


def project_env(root: Path, **extra: str) -> dict[str, str]:  # O(env vars) total
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(root / "src"), str(root)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(extra)
    return env


def module_name(root: Path, file: Path) -> str:  # O(d) path syscalls total
    file = file.resolve()
    for base in (root / "src", root):
        try:
            rel = file.relative_to(base.resolve())
        except ValueError:
            continue
        parts = list(rel.with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts)
    return file.stem


def find_module_file(root: Path, dotted: str) -> Path | None:  # O(d) stat calls total (4 is_file)
    rel = dotted.replace(".", "/")
    for base in (root / "src", root):
        for cand in (base / f"{rel}.py", base / rel / "__init__.py"):
            if cand.is_file():
                return cand
    return None


def _top_level_names(path: Path) -> set[str]:  # O(B) read + parse total
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return set()
    names: set[str] = set()
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
        elif isinstance(n, ast.Assign):
            names.update(t.id for t in n.targets if isinstance(t, ast.Name))
    return names


def _locate_owned(root: Path, owned: list[Path], parts: list[str]):  # O(o*B) total
    """Match a planned name to an owned file: by module name first, then by what the files define."""
    for f in owned:  # short module names (`slug.slugify` -> src/sitegen/slug.py)
        mod = module_name(root, f)
        for k in range(len(parts), 0, -1):
            if ".".join(parts[:k]) == mod or (k == 1 and parts[0] == mod.rsplit(".", 1)[-1]):
                return mod, parts[k:], f
    existing = [f for f in owned if f.is_file()]
    for f in existing:
        if parts[0] in _top_level_names(f):
            return module_name(root, f), parts, f
    if existing:
        return module_name(root, existing[0]), parts, existing[0]
    return "", parts, owned[0] if owned else None


def locate(root: Path, plan: planfile.Plan, p: Planned) -> tuple[str, list[str], Path | None]:  # O(d^2 + o*B) per item (re-parses owned files)
    """Return (module, attribute chain, defining file) for a planned item."""
    parts = p.parts
    for k in range(len(parts) - 1, 0, -1):
        f = find_module_file(root, ".".join(parts[:k]))
        if f:
            return module_name(root, f), parts[k:], f
    owned = [root / o for o in owned_files(plan.tasks[p.task]) if o.endswith(".py") and not TEST_PATH_RE.search(o)]
    return _locate_owned(root, owned, parts)


def read_real(root: Path, located: list[tuple[str, list[str]]]) -> list[dict]:  # O(P) + 1 subprocess spawn + import time
    todo = [(m, c) for m, c in located if m]
    results: list[dict] = []
    if todo:
        try:
            proc = subprocess.run([sys.executable, "-c", _INTROSPECT, json.dumps(todo)], cwd=root,
                                  env=project_env(root), capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=60)
            line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("@@SIG@@")), None)
            results = json.loads(line[7:]) if line else \
                [{"ok": False, "error": f"introspection failed: {proc.stderr.strip()[-200:]}"}] * len(todo)
        except subprocess.TimeoutExpired:
            results = [{"ok": False, "error": "introspection timed out"}] * len(todo)
    it = iter(results)
    return [next(it) if m else {"ok": False, "error": "no module found (file missing)"}
            for m, _c in located]


@dataclass
class Mismatch:
    part: int
    file: str
    line: int
    msg: str
    task: str = ""

    def __str__(self) -> str:  # O(m) total
        return f"MISMATCH part{self.part} {self.file}:{self.line} - {self.msg}"


def rel_to(root: Path, f: str | Path | None) -> str:  # O(d) path syscalls total
    if not f:
        return "?"
    try:
        return Path(f).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(f).replace("\\", "/")


def check_interfaces(plan: planfile.Plan, root: Path, task_ids: list[str] | None  # O(P*(d^2 + o*B + p^2)) + 1 subprocess total
                     ) -> tuple[list[Mismatch], int]:
    items = planned_items(plan, task_ids)
    out: list[Mismatch] = []
    ts = {i: hit for i, p in enumerate(items) if (hit := locate_ts(root, plan, p))}
    for i, (f, name) in ts.items():
        if not review_ts.export_line(f, name):
            out.append(Mismatch(1, rel_to(root, f), 1, f"{items[i].task} Produces: {items[i].qual}: {name} is not exported", items[i].task))
    for p in (p for i, p in enumerate(items) if i not in ts and p.ts_only):  # an owned .ts the name points at is missing
        o = review_ts.owned_match(p.parts, [owned_files(t) for t in plan.tasks.values()])
        if o:
            out.append(Mismatch(1, o, 1, f"{p.task} Produces: {p.qual}: no module found ({o} missing)", p.task))
    items = [p for i, p in enumerate(items) if i not in ts and not p.ts_only]
    locs = [locate(root, plan, p) for p in items]
    reals = read_real(root, [(m, c) for m, c, _f in locs])
    for p, (_mod, _chain, f), real in zip(items, locs, reals):
        msgs = compare_sig(p, real)
        where = real.get("file") or f
        file = rel_to(root, where) if where else rel_to(root, plan.path)
        line = int(real.get("line") or 1) if real.get("file") else (1 if f else p.plan_line)
        out += [Mismatch(1, file, line, f"{p.task} Produces: {m}", p.task) for m in msgs]
    return out, len(items) + len(ts)


def locate_ts(root: Path, plan: planfile.Plan, p: Planned) -> tuple[Path, str] | None:  # O(T*o + F) per item
    """(TypeScript file, exported name) for a dotted Produces name with no Python module, else None."""
    if any(find_module_file(root, ".".join(p.parts[:k])) for k in range(1, len(p.parts))) or _owned_py(root, plan, p):
        return None
    owns = [owned_files(plan.tasks[p.task])] + [owned_files(t) for tid, t in plan.tasks.items() if tid != p.task]
    return review_ts.locate(root, p.parts, owns)


def _owned_py(root: Path, plan: planfile.Plan, p: Planned) -> bool:  # O(o*B) total
    """True when the task's own Python files resolve the name (a short module name or a top-level definition)."""
    owned = [root / o for o in owned_files(plan.tasks[p.task]) if o.endswith(".py") and not TEST_PATH_RE.search(o)]
    mods = {module_name(root, f) for f in owned}
    short = {m.rsplit(".", 1)[-1] for m in mods}
    if any(".".join(p.parts[:k]) in mods for k in range(1, len(p.parts) + 1)) or p.parts[0] in short:
        return True
    return any(p.parts[0] in _top_level_names(f) for f in owned if f.is_file())


# --------------------------------------------------------------------------- data contract


@dataclass
class ContractLine:
    method: str
    path: str
    body: object | None
    status: int | None
    shape: object | None  # parsed shape or None
    plan_line: int


@dataclass
class Call:
    file: str
    line: int
    method: str
    path: str
    prefix: bool


def _path_clean(p: str) -> tuple[str, bool]:  # O(m) total
    prefix = False
    cut = re.search(r"\$\{|\{\{|[?#]", p)
    if cut:
        prefix = cut.group(0) in ("${", "{{")
        p = p[: cut.start()]
    return p, prefix


def _calls_in(rel: str, f: Path, text: str) -> list[Call]:  # O(B*M) total (line_of rescans text per match)
    calls: list[Call] = []

    def line_of(pos: int) -> int:
        return text.count("\n", 0, pos) + 1

    for m in re.finditer(r"\bfetch\(\s*([`'\"])(/[^`'\"]*)", text):
        nxt = text.find("fetch(", m.end())
        window = text[m.end(): nxt if nxt != -1 else m.end() + 300][:300]
        mm = re.search(r"method\s*:\s*[`'\"](\w+)", window)
        path, prefix = _path_clean(m.group(2))
        calls.append(Call(rel, line_of(m.start()), (mm.group(1) if mm else "GET").upper(), path, prefix))
    for m in re.finditer(r"\b(?:axios|requests|session|client|http)\.(get|post|put|patch|delete|head)\(\s*"
                         r"f?([`'\"])(?:https?://[^/`'\"]+)?(/[^`'\"]*)", text):
        path, prefix = _path_clean(m.group(3))
        if f.suffix == ".py" and "{" in m.group(3):
            path, prefix = m.group(3).split("{", 1)[0], True
        calls.append(Call(rel, line_of(m.start()), m.group(1).upper(), path, prefix))
    for m in re.finditer(r"\.request\(\s*(['\"])(\w+)\1\s*,\s*f?(['\"])(/[^'\"]*)", text):
        path, prefix = _path_clean(m.group(4))
        calls.append(Call(rel, line_of(m.start()), m.group(2).upper(), path, prefix))
    return calls


def scan_calls(root: Path, files: list[str]) -> list[Call]:  # O(F*B*M) total
    calls: list[Call] = []
    for rel in files:
        f = root / rel
        if f.suffix not in CONSUMER_EXT or TEST_PATH_RE.search(rel) or not f.is_file():
            continue
        calls += _calls_in(rel, f, f.read_text(encoding="utf-8", errors="replace"))
    return calls


def _segments(path: str) -> list[str]:  # O(m) total
    return path.strip("/").split("/")


def _is_param(seg: str) -> bool:  # O(m) total
    return bool(re.fullmatch(r"\{[^}]*\}|:[\w]+|<[^>]*>", seg))


def call_in_contract(call: Call, contract: list[ContractLine]) -> bool:  # O(C*s) per call, s = path segments
    cs = _segments(call.path)
    for c in contract:
        if c.method != call.method:
            continue
        if call.prefix:  # the call path stops at a template literal, e.g. /api/cells/${ref}
            if c.path.startswith(call.path):
                return True
            continue
        ks = _segments(c.path)
        if len(ks) == len(cs) and all(_is_param(k) or k == s for k, s in zip(ks, cs)):
            return True
    return False
