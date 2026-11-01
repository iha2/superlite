"""Probe adapter and the live-server half of the seam check.

    superlite-cli plan adapter --plan PLAN.md [--root .] [--out .superlite/probes/adapter.py]

Reads `**Probe entry:** call=pkg.mod.fn · run=python -m pkg · serve=pkg.server.create_server(Workbook())`.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import planfile  # noqa: E402
import state  # noqa: E402
from review_contract import (HTTP_LINE_RE, ContractLine, Mismatch, ToolError, call_in_contract,  # noqa: E402
                             layout, locate, module_name, owned_files, planned_items, probe_entries, project_env,
                             rel_to, scan_calls, task_start)


_SERVE_SNIPPET = r'''
def _make_server(target, args_src):
    import ast, importlib, inspect
    mod_name, _, fn_name = target.rpartition(".")
    mod = importlib.import_module(mod_name)
    fn = getattr(mod, fn_name)
    pos, kw = (), {}
    if args_src is not None:
        tree = ast.parse("_cap(%s)" % args_src, mode="eval").body
        if not isinstance(tree, ast.Call):
            raise RuntimeError("serve args must be a call argument list")
        import builtins
        ns = dict(vars(builtins))
        ns.update(vars(mod))

        def _call_args(call):
            a = []
            for x in call.args:
                if isinstance(x, ast.Starred):
                    a.extend(_resolve(x.value))
                else:
                    a.append(_resolve(x))
            k = {}
            for kw in call.keywords:
                if kw.arg is None:
                    k.update(_resolve(kw.value))
                else:
                    k[kw.arg] = _resolve(kw.value)
            return a, k

        def _resolve(node):
            if isinstance(node, ast.Constant):
                return node.value
            if isinstance(node, ast.Name):
                if node.id not in ns:
                    raise RuntimeError("unknown name %r in serve args" % node.id)
                return ns[node.id]
            if isinstance(node, ast.Attribute):
                return getattr(_resolve(node.value), node.attr)
            if isinstance(node, ast.Call):
                callee = _resolve(node.func)
                a, k = _call_args(node)
                return callee(*a, **k)
            if isinstance(node, ast.List):
                return [_resolve(x) for x in node.elts]
            if isinstance(node, ast.Tuple):
                return tuple(_resolve(x) for x in node.elts)
            if isinstance(node, ast.Dict):
                out = {}
                for k, v in zip(node.keys, node.values):
                    if k is None:
                        out.update(_resolve(v))
                    else:
                        out[_resolve(k)] = _resolve(v)
                return out
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)) \
                    and isinstance(node.operand, ast.Constant) and isinstance(node.operand.value, (int, float)):
                return node.operand.value if isinstance(node.op, ast.UAdd) else -node.operand.value
            raise RuntimeError("unsupported serve arg expression: %s" % type(node).__name__)

        pos, kw = _call_args(tree)
        pos = tuple(pos)
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
'''

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
'''

def parse_serve(entry: str) -> tuple[str, str | None]:  # O(m) total
    m = re.match(r"^\s*([\w.]+)\s*(?:\((.*)\))?\s*$", entry, re.S)
    if not m:
        raise ToolError(f"bad serve entry: {entry!r}")
    return m.group(1), m.group(2)


def parse_probe_entry(text: str) -> dict[str, str]:  # O(m) total
    out: dict[str, str] = {}
    for part in text.split("·"):
        key, eq, val = part.strip().partition("=")
        key = key.strip().lower()
        if eq and key in ("call", "run", "serve") and val.strip():
            out[key] = val.strip().strip("`").strip()
    return out


class ServerHandle:
    """A real server in its own process (own PID), port 0; killed by PID in close()."""

    def __init__(self, root: Path, target: str, args: str | None, timeout: float = 20.0):  # O(T/0.05 * X) poll worst + process spawn
        self.tmp = tempfile.mkdtemp(prefix="pt_review_")
        self.out_path = Path(self.tmp) / "out.txt"
        self.err_path = Path(self.tmp) / "err.txt"
        self._out = open(self.out_path, "wb")
        self._err = open(self.err_path, "wb")
        self.proc = subprocess.Popen(
            [sys.executable, "-c", _SERVER_RUNNER, json.dumps({"target": target, "args": args})],
            cwd=root, env=project_env(root), stdout=self._out, stderr=self._err, stdin=subprocess.DEVNULL)
        self.info: dict | None = None
        self.error: str | None = None
        deadline = time.time() + timeout
        while time.time() < deadline:
            text = self.out_path.read_text(encoding="utf-8", errors="replace")
            for ln in text.splitlines():
                if ln.startswith("@@READY@@"):
                    self.info = json.loads(ln[9:])
                elif ln.startswith("@@ERR@@"):
                    self.error = ln[7:]
            if self.info or self.error or self.proc.poll() is not None:
                break
            time.sleep(0.05)
        if not self.info and not self.error:
            tail = self.err_path.read_text(encoding="utf-8", errors="replace").strip()[-200:]
            self.error = "server did not start" + (f": {tail}" if tail else "")

    def request(self, method: str, path: str, body) -> tuple[int, bytes]:  # O(response size) + network round trip
        host = self.info["host"] if self.info["host"] not in ("", "0.0.0.0") else "127.0.0.1"
        conn = http.client.HTTPConnection(host, self.info["port"], timeout=10)
        try:
            data = None if body is None else json.dumps(body).encode()
            conn.request(method, path, body=data,
                         headers={"Content-Type": "application/json"} if data is not None else {})
            r = conn.getresponse()
            return r.status, r.read()
        finally:
            conn.close()

    def close(self) -> None:
        try:
            self.proc.kill()
            self.proc.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            pass
        self._out.close()
        self._err.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


def _split_commas(s: str) -> list[str]:  # O(m) total
    """Split on commas outside brackets and braces."""
    parts, depth, cur = [], 0, []
    for ch in s:
        depth += (ch in "{([") - (ch in "})]")
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def parse_shape(text: str):  # O(m*depth) total
    text = text.strip()
    if text.startswith("{") and text.endswith("}"):
        shape: dict[str, tuple[bool, object]] = {}
        for part in _split_commas(text[1:-1]):
            key, _, typ = part.partition(":")
            key = key.strip().strip("\"'")
            optional = key.endswith("?")
            shape[key.rstrip("?")] = (optional, parse_shape(typ) if typ.strip() else "any")
        return shape
    if "|" in text and not text.startswith(("{", "list[")):
        return ("union", [parse_shape(t) for t in text.split("|")])
    return text.strip("`\"'").lower()


_TYPE_CHECKS = {
    "str": lambda v: isinstance(v, str), "string": lambda v: isinstance(v, str),
    "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "float": lambda v: isinstance(v, float) or (isinstance(v, int) and not isinstance(v, bool)),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "bool": lambda v: isinstance(v, bool), "boolean": lambda v: isinstance(v, bool),
    "list": lambda v: isinstance(v, list), "array": lambda v: isinstance(v, list),
    "dict": lambda v: isinstance(v, dict), "object": lambda v: isinstance(v, dict),
    "null": lambda v: v is None, "none": lambda v: v is None, "any": lambda v: True,
}


def _check_object(value, shape: dict, where: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{where} is {type(value).__name__}, expected object"]
    probs: list[str] = []
    for key, (optional, sub) in shape.items():
        if key in value:
            probs.extend(check_shape(value[key], sub, f"{where}.{key}"))
        elif not optional:
            probs.append(f"{where} lacks required key '{key}'")
    return probs


def _check_named(value, shape: str, where: str) -> list[str]:
    m = re.match(r"^(?:list|array)\[(.+)\]$", shape)
    if m and not isinstance(value, list):
        return [f"{where} is {type(value).__name__}, expected list"]
    if m:
        probs: list[str] = []
        for n, item in enumerate(value[:20]):
            probs.extend(check_shape(item, parse_shape(m.group(1)), f"{where}[{n}]"))
        return probs
    check = _TYPE_CHECKS.get(shape)
    if check and not check(value):
        return [f"{where} is {type(value).__name__}, expected {shape}"]
    return []


def check_shape(value, shape, where: str = "$") -> list[str]:  # O(size of value + shape), arrays capped at 20 items
    if isinstance(shape, dict):
        return _check_object(value, shape, where)
    if isinstance(shape, tuple) and shape and shape[0] == "union":
        alts = [check_shape(value, s, where) for s in shape[1]]
        return [] if any(not a for a in alts) else [f"{where} matches none of the allowed types"]
    if isinstance(shape, str):
        return _check_named(value, shape, where)
    return []


def _contract_line(raw: str, i: int) -> ContractLine | None:  # O(m) total
    m = HTTP_LINE_RE.match(raw)
    if not m:
        return None
    rest = m.group(3).strip().rstrip("`")
    left, right = (re.split(r"->|→", rest, maxsplit=1) if re.search(r"->|→", rest) else (rest, ""))
    body = None
    if left.strip():
        try:
            body = json.loads(left.strip())
        except ValueError:
            body = None
    status, shape = None, None
    sm = re.match(r"^\s*(\d{3})\s*(.*)$", right)
    if sm:
        status = int(sm.group(1))
        shape = parse_shape(sm.group(2).strip()) if sm.group(2).strip() else None
    return ContractLine(m.group(1), m.group(2), body, status, shape, i + 1)


def data_contract(plan: planfile.Plan, task_ids: list[str]) -> list[ContractLine]:  # O(L*m + H) total
    lay = layout(plan)
    ranges = [r for r in (lay.task_range(t) for t in task_ids if t in plan.tasks) if r]
    ranges += [(i, end) for i, _l, title, end in lay.heads
               if "contract" in title.lower() and not planfile.HEADING.match(plan.lines[i])]
    seen: set[int] = set()
    out: list[ContractLine] = []
    for s, e in ranges:
        for i in (i for i in range(s, e) if i not in seen):
            seen.add(i)
            raw = re.sub(r"^\s*[-*]\s+", "", plan.lines[i]).strip().strip("`").strip()
            line = _contract_line(raw, i)
            if line:
                out.append(line)
    return out


def find_serve(plan: planfile.Plan, root: Path, producer: planfile.Task) -> tuple[str, str | None] | None:  # O(n + E_p*m + producer items*(d^2 + o*B)) total
    owned_mods = {module_name(root, root / o) for o in owned_files(producer) if o.endswith(".py")}
    entries = []
    for e in probe_entries(plan):
        serve = parse_probe_entry(e).get("serve")
        if serve:
            entries.append(parse_serve(serve))
    for target, args in entries:
        if target.rpartition(".")[0] in owned_mods:
            return target, args
    if entries:
        return entries[0]
    for p in planned_items(plan, [producer.id]):
        if not (p.ret and "Server" in p.ret):
            continue
        mod, chain, _f = locate(root, plan, p)
        if mod and len(chain) == 1:
            return f"{mod}.{chain[0]}", None
    return None


def _check_line(handle: ServerHandle, c, loc: tuple[str, int], producer_id: str) -> list[Mismatch]:  # O(response size) + network round trip
    """Send one contract line to the live server and compare status and shape."""
    where = f"{c.method} {c.path}"
    path = re.sub(r"\{[^}]*\}|:\w+|<[^>]*>", "x", c.path)
    try:
        status, raw = handle.request(c.method, path, c.body)
    except (OSError, http.client.HTTPException, ValueError) as e:  # a dead server is a mismatch, not a crash
        return [Mismatch(2, *loc, f"{where} failed: {type(e).__name__}: {e}", producer_id)]
    if c.status is not None and status != c.status:
        return [Mismatch(2, *loc, f"{where} returned status {status}, contract says {c.status}", producer_id)]
    if c.shape is None:
        return []
    try:
        value = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        return [Mismatch(2, *loc, f"{where} body is not JSON, contract expects a shape", producer_id)]
    return [Mismatch(2, *loc, f"{where}: {prob}", producer_id) for prob in check_shape(value, c.shape)]


def check_data_contract(plan: planfile.Plan, root: Path, producer: planfile.Task, consumer: planfile.Task  # O(M*C + C*(|response| + network)) + server spawn total
                        ) -> tuple[list[Mismatch], list[str]]:
    contract = data_contract(plan, [producer.id, consumer.id])
    if not contract:
        return [], ["part2 skipped: no data contract lines (METHOD /path -> status {shape}) in the plan"]
    mm: list[Mismatch] = []
    for c in scan_calls(root, owned_files(consumer)):
        if not call_in_contract(c, contract):
            shown = c.path + ("..." if c.prefix else "")
            mm.append(Mismatch(2, c.file, c.line, f"consumer {consumer.id} calls {c.method} {shown}, "
                                                  f"which is not in the data contract", consumer.id))
    serve = find_serve(plan, root, producer)
    if not serve:
        return mm, ["part2: server not started (no serve= in a Probe entry and no Produces returning a server)"]
    handle = ServerHandle(root, *serve)
    try:
        if handle.error or not handle.info:
            err = handle.error or "unknown"
            if "needs arguments" in err:
                return mm, [f"part2: server not started: {err}"]
            mm.append(Mismatch(2, rel_to(root, plan.path), task_start(plan, producer.id) + 1,
                               f"producer {producer.id} server failed to start: {err}", producer.id))
            return mm, []
        loc = (rel_to(root, handle.info["file"]) if handle.info.get("file") else rel_to(root, plan.path),
               handle.info.get("line") or 1)
        for c in contract:
            mm += _check_line(handle, c, loc, producer.id)
    finally:
        handle.close()
    return mm, []


# --------------------------------------------------------------------------- adapter command

_ADAPTER_HEAD = '''"""Probe adapter, generated by superlite-cli plan adapter from the plan's Probe entry lines.
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
'''

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
'''

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
'''

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
'''


def build_adapter(plan: planfile.Plan, out: Path, root: Path) -> str:  # O(E_p*m + template size) total
    entries: list[dict] = []
    for text in probe_entries(plan):
        parsed = parse_probe_entry(text)
        if not parsed:
            continue
        first = parsed.get("call") or parsed.get("serve") or parsed.get("run", "")
        first = re.sub(r"^(python3?|py)\s+-m\s+", "", first.split("(")[0].strip())
        entry: dict = {"package": first.split(".")[0].split()[0] if first else f"p{len(entries)}"}
        for kind in ("call", "run"):
            if kind in parsed:
                entry[kind] = parsed[kind]
        if "serve" in parsed:
            entry["serve"] = list(parse_serve(parsed["serve"]))
        entries.append(entry)
    if not entries:
        raise ToolError("no `Probe entry:` lines with call=/run=/serve= found in the plan")
    kinds = [k for k in ("call", "run", "serve") if any(k in e for e in entries)]
    rel_root = os.path.relpath(root, out.parent.resolve()).replace("\\", "/")
    src = _ADAPTER_HEAD.replace("__REL_ROOT__", repr(rel_root)).replace("__ENTRIES__", repr(entries)) \
        .replace("__KINDS__", repr(["request" if k == "serve" else k for k in kinds]))
    templates = {"call": _ADAPTER_CALL, "run": _ADAPTER_RUN, "serve": _ADAPTER_SERVE}
    return src + "".join(templates[k] for k in kinds)


SEAM_FILES = {
    "typescript": "seam.ts", "javascript": "seam.js", "go": "seam.go", "rust": "seam.rs",
    "csharp": "seam.cs", "java": "seam.java", "ruby": "seam.rb",
}


def seam_path(root: Path, language: str) -> Path:
    """Python keeps adapter.py. Every other language gets its own seam file."""
    lang = (language or "").strip().lower()
    name = "adapter.py" if lang in ("", "python") else SEAM_FILES.get(lang, "seam.txt")
    return state.path(Path(root), "probes") / name


def cmd_adapter(a: argparse.Namespace) -> int:  # O(template size) + file write total
    plan = planfile.read(a.plan)
    root = Path(a.root).resolve()
    import project_config
    try:
        project_config.load(root)
    except project_config.ProjectConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    lang = "python" if a.out else project_config.language(root)  # explicit --out stays the Python adapter
    out = Path(a.out) if a.out else seam_path(root, lang)
    out.parent.mkdir(parents=True, exist_ok=True)
    if lang != "python":
        out.write_text(f"// superlite seam: {lang}\n", encoding="utf-8", newline="\n")
    else:
        src = build_adapter(plan, out, root)
        compile(src, str(out), "exec")
        out.write_text(src, encoding="utf-8", newline="\n")
    print(f"wrote {out}")
    return 0
