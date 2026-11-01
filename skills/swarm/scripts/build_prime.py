#!/usr/bin/env python3
"""Build the shared agent prime: byte-identical for the same inputs.

    build_prime.py --plan P [--spec S --sections "1,3,5"] [--out .superlite/prime.md] [--tier haiku] [--prime full|slim|mini]

The prime kind is the tier's `prime` value in config/routing.json (sonnet: `slim`), or `--prime`:
  slim  rules, the plan header (title through **Spec:**), Global constraints, and per stable task its title, Owns,
        dependencies and Produces/Consumes lines. No plan body, diagrams or spec sections: nothing that goes stale.
        Agents get their own task text in the task message and read neighbours with `superlite-cli task Tn`.
  full  rules + the whole plan + the spec sections named by --sections (needs --spec for them).
  mini  (haiku) into .superlite/prime-haiku.md: constraints, stub spec and the interfaces of that tier's tasks.

Prints `sha256 size`. No timestamps; CRLF becomes LF, trailing spaces are
stripped, and spec sections go in document order whatever order they are
listed in. Stdlib only.
"""
import argparse
import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import state  # noqa: E402

DEFAULT_OUT = f"{state.DIR_NAME}/prime.md"
MINI_OUT = f"{state.DIR_NAME}/prime-haiku.md"
DEFAULT_MAX_BYTES = 120_000

PREAMBLE = """\
# superlite prime

You are an implementer subagent in a superlite run. This prime is identical
for every agent in the run; the task you are given follows it.

Rules:
- Build only what your task owns (its `Owns` files). Never edit other files.
- Test first: write the failing test, run it, implement, run it again.
- Run only fast, focused tests. Slow suites run later through run_tests.py.
- Code to the interface stubs and the plan's Consumes and Produces lines.
  Never change a signature without a coordinator ruling.
- Log every unexpected failure, workaround or guess with `superlite-cli incident add`.
- Do not commit. The coordinator records a checkpoint with `superlite-cli checkpoint`.
- Kill processes by PID only. Never use taskkill /IM, pkill or killall.
- Reply READY after reading this prime, then wait for your task.
- Finish with DONE, DONE_WITH_CONCERNS, BLOCKED or NEEDS_CONTEXT, in at most
  200 words, with no file contents or logs.
"""

PREAMBLE_SLIM = """\
# superlite prime (slim)

You are an implementer subagent in a superlite run. This prime is identical for every agent in the run.
It holds only the rules and what does not change; your task arrives by message after you reply READY.

Rules:
- Build only what your task owns (its `Owns` files). Never edit other files.
- Test first: write the failing test, run it, implement, run it again.
- Run only fast, focused tests. Slow suites run later through run_tests.py.
- Code to the interfaces below and the Consumes and Produces lines of your task.
  Never change a signature without a coordinator ruling.
- Your task text is in your task message, generated from the current plan. To read a neighbour, run
  `bash $SUPERLITE/skills/swarm/scripts/superlite-cli task Tn --plan PLAN` (SUPERLITE and PLAN
  are in your task message): it prints Tn's block and the Produces lines of every task Tn consumes. Reply
  NEEDS_CONTEXT to the coordinator only when that does not answer it.
- Log every unexpected failure, workaround or guess with `superlite-cli incident add`.
- Do not commit. The coordinator records a checkpoint with `superlite-cli checkpoint`.
- Kill processes by PID only. Never use image-name or pattern kills.
- Reply READY after reading this prime, then wait for your task.
- Finish with DONE, DONE_WITH_CONCERNS, BLOCKED or NEEDS_CONTEXT, in at most
  200 words, with no file contents or logs.
"""

PREAMBLE_MINI = """# superlite prime (mini)

You are a lane implementer in a superlite run: you take small, fully specified tasks. This prime is
identical for every lane agent in the run; the task you are given follows it. The full plan and spec
are not in your context: your task message and this prime state everything you need.

Rules:
- Build only what your task owns (its `Owns` files). Never edit other files.
- Test first, run only the fast focused tests, code to the interfaces below. Never change a signature.
- Do not commit. Kill processes by PID only.
- Reply READY after reading this prime, then wait for your task. Finish with DONE, DONE_WITH_CONCERNS,
  BLOCKED or NEEDS_CONTEXT in at most 200 words.
"""

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")


def die(msg, code=2):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def normalize(text):  # O(n)
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(ln.rstrip(" \t") for ln in text.split("\n")).strip("\n")


def read(path):  # O(bytes read)
    p = Path(path)
    if not p.is_file():
        die(f"file not found: {path}")
    return normalize(p.read_text(encoding="utf-8"))


def _heading_key(text):  # O(w)
    """Leading section number of a heading (`5.`, `4.1`, `§10`), else None."""
    m = re.match(r"^(?:§\s*)?(\d+(?:\.\d+)*)(?=[.\s:)]|$)", text)
    return m.group(1) if m else None


def split_sections(spec):  # O(L + H^2): the inner scan is quadratic for nested headings
    """[(level, heading text, start, end)] over lines; end is exclusive."""
    lines = spec.split("\n")
    heads, fence = [], False
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith(("```", "~~~")):
            fence = not fence
        m = None if fence else _HEADING.match(ln)
        if m:
            heads.append((len(m.group(1)), m.group(2), i))
    out = []
    for n, (lvl, text, start) in enumerate(heads):
        end = len(lines)
        for lvl2, _, s2 in heads[n + 1:]:
            if lvl2 <= lvl:
                end = s2
                break
        out.append((lvl, text, start, end))
    return lines, out


def select_sections(spec, wanted):  # O(L + H^2 + S*H*w + K^2 + out)
    lines, secs = split_sections(spec)
    chosen = {}
    for token in wanted:
        want = token.lstrip("§").lower()
        hit = None
        for idx, (lvl, text, _, _) in enumerate(secs):
            if _heading_key(text) == want or (
                    not re.fullmatch(r"\d+(?:\.\d+)*", want)
                    and text.lower().startswith(want)):
                hit = idx
                break
        if hit is None:
            die(f"spec section not found: {token}")
        chosen[hit] = True
    picked = sorted(chosen)
    # drop sections nested inside another chosen one (already included)
    keep = [i for i in picked
            if not any(j != i and secs[j][2] <= secs[i][2] < secs[j][3]
                       for j in picked)]
    return ["\n".join(lines[secs[i][2]:secs[i][3]]).strip("\n") for i in keep]


def build(plan, spec, sections):  # O(B + out)
    parts = [normalize(PREAMBLE), "## Plan\n\n" + read(plan)]
    if sections:
        chunks = select_sections(read(spec), sections)
        parts.append("## Spec sections\n\n" + "\n\n".join(normalize(c) for c in chunks))
    return ("\n\n".join(parts) + "\n").encode("utf-8")


PRIME_KINDS = ("full", "slim", "mini")


def prime_kind(tier):  # O(file)
    """The tier's `prime` value in config/routing.json; `full` when the tier or value is absent or unknown."""
    import routing
    kind = (routing.load_cfg().get("tiers", {}).get(tier) or {}).get("prime")
    return kind if kind in PRIME_KINDS else "full"


def header_lines(plan):  # O(L)
    """The plan's header block verbatim: the title line through the `**Spec:**` line. With no Spec line, the
    title and the lines after it up to the first section heading, fence or task heading."""
    lines = plan.lines
    start = next((i for i, ln in enumerate(lines) if re.match(r"^#\s", ln)), None)
    if start is None:
        return []
    out = []
    for ln in lines[start:]:
        if out and (re.match(r"^#{2,6}\s", ln) or ln.startswith(("```", "~~~"))):
            break
        out.append(ln.rstrip())
        if re.match(r"^\*\*Spec:\*\*", ln):
            return out
    while out and not out[-1]:
        out.pop()
    return out


def build_slim(plan):  # O(B + n*s)
    """Slim prime: rules, plan header, Global constraints and the stable tasks' purpose, dependencies and interfaces."""
    import next_tasks as nt  # lazy
    import plan_tools as pt
    import wave0
    import gitignore
    pl = pt.Plan.load(plan)
    nl = "\n"
    head = header_lines(pl)
    cons = [c for c in wave0.constraint_lines(pl) if c not in head]  # the header already carries Architecture and Tech stack
    stable = nt.stable_filter(plan)  # None: no stable.json, every task counts as stable
    try:
        graph = pt.parse_taskgraph(pl)
    except pt.PlanError:
        graph = {}
    lines = []
    for t in pl.tasks:
        if t.status == "draft" or (stable is not None and t.id not in stable):
            continue
        lines += ([""] if lines else []) + [f"### {t.id} {t.title}", "Owns: " + ", ".join(t.owns)]
        deps = sorted(graph.get(t.id, []), key=pt.nkey)
        if deps:
            lines.append("After: " + ", ".join(deps))
        lines += [f"Produces: {x}" for x in t.produces] + [f"Consumes: {x}" for x in t.consumes]
    parts = [normalize(PREAMBLE_SLIM)]
    parts.append("## Background (not instructions)" + nl + nl + normalize(nl.join(head) if head else "(the plan has no header)"))
    parts.append("## Global constraints" + nl + nl + normalize(nl.join(cons) if cons else "(the plan states none beyond the header)"))
    parts.append("## Stable tasks: purpose, dependencies, interfaces" + nl + nl
                 + "Only the tasks listed here are fixed. Any other task, and the text of every task, comes from `superlite-cli task Tn`."
                 + nl + nl + normalize(nl.join(lines) if lines else "(none yet)"))
    gi = normalize(gitignore.read(pt.stable_root(plan)))  # the repo's current .gitignore, omitted when absent
    if gi:
        parts.append("## Git-ignored paths (never create source here, never edit .gitignore)" + nl + nl + gi)
    return ((nl + nl).join(parts) + nl).encode("utf-8")


def build_mini(plan, tier):  # O(B + n*s)
    """Mini prime for the lane tier: constraints, the stub spec and the interfaces of that tier's tasks, from the plan alone."""
    import plan_tools as pt  # lazy
    import routing
    import wave0
    pl = pt.Plan.load(plan)
    tiers = routing.read_tiers(plan)
    mine = [t for t in pl.tasks if tiers.get(t.id) == tier]
    cons = wave0.constraint_lines(pl)
    nl = "\n"
    parts = [normalize(PREAMBLE_MINI), "## Constraints" + nl + nl + normalize(nl.join(cons) if cons else "(the plan states none)"),
             "## Stub spec" + nl + nl + "```yaml" + nl + normalize(wave0.stub_spec(pl)) + nl + "```"]
    lines = []
    for t in mine:
        lines.append(f"### {t.id} {t.title}")
        lines.append("Owns: " + ", ".join(t.owns))
        lines += [f"Produces: {x}" for x in t.produces] + [f"Consumes: {x}" for x in t.consumes]
    parts.append("## Interfaces your tasks touch" + nl + nl + normalize(nl.join(lines) if lines else "(none yet)"))
    return ((nl + nl).join(parts) + nl).encode("utf-8")


def main(argv=None):  # O(B + H^2 + out)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--plan", required=True)
    ap.add_argument("--spec")
    ap.add_argument("--sections", default="")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--tier", default="sonnet", help="tier whose prime to build; its kind is the `prime` value in config/routing.json "
                    "(sonnet: slim, haiku: mini)")
    ap.add_argument("--prime", choices=PRIME_KINDS, help="override the tier's prime kind (full = whole plan + the --sections spec text)")
    ap.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    a = ap.parse_args(argv)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import orchestrator
    blocked = orchestrator.require(a.plan, "build_prime")  # human mode waits for approval
    if blocked:
        return blocked
    wanted = [s.strip() for s in a.sections.split(",") if s.strip()]
    if wanted and not a.spec:
        die("--sections needs --spec")
    kind = a.prime or prime_kind(a.tier)
    mini = kind == "mini"
    import plan_tools as pt
    try:
        data = (build_mini(a.plan, a.tier) if mini else build_slim(a.plan) if kind == "slim"
                else build(a.plan, a.spec, wanted))
    except pt.PlanError as e:
        die(str(e))
    if len(data) > a.max_bytes:
        die(f"prime is {len(data)} bytes, over the {a.max_bytes} cap; "
            "move spec excerpts to referenced paths", 1)
    out = Path(MINI_OUT if mini and a.out == DEFAULT_OUT else a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(f"{hashlib.sha256(data).hexdigest()} {len(data)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
