#!/usr/bin/env python3
"""Run cards: the forecast right before a build and the metrics right after, each one self-contained HTML fragment.

    run_widget.py [--dir .superlite] [--plan PLAN.md] [--before] [--out FILE]

After (default): `<dir>/report.json` (written by `report.write_report`), the rulings in `<dir>/progress.md`, the hidden
checkpoints in `<dir>/checkpoints.json` and the orchestrator mode: time and cost, tasks and tests, cost by model,
rulings and checkpoint state. Before (--before, needs --plan): `<dir>/projection.json` (saved by the first
`plan_tools lint --stable`), the stable and fix tasks from `<dir>/stable.json` and the plan's `**Budget-usd:**` cap:
projected time and cost against the cap, agents and peak width, the critical path, projected cost by model. Both are
for an inline widget tool such as `show_widget` and share one look: a fragment only (no <html> or <body>), no scripts,
no external loads, host CSS variables for every colour, every number rounded and every value HTML-escaped; n/a where a
value is missing. With no report.json (after) or projection.json (before) it prints nothing and exits 0.
Deterministic. Stdlib only.
"""
import argparse
import html
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import base  # noqa: E402
import finish  # noqa: E402
import orchestrator  # noqa: E402
import state  # noqa: E402

AFTER = "run-widget-after.html"
BEFORE = "run-widget-before.html"
MAX_RULINGS = 5  # the rest are in the finish summary
MAX_PATH = 8  # critical path ids shown before "+N more"
NA = "n/a"
MODEL_FILL = "var(--text-accent)"
CONTEXT_FILL = "var(--text-muted)"
CENT = 0.005  # a forecast gap smaller than half a cent gets no "other" bar


def esc(v) -> str:
    return html.escape(str(v), quote=True)


def is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)  # 1e309 in json is inf


def obj(v) -> dict:
    """The value when it is a dict, else {}: a malformed json field reads n/a instead of failing."""
    return v if isinstance(v, dict) else {}


def num(v, fmt, prefix="", suffix="") -> str:
    if not is_num(v):
        return NA
    return f"{prefix}{format(round(v) if fmt.endswith('d') else v, fmt)}{suffix}"


def diff(proj, act, fmt) -> str:
    """`proj 12.0 · +2.2`; n/a where either side is missing."""
    if not is_num(proj) or not is_num(act):
        return f"proj {num(proj, fmt)}"
    return f"proj {num(proj, fmt)} · {num(act - proj, '+' + fmt)}"


def plural(n, word) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def tile(label, value, sub) -> str:
    return ('<div style="background: var(--surface-1); border-radius: var(--radius); padding: 12px 14px; min-width: 0;">'
            f'<div style="font-size: 13px; color: var(--text-secondary);">{esc(label)}</div>'
            f'<div style="font-size: 22px; font-weight: 500; color: var(--text-primary); line-height: 1.3;">{esc(value)}</div>'
            f'<div style="font-size: 12px; color: var(--text-muted);">{esc(sub)}</div></div>')


def tiles(items) -> str:  # O(k)
    return ('<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; margin: 12px 0 16px;">'
            + "".join(tile(*t) for t in items) + "</div>")


def section(title, rows) -> str:  # O(k)
    return (f'<div style="font-size: 14px; font-weight: 500; color: var(--text-primary); margin: 0 0 6px;">{esc(title)}</div>'
            f'<div style="font-size: 13px; color: var(--text-secondary); line-height: 1.7; margin: 0 0 14px;">{"".join(rows)}</div>')


def line(*parts) -> str:  # O(k)
    return "<div>" + " &nbsp;·&nbsp; ".join(esc(p) for p in parts if p) + "</div>"


def bar(name, usd, scale, tasks, fill) -> str:
    pct = round(100 * usd / scale) if scale else 0
    count = plural(tasks, "task") if tasks is not None else ""
    return (f'<div data-bar="{esc(name)}" style="display: grid; grid-template-columns: minmax(0, 7rem) minmax(0, 1fr) 4rem 4rem; '
            'gap: 8px; align-items: center; margin: 4px 0;">'
            f'<span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">{esc(name)}</span>'
            '<span style="height: 8px; border-radius: 4px; background: var(--border); overflow: hidden;">'
            f'<span style="display: block; height: 100%; width: {pct}%; background: {fill}; border-radius: 4px;"></span></span>'
            f'<span style="text-align: right; color: var(--text-primary);">{esc(num(usd, ".2f", "$"))}</span>'
            f'<span style="color: var(--text-muted);">{esc(count)}</span></div>')


def bars(rows) -> list[str]:  # O(m)
    """rows: (name, usd, tasks or None, fill), drawn on one shared scale."""
    if not rows:
        return [f"<div>{NA}</div>"]
    scale = max(r[1] for r in rows)
    return [bar(n, usd, scale, t, fill) for n, usd, t, fill in rows]


def by_cost(mix) -> list:  # O(m log m)
    """(model, row) most expensive first; rows without a numeric cost_usd are left out."""
    rows = [(str(m), v) for m, v in mix.items() if isinstance(v, dict) and is_num(v.get("cost_usd"))]
    return sorted(rows, key=lambda x: (-x[1]["cost_usd"], x[0]))


def card(summary, head, body) -> str:  # O(k)
    """The shared frame: a visually hidden one-line summary first, then the raised card."""
    return ('<div style="padding: 1rem 0;">'
            '<h2 class="sr-only" style="position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); '
            f'white-space: nowrap;">{esc(summary)}</h2>'
            '<div style="background: var(--surface-2); border: 0.5px solid var(--border); border-radius: 12px; padding: 1rem 1.25rem;">'
            f'<div style="font-size: 14px; color: var(--text-secondary);">{esc(head)}</div>'
            + "".join(body) + "</div></div>\n")


# ---- after the run


def quality_rows(rep) -> list[str]:  # O(1)
    q, tasks = obj(rep.get("quality")), rep.get("tasks")
    tasks = tasks if isinstance(tasks, list) else []
    t = obj(q.get("tests"))
    n = q.get("first_time_n")
    ftp = f"{q.get('first_time_pass', 0)}/{n}" if n else NA
    rows = [line(f"tasks {len(tasks)}", f"first-time pass {ftp}", f"rework {num(q.get('rework_dispatches'), 'd')}"),
            line(f"tests {num(t.get('passed'), 'd')} passed, {num(t.get('failed'), 'd')} failed",
                 f"open incidents {num(q.get('open_incidents'), 'd')}")]
    f = rep.get("feature")
    if isinstance(f, dict):
        proto, integ = (obj(obj(f.get(k)).get("wall_min")) for k in ("prototype", "integration"))
        rows.append(line(f"prototype {num(proto.get('actual'), '.1f')} min", f"integration {num(integ.get('actual'), '.1f')} min"))
    return rows


def actual_model_rows(rep) -> list[str]:  # O(m log m)
    """One bar per model in model_mix, most expensive first, then the coordinator's share when known."""
    rows = [(m, v["cost_usd"], v.get("tasks"), MODEL_FILL) for m, v in by_cost(obj(rep.get("model_mix")))]
    coord = obj(rep.get("cost_split")).get("coordinator_usd")
    if is_num(coord):
        rows.append(("coordinator", coord, None, CONTEXT_FILL))
    return bars(rows)


def ruling_rows(rulings, checkpoints) -> list[str]:  # O(R)
    scope = finish.scope_rulings(rulings)
    saved = (f"{plural(checkpoints, 'checkpoint')} taken; work uncommitted in the folder, branch untouched" if checkpoints
             else "no checkpoint state recorded")
    rows = [line(f"rulings {len(rulings)}", f"scope changes {len(scope)}", saved)]
    shown = [f"<li>{esc(r)}</li>" for r in rulings[:MAX_RULINGS]]
    if len(rulings) > MAX_RULINGS:
        shown.append(f"<li>+{len(rulings) - MAX_RULINGS} more in the finish summary</li>")
    if shown:
        rows.append('<ul style="margin: 4px 0 0; padding-left: 1.2rem;">' + "".join(shown) + "</ul>")
    return rows


def render(rep, rulings, checkpoints, title="", mode="") -> str:  # O(m log m + R)
    """The after card for one report.json dict, the ledger's rulings and the hidden checkpoint count."""
    h, cs = obj(rep.get("headline")), obj(rep.get("cost_split"))
    w, k, a = (obj(h.get(x)) for x in ("wall_min", "cost_usd_agents", "agents"))
    summary = (f"superlite run: {num(w.get('actual'), '.1f')} minutes, total cost {num(cs.get('total_usd'), '.2f', '$')}, "
               f"{plural(len(rulings), 'ruling')}")
    head = " · ".join(p for p in ("superlite run", title, mode) if p)
    return card(summary, head, [
        tiles([("Wall time", num(w.get("actual"), ".1f", suffix=" min"), diff(w.get("projected"), w.get("actual"), ".1f")),
               ("Agent cost", num(k.get("actual"), ".2f", "$"), diff(k.get("projected"), k.get("actual"), ".2f")),
               ("Total cost", num(cs.get("total_usd"), ".2f", "$"), "coord " + num(cs.get("coordinator_usd"), ".2f", "$")),
               ("Agents", num(a.get("actual"), "d"), diff(a.get("projected"), a.get("actual"), "d"))]),
        section("Tasks and tests", quality_rows(rep)),
        section("Cost by model", actual_model_rows(rep)),
        section("Rulings and checkpoints", ruling_rows(rulings, checkpoints))])


# ---- before the run


def cap_sub(cost, cap) -> str:
    if not is_num(cap):
        return "no Budget-usd cap"
    if not is_num(cost) or not cap:
        return f"cap ${cap:.2f}"
    return f"{round(100 * cost / cap)}% of ${cap:.2f} cap"


def projected_model_rows(proj) -> list[str]:  # O(n + m log m)
    """Projected cost per model, summed from the per-task forecast; an "other" bar for the part of the headline cost
    no task owns, so the bars add up to the headline."""
    mix: dict[str, dict] = {}
    for t in obj(proj.get("tasks")).values():
        t = obj(t)
        m = mix.setdefault(str(t.get("model") or "unknown"), {"tasks": 0, "cost_usd": 0.0})
        m["tasks"] += 1
        m["cost_usd"] += t.get("cost_usd") if is_num(t.get("cost_usd")) else 0.0
    rows = [(m, v["cost_usd"], v["tasks"], MODEL_FILL) for m, v in by_cost(mix)]
    total = proj.get("cost_usd")
    gap = total - sum(r[1] for r in rows) if is_num(total) else 0.0
    if gap >= CENT:
        rows.append(("other", gap, None, CONTEXT_FILL))
    return bars(rows)


def path_rows(proj, stable) -> list[str]:  # O(n)
    cp = proj.get("critical_path")
    cp = [str(t) for t in cp] if isinstance(cp, list) else []
    shown = " > ".join(cp[:MAX_PATH]) + (f" +{len(cp) - MAX_PATH} more" if len(cp) > MAX_PATH else "")
    rows = [line(f"tasks {len(obj(proj.get('tasks')))}", f"task-minutes {num(proj.get('sum_task_min'), '.1f')}",
                 f"peak {num(proj.get('peak_width'), 'd')} at once"),
            line(f"critical path {shown or NA}", f"{num(proj.get('critical_path_min'), '.1f')} min")]
    if isinstance(stable, dict):
        n = [len(v) if isinstance(v, list) else 0 for v in (stable.get("stable"), stable.get("fix"))]
        rows.append(line(f"stable {n[0]}", f"fix first {n[1]}"))
    return rows


def render_before(proj, stable=None, cap=None, title="", mode="") -> str:  # O(n + m log m)
    """The before card for one projection.json dict, the lint's stable and fix lists and the plan's Budget-usd cap."""
    cost = proj.get("cost_usd")
    summary = (f"superlite run forecast: {num(proj.get('wall_min'), '.1f')} minutes, agent cost {num(cost, '.2f', '$')}, "
               f"{plural(len(obj(proj.get('tasks'))), 'task')}")
    head = " · ".join(p for p in ("superlite run forecast", title, mode) if p)
    return card(summary, head, [
        tiles([("Wall time", num(proj.get("wall_min"), ".1f", suffix=" min"), "critical path, build only"),
               ("Agent cost", num(cost, ".2f", "$"), cap_sub(cost, cap)),
               ("Budget cap", num(cap, ".2f", "$"), "Budget-usd in the plan"),
               ("Agents", num(proj.get("agents"), "d"), f"peak {num(proj.get('peak_width'), 'd')} at once")]),
        section("Tasks and critical path", path_rows(proj, stable)),
        section("Projected cost by model", projected_model_rows(proj))])


# ---- files


def mode_label(sl_dir) -> str:  # O(file)
    st = orchestrator.read_state(sl_dir)
    if not st:
        return ""
    return "AI orchestrator" if st["mode"] == "ai" else "human orchestrator"


def budget_cap(plan) -> float | None:  # O(C)
    try:
        import budget
        import plan_tools
        return budget.header_budget(plan_tools.Plan.load(str(plan)))
    except base.advisory_errors():
        return None


def build(sl_dir, plan=None, ledger=None) -> str:  # O(file + R*w + m log m)
    """The after card for a run folder; "" when it has no report.json. Rulings come from `ledger` (default
    `<dir>/progress.md`)."""
    sl = Path(sl_dir)
    rep = base.read_json(sl / "report.json", None)
    if not isinstance(rep, dict):
        return ""
    rulings, _, _ = finish.read_ledger(ledger or sl / "progress.md")
    cps = base.read_json(sl / "checkpoints.json", {})
    entries = obj(cps).get("entries")
    checkpoints = len(entries) if isinstance(entries, list) else 0
    feature = obj(rep.get("feature")).get("topic")
    title = f"feature: {feature}" if feature else Path(plan).stem if plan else ""
    return render(rep, rulings, checkpoints, title, mode_label(sl))


def build_before(sl_dir, plan, stable=None) -> str:  # O(file + C + n + m log m)
    """The before card for a run folder and its plan; "" when it has no projection.json. `stable` ({stable, fix})
    defaults to the folder's stable.json."""
    sl = Path(sl_dir)
    proj = base.read_json(sl / "projection.json", None)
    if not isinstance(proj, dict):
        return ""
    if stable is None:
        stable = base.read_json(sl / "stable.json", None)
    return render_before(proj, stable, budget_cap(plan), Path(plan).stem, mode_label(sl))


def write(sl_dir, plan=None, before=False, stable=None, ledger=None) -> Path | None:  # O(file + R*w + n + m log m)
    """Write `<dir>/run-widget-after.html` (or `-before.html`); None (and no file) when its source is missing."""
    text = build_before(sl_dir, plan, stable) if before else build(sl_dir, plan, ledger)
    if not text:
        return None
    out = Path(sl_dir) / (BEFORE if before else AFTER)
    out.write_text(text, encoding="utf-8")
    return out


def widget_line(path, root=None) -> str:
    """The line a tool prints after writing a card; the coordinator shows the file when it has a widget tool. Without
    `root` the path is `<run dir>/<file>`, as lint's THEN LOG line names the ledger, so the bytes never depend on the
    working directory. With `root` it is the full path from there, or absolute when the card is outside it."""
    p = Path(path)
    shown = f"{p.parent.name}/{p.name}"
    if root is not None:
        full = p.resolve()
        shown = full.relative_to(Path(root).resolve()).as_posix() if full.is_relative_to(Path(root).resolve()) else str(full)
    return f"widget: {shown} (show it with an inline widget tool if you have one, e.g. show_widget; else skip)"


def main(argv=None):  # O(file + R*w + n + m log m)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dir", default=state.DIR_NAME, help=f"the {state.DIR_NAME} folder itself")
    ap.add_argument("--plan", help="the build's plan; its name titles the card (needed with --before)")
    ap.add_argument("--before", action="store_true", help="the forecast card from projection.json instead of the report")
    ap.add_argument("--out", help="write the card to this file instead of printing it")
    a = ap.parse_args(argv)
    if a.before and not a.plan:
        ap.error("--before needs --plan")
    sys.stdout.reconfigure(encoding="utf-8")
    text = build_before(a.dir, a.plan) if a.before else build(a.dir, a.plan)
    if not text:
        return 0
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        return 0
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
