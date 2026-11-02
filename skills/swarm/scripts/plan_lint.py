"""Lint for plan_tools: placeholders, Owns, Consumes/Produces, diagrams, size caps, CHAIN, Kind, spec coverage,
and the STABLE/FIX partition (`lint --stable`). Stdlib only."""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

import base
import plan_tools as pt
from plan_schedule import (CTX_CEILING, DEFAULT_SIZE_BAND, ancestors, block_hash, chain_id, dispatched_tasks, edge_notes,
                           effective_context, effective_times, example_issues, find_cycle, parse_taskgraph,
                           schedule_report, split_notes, stable_partition, task_interface, waited_graph)
from plan_stubs import param_names, parse_signature, sig_key
from plan_tools import (DEFAULT_MAX_CHARS, REF_TAIL, TASK_ID, FenceBlock, Plan, PlanError, Task, nkey, overlay_rc,
                        scan_fences, strip_ticks)

PLACEHOLDERS = [
    (r"\bTBD\b", "TBD"),
    (r"\bTODO\b", "TODO"),
    (r"handle edge cases", "'handle edge cases'"),
    (r"add (?:appropriate )?error handling", "'add error handling'"),
    (r"similar to task\s+\w+", "'similar to Task N'"),
    (r"tests? for the above", "'tests for the above'"),
    (r"\bfill in later\b|\bto be decided\b", "deferred detail"),
]
DIAGRAM_TYPES = ("flowchart", "graph", "sequencediagram", "statediagram", "statediagram-v2", "gantt", "classdiagram",
                 "erdiagram", "journey", "pie", "mindmap", "timeline", "gitgraph", "quadrantchart", "requirementdiagram")
SPEC_SKIP = {"overview", "introduction", "background", "goals", "non-goals", "glossary", "contents",
             "table of contents", "references", "open questions", "appendix", "summary", "status"}


def check_diagram(block: FenceBlock) -> str | None:  # O(B) total, B = block chars
    lines = [ln for ln in block.content if ln.strip() and not ln.strip().startswith("%%")]
    if not lines:
        return "empty mermaid block"
    first = lines[0].strip().split()[0].lower()
    if first not in DIAGRAM_TYPES:
        return f"unknown diagram type {lines[0].strip().split()[0]!r}"
    if first in ("flowchart", "graph"):
        subs = sum(1 for ln in lines if ln.strip().lower().startswith("subgraph"))
        ends = sum(1 for ln in lines if ln.strip().lower() == "end")
        if subs != ends:
            return "unbalanced subgraph/end"
    body = "\n".join(lines)
    body = re.sub(r'"[^"\n]*"', "", body)
    body = re.sub(r"<[^>\n]*>", "", body)
    for o, c in (("[", "]"), ("{", "}")):
        if body.count(o) != body.count(c):
            return f"unbalanced {o}{c}"
    if first in ("flowchart", "graph") and body.count("(") != body.count(")"):
        return "unbalanced ()"
    return None


def review_focus_cases(plan: Plan) -> dict[str, set[str]]:  # O(L)
    """{package: {cases}} from the plan's Review Focus section lines `- <package>: deep, numeric, ...`."""
    out: dict[str, set[str]] = {}
    inside = False
    for i, ln in enumerate(plan.lines):
        if plan.fenced[i]:
            continue
        if re.match(r"^#{1,6}\s", ln):
            inside = "review focus" in ln.lower()
            continue
        m = re.match(r"^\s*[-*]\s+`?([\w./-]+?)`?\s*:\s*(.+)$", ln) if inside else None
        if m:
            out.setdefault(m.group(1).lower(), set()).update(re.findall(r"[a-z]+", m.group(2).lower()))
    return out


def not_mechanical_reasons(task: Task, focus: dict[str, set[str]]) -> list[str]:  # O(k*s + pkgs)
    feats = task.explicit_features
    why: list[str] = []
    if feats.get("loc", 0) > 150:
        why.append(f"LOC owned {feats['loc']} > 150")
    if feats.get("components", 0) > 2:
        why.append(f"Components {feats['components']} > 2")
    if feats.get("tests") == "slow":
        why.append("Tests: slow")
    parts = {seg.lower() for o in task.owns for seg in re.split(r"[/.]", o)}
    hard = sorted({c for pkg, cases in focus.items() if pkg in parts for c in cases}
                  & {"deep", "numeric", "state", "protocol"})
    if hard:
        why.append(f"Review Focus has {', '.join(hard)}")
    txt = task.text
    named = (re.search(r"\btest_\w+", txt)
             and re.search(r"==|\bassert\b|\bexpect|->|\breturns?\b", txt, re.I))
    if not (re.search(r"test code", txt, re.I) or named):
        why.append("no test code or contract with exact inputs and outputs")
    return why


class Issue(str):
    """A str, so callers and tests that print or match messages are unaffected. `tasks` names the
    subject tasks structurally; `scope` is "task" (blocks those tasks) or "plan" (never blocks a
    stable task by itself); `chain` marks a flagged CHAIN, whose member tasks are unstable."""
    tasks: tuple
    scope: str
    chain: bool

    def __new__(cls, msg: str, tasks=(), scope: str = "task", chain: bool = False):  # O(s)
        o = super().__new__(cls, msg)
        o.tasks, o.scope, o.chain = tuple(tasks), scope, chain
        return o


def size_band(coef: dict, args=None) -> tuple[float, float]:
    b = coef.get("size_band") or {}
    low = float(b.get("low_min", DEFAULT_SIZE_BAND["low_min"]))
    high = float(b.get("high_min", DEFAULT_SIZE_BAND["high_min"]))
    override = getattr(args, "max_task_min", None)
    if override is not None:
        high = float(override)
    return low, high



class _Lint:
    """One lint run: the checks append to issues and warnings in a fixed order."""

    def __init__(self, plan: Plan, args):
        self.plan, self.args = plan, args
        self.issues: list = []
        self.warnings: list[str] = []
        self.graph: dict | None = None
        self.full_graph: dict | None = None

    def add(self, msg: str, tasks=(), scope: str = "task", chain: bool = False) -> None:
        self.issues.append(Issue(msg, tasks, scope, chain))

    def headings(self) -> None:
        plan = self.plan
        ids = [t.id for t in plan.tasks]
        for tid in sorted({i for i in ids if ids.count(i) > 1}, key=nkey):
            self.add(f"duplicate task heading {tid}", [tid])
        if not plan.tasks:
            self.add("no task headings (### Tn ...) found", scope="plan")

    def placeholders(self) -> None:
        """Placeholders outside code fences and draft task blocks."""
        plan = self.plan
        draft_lines: set[int] = set()
        for t in plan.tasks:
            if t.status == "draft":
                draft_lines.update(range(t.line, t.line + t.text.count("\n") + 1))
        for i, ln in enumerate(plan.lines):
            if plan.fenced[i] or i in draft_lines:
                continue
            for pat, label in PLACEHOLDERS:
                if re.search(pat, ln, re.I):
                    owner = [t.id for t in plan.tasks if t.line <= i + 1 <= t.line + t.text.count("\n") + 1]
                    self.add(f"placeholder {label} at line {i + 1}", owner, "task" if owner else "plan")

    def header_notes(self) -> None:
        plan = self.plan
        if plan.header_ignore() is None:
            self.warnings.append("NOTE: no **Ignore:** header line; .gitignore gets only the language defaults "
                                 "(list .env, local databases, .next/, out/ and other generated paths your tooling creates)")
        for t in plan.tasks:
            if not t.has_features and t.stated_time is None:
                self.warnings.append(f"WARNING: {t.id} has no features (Components, LOC owned, Tests) and no Est. time; "
                                     "the predictor will use defaults")

    def owns(self) -> None:
        """Owns present; tasks that can run in parallel do not share files."""
        plan = self.plan
        try:
            self.full_graph = parse_taskgraph(plan)
            self.graph = waited_graph(plan)
        except PlanError as exc:
            self.add(f"taskgraph: {exc}", scope="plan")
        for t in plan.tasks:
            if not t.owns:
                self.add(f"{t.id}: no **Owns:** line", [t.id])
        if not self.graph:
            return
        anc = ancestors(self.graph)
        for a_i, a in enumerate(plan.tasks):
            for b in plan.tasks[a_i + 1:]:
                parallel = a.id in self.graph and b.id in self.graph and a.id not in anc[b.id] and b.id not in anc[a.id]
                shared = sorted(set(a.owns) & set(b.owns)) if parallel else []
                if shared:
                    self.add(f"{a.id} and {b.id} can run in parallel but both own {', '.join(shared)}", [a.id, b.id])

    def interfaces(self) -> None:
        """Every Consumes item has a Produces line with the same parameters and return type."""
        plan = self.plan
        produced: dict[str, list] = {}
        for t in plan.tasks:
            for item in t.produces:
                sig = parse_signature(item)
                if sig:
                    produced.setdefault(sig_key(sig), []).append((t.id, sig))
        for t in plan.tasks:
            for item in t.consumes:
                sig = parse_signature(item)
                if sig:
                    self._check_consumed(t, item, sig, produced)

    def _check_consumed(self, t: Task, item: str, sig: dict, produced: dict) -> None:
        ref = re.search(REF_TAIL, strip_ticks(item))
        refs = re.findall(TASK_ID, ref.group(1)) if ref else []
        cands = [c for c in produced.get(sig_key(sig), []) if not refs or c[0] in refs]
        if not cands:
            where = f" from {'/'.join(refs)}" if refs else ""
            self.add(f"{t.id} consumes {sig['name']}{where}, but no Produces line defines it", [t.id])
            return
        if any(_same_interface(psig, sig) for _, psig in cands):
            return
        pid, psig = cands[0]
        self.add(f"{t.id} consumes {sig['name']}({sig['args']}) but {pid} produces "
                 f"{psig['name']}({psig['args']})" + (f" -> {psig['ret']}" if psig["ret"] else ""), [t.id])

    def examples_and_diagrams(self) -> None:
        plan = self.plan
        self.issues.extend(example_issues(plan))  # a consumer of structured data without examples is a fix task
        self.warnings.extend(split_notes(plan))
        for b in plan.mermaid_blocks():
            err = check_diagram(b)
            if err:
                self.add(f"mermaid block at line {b.start + 1}: {err}", scope="plan")

    def size_caps(self) -> dict:
        """Plan length and per-task context ceilings; returns the predictor coefficients."""
        plan, args = self.plan, self.args
        max_chars = getattr(args, "max_chars", DEFAULT_MAX_CHARS)
        if len(plan.text) > max_chars:
            self.add(f"plan is {len(plan.text)} chars, over the {max_chars} cap", scope="plan")
        import predict
        models = predict.load_models(getattr(args, "models", None))
        coef = predict.load_coef(getattr(args, "coef", None))
        for t in plan.tasks:
            c, src = effective_context(t, models, coef, bool(getattr(args, "stated", False)))
            if c is not None and c > CTX_CEILING:
                what = "Est. context" if src == "stated" else "predicted context"
                self.add(f"{t.id}: {what} {c // 1000}k is over the {CTX_CEILING // 1000}k ceiling; split it", [t.id])
            if src == "predicted" and t.stated_context and abs(t.stated_context - c) / c > 0.25:
                self.warnings.append(f"NOTE: {t.id} states Est. context {t.stated_context // 1000}k but the predictor "
                                     f"says {c // 1000}k (over 25% apart); the predicted value is used")
        return coef

    def schedule(self, coef: dict) -> None:
        """Size band (predicted minutes), CHAIN and the time-target warning."""
        plan, args = self.plan, self.args
        if self.graph is None or find_cycle(self.graph):
            return
        low_min, high_min = size_band(coef, args)
        try:
            times, _ = effective_times(plan, self.graph, args)
        except PlanError as exc:
            self.add(str(exc), scope="plan")
            return
        for t in sorted(times, key=nkey):
            if times[t] > high_min + 1e-9:
                self.add(f"SIZE: {t} predicted {times[t]:.1f} min > {high_min:g} max; split it along its components", [t])
            elif times[t] < low_min - 1e-9 and t in plan.by_id:
                self.warnings.append(f"NOTE: {t} predicted {times[t]:.1f} min < {low_min:g}; consider merging with a related task")
        rep = schedule_report(plan, args)
        self.chain(rep)
        self.warnings.extend(edge_notes(plan))  # EDGE: edges the consumer does not wait on
        if rep["target_min"] is not None and not rep["target_met"]:
            self.warnings.append(f"MISSED TARGET: predicted {rep['total_min']:.1f} min vs target {rep['target_min']:.1f} min "
                                 f"— critical path {' → '.join(rep['critical_path'])}")

    def chain(self, rep: dict) -> None:
        """A critical path over 2.5x max(longest task, work / peak width) is a CHAIN issue unless a Ruling carries its id."""
        plan = self.plan
        cp, ctimes = rep["critical_path"], [rep["times"][t] for t in rep["critical_path"]]
        work = sum(rep["times"].values())  # total predicted work
        base = max(max(rep["times"].values(), default=0.0), work / max(1, rep["peak_width"]))
        if not (len(cp) >= 3 and ctimes and sum(ctimes) > 2.5 * base + 1e-9):
            return
        cid = chain_id(cp, ctimes)  # short hash of the chain's tasks and minutes
        rulings = re.findall(r"^.*Ruling:\s*sequential\s*\[(chain-[0-9a-f]+)\]\s*-\s*(.*)$", plan.text, re.M | re.I)
        hit = [r for r in rulings if r[0].lower() == cid]  # a ruling counts only if it carries the id lint printed
        if hit:
            self.warnings.append(f"NOTE: accepted Ruling: sequential [{cid}] chain {' -> '.join(cp)} {sum(ctimes):.1f} min "
                                 f"vs baseline {base:.1f} min (costs {sum(ctimes) - base:.1f} min): {hit[0][1].strip()}")
            return
        ignored = " (a Ruling: sequential without this id was ignored)" if re.search(r"Ruling:\s*sequential", plan.text, re.I) else ""
        self.add(f"CHAIN [{cid}]: critical path {' -> '.join(cp)} is {sum(ctimes):.1f} min, over 2.5x "
                 f"max(longest task, work / peak width) = {base:.1f} min{ignored}; give producers e.g. examples, "
                 f"or add Ruling: sequential [{cid}] - <why>", cp, "plan", chain=True)

    def models(self) -> None:
        """No Opus implementers; Model: haiku only for mechanical tasks (approved tasks only)."""
        approved = [t for t in self.plan.tasks if t.status != "draft"]
        for t in approved:
            if re.search(r"\bopus\b", t.field_text("model"), re.I):
                self.add(f"OPUS-TASK: {t.id} uses Model: opus; split it, or leave it on Sonnet and let a fixer-opus "
                         "handle what Sonnet can't", [t.id])
        focus = review_focus_cases(self.plan)
        for t in approved:
            feats = t.explicit_features
            kind = feats.get("kind", "design")
            if re.search(r"\bhaiku\b", feats.get("model") or "", re.I) and kind != "mechanical":
                self.add(f"KIND: {t.id} uses Model: haiku but is not Kind: mechanical", [t.id])
            why = not_mechanical_reasons(t, focus) if kind == "mechanical" else []
            if why:
                self.add(f"KIND: {t.id} is not mechanical: {'; '.join(why)}", [t.id])

    def spec(self) -> None:
        """Every `##`/`###` section of the spec is named in the plan."""
        path = getattr(self.args, "spec", None)
        if not path:
            return
        sp = Path(path)
        if not sp.is_file():
            self.add(f"spec not found: {path}", scope="plan")
            return
        lines = sp.read_text(encoding="utf-8-sig").replace("\r\n", "\n").split("\n")
        flags, _ = scan_fences(lines)
        plan_l = self.plan.text.lower()
        for ln, fenced in zip(lines, flags):
            m = None if fenced else re.match(r"^#{2,3}\s+(.*?)\s*#*$", ln)
            if m and not _spec_covered(m.group(1), plan_l):
                self.add(f"SPEC section {m.group(1)!r} has no task", scope="plan")


def _same_interface(produced: dict, consumed: dict) -> bool:
    same_ret = not (produced["ret"] and consumed["ret"]) or re.sub(r"\s+", "", produced["ret"]) == re.sub(r"\s+", "", consumed["ret"])
    return param_names(produced["args"]) == param_names(consumed["args"]) and same_ret


def _spec_covered(raw: str, plan_l: str) -> bool:
    nm = re.match(r"^(?:§\s*)?(\d+(?:\.\d+)*)\.?\s+(.*)$", raw)
    num, title = (nm.group(1), nm.group(2)) if nm else (None, raw)
    title = title.strip().strip("`")
    if title.lower() in SPEC_SKIP or title.lower() in plan_l:
        return True
    return bool(num and re.search(rf"(?:§|section\s+|sec\.\s*)\s*{re.escape(num)}\b", plan_l))


def lint_plan(plan: Plan, args) -> tuple[list, list[str]]:  # O(n^2*k*s + e*n + C*P + n*p + merge/simulate) total
    run = _Lint(plan, args)
    run.headings()
    run.placeholders()
    run.header_notes()
    run.owns()
    run.interfaces()
    run.examples_and_diagrams()
    coef = run.size_caps()
    run.schedule(coef)
    run.models()
    run.spec()
    return run.issues, run.warnings


def _iface_str(i: dict) -> str:
    return f"Produces: {', '.join(i.get('produces') or []) or '-'}; Consumes: {', '.join(i.get('consumes') or []) or '-'}"


def _frozen_line(t: str, plan: Plan, old: dict, sent: set, run_dir: Path, interfaces: dict) -> str | None:
    """The FROZEN line for a dispatched task whose block or interface moved, else None."""
    old_if = old.get("interfaces") or {}
    if t in sent and t in old_if and t not in plan.by_id:  # a rewrite dropped a dispatched block
        return f"FROZEN: {t} dispatched block was removed or renumbered; restore it unchanged"
    if t not in old_if or t not in plan.by_id:
        return None
    new_i = task_interface(plan.by_id[t])
    old_hash = (old.get("blocks") or {}).get(t)
    if new_i == old_if[t]:
        if t in sent and old_hash and block_hash(plan.by_id[t]) != old_hash:
            return f"FROZEN: {t} block changed after dispatch; restore it unchanged (a rewrite must keep dispatched blocks identical)"
        return None
    if (run_dir / "briefs" / "wave0.md").is_file():  # Wave 0 already sent: stubs may be stale
        print(f"STUB UPDATE: {t}: {_iface_str(old_if[t])} -> {_iface_str(new_i)}")
    interfaces[t] = old_if[t]  # keep the old entry
    return f"FROZEN: {t} interface changed after dispatch: {_iface_str(old_if[t])} -> {_iface_str(new_i)}; add a follow-up fix task"


def _freeze(plan: Plan, old: dict, sent: set, run_dir: Path, stable: list, fix: list, interfaces: dict):
    """Dispatched tasks keep the interface and block they were sent with. Returns (FROZEN lines, stable, fix)."""
    frozen: list[str] = []
    for t in sorted(set(old.get("stable") or []) | sent, key=nkey):
        line = _frozen_line(t, plan, old, sent, run_dir, interfaces)
        if line is None:
            continue
        frozen.append(line)
        if t in plan.by_id and t not in stable:
            stable.append(t)
            fix = [f for f in fix if f["task"] != t]
    return frozen, stable, fix


def _tiers(plan: Plan, args, run_dir: Path, stable: list) -> tuple[dict, list, str]:
    """(tier per stable task, critical path, the stats and prediction line). Tiers never block a lint."""
    try:
        import history
        import routing
        rep = schedule_report(plan, args)
        stats = history.project_stats(run_dir)
        gated: list[str] = []
        all_tiers = routing.compute_tiers(plan, rep["critical_path"], rep["times"], None, stats, gated)
    except base.advisory_errors() as e:  # logged, the lint goes on
        print(f"routing: tiers not computed ({e})", file=sys.stderr)
        return {}, [], ""
    tiers = {t: v for t, v in all_tiers.items() if t in stable}
    line = f"stats: n={stats.get('rows', 0)}" + (f" (history gate kept {' '.join(sorted(set(gated)))} off haiku)" if gated else "")
    pred = f"predicted: {rep['total_min']:.1f} min"
    try:  # cost from the predictor (history-refit coefficients when enough rows)
        import predict
        pred += f", ${predict.predict_plan(plan, predict.load_models(), predict.load_coef())['totals']['cost_usd']['total']:.2f}"
    except base.advisory_errors() as e:  # the cost is advisory: logged, the line goes without it
        print(f"predict: cost not computed ({e})", file=sys.stderr)
    pred += " (history-backed)" if stats.get("rows", 0) >= 30 else " (shipped coefficients)"
    return tiers, list(rep["critical_path"]), line + "; " + pred


def _wave0_line(plan_path: str, run_dir: Path, pending: list, sent: set) -> list[str]:
    """The skeleton brief comes with the first lint: one command, nothing to author. Never blocks the lint."""
    if not pending or (run_dir / "briefs" / "wave0.md").is_file() or "T0" in sent:
        return []
    try:
        import wave0
        line, tid, _, _ = wave0.write_brief(plan_path)
    except base.advisory_errors() as e:  # logged
        print(f"wave0: brief not written ({e})", file=sys.stderr)
        return []
    return [line] if tid not in pending and tid not in sent else []


def _graph_lines(plan_path: str, run_dir: Path, critical: list) -> list[str]:  # O(L + n+e)
    """The framed mermaid flowchart with lint's critical path marked, only when it differs from the last one
    shown (graph-shown.txt). Never blocks the lint."""
    try:
        import graph
        body = graph.render(plan_path, critical).split("\n", 1)[1]
        shown = run_dir / "graph-shown.txt"
        if shown.is_file() and shown.read_text(encoding="utf-8") == body:
            return []
        shown.write_text(body, encoding="utf-8", newline="\n")
    except base.advisory_errors() as e:  # logged, the lint goes on
        print(f"graph: not drawn ({e})", file=sys.stderr)
        return []
    return ["GRAPH (show the user as a mermaid block):", *body.splitlines(), "END GRAPH"]


def _print_report(args, run_dir: Path, stable: list, fix: list, plan_issues: list, warnings: list, pending: list,
                  first: list, tiers: dict, tiers_line: str) -> None:
    print("STABLE: " + " ".join(stable))
    print("FIX:")
    for f in fix:
        print(f"  {f['task']}: " + " | ".join(f["reasons"]))
    for pi in plan_issues:
        print(f"  PLAN: {pi}")
    for w in warnings:
        if w.startswith(("NOTE: ", "WARNING", "EDGE: ")):
            print(w)
    for line in first:
        print("START FIRST (Wave 0 skeleton, T0): " + line)
    sendable = (["T0"] if first else []) + pending
    if pending and fix:
        print("SEND BEFORE ANY PLAN EDIT: " + " ".join(sendable)
              + f" (send them in this turn; only then patch {' '.join(f['task'] for f in fix)})")
    if pending:  # the progress line, ready to run in the same message as the Agent calls
        print('THEN LOG (same message): echo "- unblocked: dispatched ' + " ".join(sendable)
              + ' at $(date +%H:%M:%S); waiting on fixes: ' + (" ".join(f["task"] for f in fix) or "none")
              + f'" >> {run_dir.name}/progress.md')
    try:  # run-time control line, only when something changed and metrics exist
        import budget
        budget.maybe_print(args.plan, str(run_dir))
    except base.advisory_errors() as e:  # advisory line: logged
        print(f"budget: control line skipped ({e})", file=sys.stderr)
    if tiers:
        print(tiers_line)
        print("TIERS: " + " ".join(f"{t}={tiers[t]}" for t in stable if t in tiers))


def cmd_lint_stable(args) -> int:  # O(lint + n*k*s)
    bad = overlay_rc(".")  # a bad superlite.json fails closed
    if bad:
        return bad
    import orchestrator  # human mode: no lint --stable (and so no dispatch) before approval
    blocked = orchestrator.require(args.plan, "lint --stable")
    if blocked:
        return blocked
    plan = Plan.load(args.plan)
    issues, warnings = lint_plan(plan, args)
    stable, fix, plan_issues = stable_partition(plan, issues, warnings)
    run_dir = pt.run_dir(args.plan)
    out = run_dir / "stable.json"
    interfaces = {t: task_interface(plan.by_id[t]) for t in stable if t in plan.by_id}
    old = base.read_json(out, {})
    sent = dispatched_tasks(run_dir)
    frozen, stable, fix = _freeze(plan, old, sent, run_dir, stable, fix, interfaces)
    stable.sort(key=nkey)
    old_blocks = old.get("blocks") or {}
    blocks = {t: (old_blocks[t] if t in sent and t in old_blocks else block_hash(plan.by_id[t]))
              for t in stable if t in plan.by_id}
    pending = [t for t in stable if t not in sent]
    tiers, cp_ids, tiers_line = _tiers(plan, args, run_dir, stable)
    plan_issues = list(plan_issues) + frozen
    first = _wave0_line(args.plan, run_dir, pending, sent)
    for line in _graph_lines(args.plan, run_dir, cp_ids):
        print(line)
    _print_report(args, run_dir, stable, fix, plan_issues, warnings, pending, first, tiers, tiers_line)
    accepted = [w[len("NOTE: "):] for w in warnings if w.startswith("NOTE: accepted Ruling: sequential")]  # for finish
    base.write_json(run_dir / "chain-rulings.json", accepted)
    base.write_json(out, {"stable": stable, "fix": fix, "plan_issues": plan_issues, "interfaces": interfaces,
                          "blocks": blocks, "tiers": tiers, "critical_path": cp_ids})
    entry = {"time": datetime.now().isoformat(timespec="seconds"), "stable": len(stable), "fix": len(fix),
             "stable_ids": stable, "newly_stable": [t for t in stable if t not in set(old.get("stable") or [])],
             "dispatched": sorted(sent & set(stable), key=nkey), "pending": pending, "frozen": len(frozen)}
    with (run_dir / "lint-log.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    if stable and not frozen:  # the fixed forecast `report` compares against, written once
        _forecast_card(args.plan, run_dir, stable, fix)
    print("DISPATCH NOW: " + (" ".join(pending) if pending else "none"))  # always the last line
    return 0 if stable and not frozen else 1


def _save_projection(plan_path: str, run_dir: Path) -> Path | None:
    try:
        import report
        return report.save_projection(plan_path, run_dir)
    except base.advisory_errors() as e:  # never blocks a lint: logged
        print(f"report: projection not saved ({e})", file=sys.stderr)
        return None


def _forecast_card(plan_path: str, run_dir: Path, stable: list, fix: list) -> None:  # O(n*(n + e)) once, O(1) after
    """Save the fixed forecast; on the lint that first saves it, write the before card and print its `widget:` line."""
    if _save_projection(plan_path, run_dir) is None:
        return
    try:
        import run_widget
        out = run_widget.write(run_dir, plan_path, before=True, stable={"stable": stable, "fix": fix})
    except base.advisory_errors() as e:  # the card is extra: never blocks a lint
        print(f"run_widget: before card not written ({e})", file=sys.stderr)
        return
    if out:
        print(run_widget.widget_line(out))


def cmd_lint(args) -> int:  # O(lint) total
    if getattr(args, "stable", False):
        return cmd_lint_stable(args)
    bad = overlay_rc(".")  # a bad superlite.json fails closed
    if bad:
        return bad
    issues, warnings = lint_plan(Plan.load(args.plan), args)
    for w in warnings:
        print(w)
    for i in issues:
        print(f"ISSUE: {i}")
    if issues:
        print(f"lint: {len(issues)} issue(s)")
        return 1
    print("lint: ok")
    return 0
