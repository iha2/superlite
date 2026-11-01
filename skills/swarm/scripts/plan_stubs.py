"""Interface signatures and stubs for plan_tools: parse a `name(args) -> ret` Produces line, resolve its module,
and write stub modules (Python) or print a language-neutral stub spec (any other language). Stdlib only."""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

from plan_tools import REF_TAIL, Plan, Task, overlay_rc, split_top_level, strip_ticks


# A trailing "(T4)" / "(T4, T5)" / "(T14, names only)" names the producer; the note after the ids is ignored (planfile.REF_TAIL).

def _closing_paren(s: str, at: int) -> int | None:  # O(s)
    """Index of the bracket that closes the one opened at s[at], or None."""
    depth = 0
    for i in range(at, len(s)):
        depth += (s[i] in "([{") - (s[i] in ")]}")
        if depth == 0:
            return i
    return None


def parse_signature(item: str) -> dict | None:  # O(s) total
    """Split a `name(args) -> ret` interface line into name, args, ret, module path, class and member; None if it is not one."""
    s = strip_ticks(item)
    s = re.sub(REF_TAIL, "", s)
    m = re.match(r"^([A-Za-z_][\w.]*)\s*\(", s)
    if not m:
        return None
    close = _closing_paren(s, m.end() - 1)
    if close is None:
        return None
    args = s[m.end():close].strip()
    rest = s[close + 1:].strip()
    ret = None
    if rest.startswith("->"):
        ret = strip_ticks(rest[2:]).rstrip(".;,").strip() or None
    segs = m.group(1).split(".")
    member = segs[-1]
    cls, mod = None, segs[:-1]
    if len(segs) >= 2 and segs[-2][:1].isupper():
        cls, mod = segs[-2], segs[:-2]
    elif member[:1].isupper():
        cls, member, mod = member, "__init__", segs[:-1]
    return {"name": m.group(1), "args": args, "ret": ret, "module": mod, "cls": cls, "member": member, "item": s}


def sig_key(sig: dict) -> str:
    return f"{sig['cls']}.{sig['member']}" if sig["cls"] else sig["member"]


def param_names(args: str) -> list[str]:  # O(s) total
    names = []
    for p in split_top_level(args):
        p = p.strip()
        if p in ("/", "*"):
            continue
        p = p.lstrip("*")
        names.append(re.split(r"[:=\s]", p, 1)[0])
    return names


def resolve_module(prefix: list[str], owned: list[str]) -> tuple[str | None, str | None]:  # O(o*s) total
    """Return (relative path, warning)."""
    if not prefix:
        if len(owned) == 1:
            return owned[0], None
        non_init = [o for o in owned if not o.endswith("__init__.py")]
        if len(non_init) == 1:
            return non_init[0], None
        return None, "cannot tell which owned file holds it (name a module in the Produces line)"
    p = "/".join(prefix)
    cands = [o for o in owned if o[:-3] == p or o[:-3].endswith("/" + p)]
    if len(cands) == 1:
        return cands[0], None
    stem = prefix[-1]
    cands = [o for o in owned if o.rsplit("/", 1)[-1][:-3] == stem]
    if len(cands) == 1:
        return cands[0], None
    return p + ".py", f"module {'.'.join(prefix)!r} is not in the task's Owns; using {p}.py"


def _doc(text: str, indent: str) -> str:  # O(s)
    text = text.replace("\\", "\\\\").replace('"""', "'''")
    return f'{indent}"""{text}"""'


def _def_source(sig: dict, indent: str, tid: str, contract: str, is_method: bool) -> str:  # O(s) total
    args = sig["args"]
    if is_method:
        first = param_names(args)[:1]
        if not first or first[0] not in ("self", "cls"):
            args = "self" + (", " + args if args else "")
    ret = f" -> {sig['ret']}" if sig["ret"] else ""
    doc = f"Contract: {contract}" if contract else f"Contract: see plan task {tid}."
    body = f'{indent}def {sig["member"]}({args}){ret}:\n{_doc(doc, indent + "    ")}\n{indent}    raise NotImplementedError("{tid}")\n'
    return body


def build_stub_defs(task: Task, warnings: list[str]):  # O(q*(k*s + s)) total
    """Yield (relative path, sig, source-for-def, contract) for each Produces signature."""
    out = []
    for item in task.produces:
        sig = parse_signature(item)
        if sig is None:
            continue
        path, warn = resolve_module(sig["module"], task.owned_sources)
        if warn:
            warnings.append(f"{task.id}: {sig['name']}: {warn}")
        if path is None:
            continue
        for attempt in (sig, dict(sig, ret=None)):
            try:
                compile("from __future__ import annotations\n" + _def_source(attempt, "", task.id, "", False), "<stub>", "exec")
                sig = attempt
                break
            except SyntaxError:
                sig = None
        if sig is None:
            warnings.append(f"{task.id}: cannot parse signature {item!r}; skipped")
            continue
        out.append((path, sig))
    return out


def render_module(path: str, entries: list[tuple[Task, dict]], warnings: list[str]) -> str:  # O(E*s) total
    funcs: list[tuple[Task, dict]] = []
    classes: dict[str, list[tuple[Task, dict]]] = {}
    seen = set()
    for task, sig in entries:
        key = (sig["cls"], sig["member"])
        if key in seen:
            warnings.append(f"{path}: duplicate Produces for {sig['name']} ({task.id}); kept the first")
            continue
        seen.add(key)
        if sig["cls"]:
            classes.setdefault(sig["cls"], []).append((task, sig))
        else:
            funcs.append((task, sig))
    names = [s["member"] for _, s in funcs] + list(classes)
    out = ['"""Generated interface stubs (plan_tools.py stubs). The stub is the contract:',
           'do not change a signature without a coordinator ruling."""',
           "from __future__ import annotations", "",
           "__all__ = [" + ", ".join(json.dumps(n) for n in names) + "]", ""]
    for task, sig in funcs:
        out.append("")
        out.append(_def_source(sig, "", task.id, task.contract_text, False).rstrip("\n"))
        out.append("")
    for cname, items in classes.items():
        first = items[0][0]
        doc = f"Contract: {first.contract_text}" if first.contract_text else f"Contract: see plan task {first.id}."
        out.append("")
        out.append(f"class {cname}:")
        out.append(_doc(doc, "    "))
        for task, sig in items:
            out.append("")
            out.append(_def_source(sig, "    ", task.id, task.contract_text, True).rstrip("\n"))
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


# What running a module under check can raise: every ordinary error family, reported by name.
MODULE_ERRORS = (ArithmeticError, AssertionError, AttributeError, ImportError, LookupError, NameError, OSError,
                 RuntimeError, SyntaxError, TypeError, ValueError)


def import_check(path: Path) -> str | None:  # O(module size) + exec
    name = f"_stubcheck_{abs(hash(str(path)))}"
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except MODULE_ERRORS as exc:
        return f"{type(exc).__name__}: {exc}"
    finally:
        sys.modules.pop(name, None)
    return None


def draft_ids(plan: Plan) -> set[str]:  # O(n*k*s)
    return {t.id for t in plan.tasks if t.status == "draft"}


def _cmd_stub_spec(plan: Plan) -> int:  # O(n*q*s)
    """Non-Python: print one stub line and one contract line per Produces signature. Write nothing."""
    n = 0
    drafts = draft_ids(plan)
    for task in plan.tasks:
        if task.id in drafts:
            continue
        for item in task.produces:
            sig = parse_signature(item)
            if sig is None:
                print(f"warning: {task.id}: cannot parse signature {item!r}; skipped", file=sys.stderr)
                continue
            print(f"stub: {sig['item']}")
            print(f"contract: {task.contract_text}")
            n += 1
    if n == 0:
        print("no stubbable Produces lines found")
    return 0


def cmd_stubs(args) -> int:  # O(C+L + E*s) total + file I/O
    root = Path(args.root)
    bad = overlay_rc(root)
    if bad:
        return bad
    import project_config
    plan = Plan.load(args.plan)
    if project_config.language(root) != "python":
        return _cmd_stub_spec(plan)
    warnings: list[str] = []
    per_file: dict[str, list[tuple[Task, dict]]] = {}
    drafts = draft_ids(plan)
    for task in plan.tasks:
        if task.id in drafts:
            continue
        for path, sig in build_stub_defs(task, warnings):
            per_file.setdefault(path, []).append((task, sig))
    rc = 0
    for rel in sorted(per_file):
        target = root / rel
        if target.exists():
            print(f"skipped {rel} (exists)")
            continue
        src = render_module(rel, per_file[rel], warnings)
        if args.dry_run:
            print(f"would write {rel}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(src, encoding="utf-8", newline="\n")
        print(f"wrote {rel}")
        err = import_check(target)
        if err:
            print(f"import check failed for {rel}: {err}", file=sys.stderr)
            rc = 1
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    if not per_file:
        print("no stubbable Produces lines found")
    return rc
