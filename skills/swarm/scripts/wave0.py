#!/usr/bin/env python3
"""Build the Wave 0 (skeleton) brief from the plan alone; the coordinator authors nothing.

    wave0.py --plan PLAN [--out BRIEF]

Writes `briefs/wave0.md` in the run folder (+ `wave0.json`: task id and owned paths) and prints the exact
Agent call line for superlite:implementer-haiku. The brief holds: the plan's Global constraints and
stack lines, the language facts the plan states (or the rule to infer them from the repo's marker
files), the Wave 0 task's Owns (the plan's own mechanical skeleton task, else a synthesized T0),
a language-neutral YAML stub spec from every task's Produces lines, and the done criteria.
Nothing here assumes a language. Stdlib only.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402
import gitignore  # noqa: E402
import plan_tools as pt  # noqa: E402

SUPERLITE = Path(__file__).resolve().parents[3]  # plugin root
MANIFESTS = ("package.json", "pyproject.toml", "go.mod", "Cargo.toml", "Makefile", "pom.xml", "build.gradle", "Gemfile")
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__)/|\.(test|spec)\.|(^|/)test_")
SKELETON = re.compile(r"wave\s*0|skeleton|scaffold|bootstrap", re.I)
LANGS = [("TypeScript", r"typescript|\btsc\b|\.tsx?\b"), ("JavaScript", r"javascript|\bnode(?:\.js)?\b|\.m?jsx?\b"),
         ("Python", r"python|pyproject|pytest|\.py\b"), ("Go", r"\bgolang\b|\bgo\.mod\b|\.go\b"),
         ("Rust", r"\brust\b|cargo|\.rs\b"), ("C#", r"c#|\.net\b|csproj|\.cs\b"), ("Java", r"\bjava\b|\.java\b"),
         ("Ruby", r"\bruby\b|\.rb\b")]
# Root-level and glob paths a synthesized T0 owns (checkpoint matches `*` patterns; files another task owns are left alone).
SYNTH_OWNS = ["package.json", "package-lock.json", "tsconfig*.json", "pyproject.toml", "uv.lock", "go.mod", "go.sum", "Cargo.toml",
              "Cargo.lock", "Makefile", "*.csproj", "*.sln", "pom.xml", "build.gradle", "Gemfile", ".gitignore",
              "jest.config.*", "vitest.config.*", "pytest.ini", "tox.ini", "conftest.py", ".eslintrc*",
              "**/__init__.py", "**/errors.*", "**/types.*", "**/models.*"]


def sl_dir(plan_path: str) -> Path:
    return pt.run_dir(plan_path)


def is_wave0(task) -> bool:  # O(k*s)
    if task.field_text("kind").strip().lower() != "mechanical":
        return False
    owns = task.owns
    return bool(SKELETON.search(task.heading + " " + task.title)
                or any(o.rsplit("/", 1)[-1] in MANIFESTS for o in owns))


def find_wave0(plan):  # O(n)
    for t in plan.tasks:
        if is_wave0(t):
            return t
    return None


def constraint_lines(plan) -> list[str]:  # O(L)
    """Global constraints, Tech stack and Architecture lines as the plan states them, plus a `## Global constraints` section."""
    out, in_sec = [], False
    for ln in plan.lines:
        if re.match(r"^#{1,4}\s+global constraints", ln, re.I):
            in_sec = True
            continue
        if in_sec and re.match(r"^#{1,4}\s", ln):
            in_sec = False
        if in_sec or re.match(r"^(?:\*\*)?(?:Global constraints|Tech stack|Architecture|Stack|Language)\b", ln, re.I):
            if ln.strip():
                out.append(ln.rstrip())
        if ln.startswith("```mermaid") or re.match(r"^###\s+T\d+", ln):  # constraints precede the diagram and tasks
            if out:
                break
    return out


def detect_langs(lines: list[str], plan) -> list[str]:  # O(L)
    """Names from LANGS the plan's stated constraints (else its first 40 lines) mention."""
    stated = " ".join(lines)
    if not stated.strip():
        stated = " ".join(plan.lines[:40])
    return [n for n, rx in LANGS if re.search(rx, stated, re.I)]


def language_facts(lines: list[str], plan) -> str:  # O(L)
    names = detect_langs(lines, plan)
    if names:
        return ("The plan names: " + ", ".join(names) + ". Use the tooling the plan's constraints name; where they are silent, "
                "use the project's own declared tooling found in the repo (package.json, go.mod, Cargo.toml, pyproject.toml, "
                "*.csproj, Makefile).")
    return ("The plan and SPEC name no language. Infer it from the repo's marker files and the project's own declared tooling "
            "(package.json, go.mod, Cargo.toml, pyproject.toml, *.csproj, Makefile) and the file extensions the tasks' Owns lines use. "
            "Do not assume Python.")


def quote(s: str) -> str:  # O(s)
    return json.dumps(s, ensure_ascii=False)  # JSON strings are valid YAML


def seam_names(plan) -> set[str]:  # O(q*s)
    """Names (full and last segment) of every Consumes item: the cross-task seams that need a stub."""
    names: set[str] = set()
    for t in plan.tasks:
        for it in t.consumes:
            sig = pt.parse_signature(it)
            if sig:
                names.update((sig["name"], sig["name"].rsplit(".", 1)[-1]))
            names.add(pt.strip_ticks(it))
    return names


def stub_spec(plan) -> str:  # O(q*s)
    """Language-neutral YAML: per producing task, its files and the signatures another task consumes."""
    out = ["tasks:"]
    seams = seam_names(plan)
    for t in plan.tasks:
        items = [it for it in t.produces
                 if pt.strip_ticks(it) in seams or ((pt.parse_signature(it) or {}).get("name") in seams)]
        if not items:
            continue
        out.append(f"  - id: {t.id}")
        out.append(f"    contract: {quote(t.title or t.id)}")
        out.append(f"    files: [{', '.join(quote(o) for o in t.owns if not TEST_PATH.search(o))}]")
        out.append("    produces:")
        for it in items:
            sig = pt.parse_signature(it)
            if not sig:
                out.append(f"      - {{raw: {quote(it)}}}")
                continue
            params = [p.strip() for p in pt.split_top_level(sig["args"]) if p.strip()]
            out.append(f"      - name: {quote(sig['name'])}")
            out.append(f"        params: [{', '.join(quote(p) for p in params)}]")
            out.append(f"        returns: {quote(sig['ret'] or '')}")
        if t.examples:  # the plan's hand-built examples: what the producer returns
            out.append("    examples:")
            out += [f"      - {quote(ex)}" for ex in t.examples]
    return "\n".join(out) + "\n"


OWNS_SYNTH = ("manifest, package/module initialisers, test-runner config, shared error and data types "
              "(only the files the stub spec below needs to import)")


def build_brief(plan, plan_path: str, task, tid: str) -> str:  # O(L + q*s)
    lines = constraint_lines(plan)
    if task is not None:
        owns_txt = "The plan's own Wave 0 task, verbatim:\n\n" + task.text.strip() + "\n"
    else:
        owns_txt = (f"The plan has no Wave 0 task, so you own: {OWNS_SYNTH}. Project manifest, package/module "
                    "initialisers, test-runner config, and one file of shared error/data types per source directory the stub spec names.\n")
    return f"""# {tid}: Wave 0 skeleton

You are {tid}, a one-off skeleton task. This message is your whole task, not the shared plan: do not reply READY. Plan: `{plan_path}`. Edit only the files you own.

## Global constraints and stack (as the plan states them)

{chr(10).join(lines) if lines else "(the plan states none)"}

## Language and layout

{language_facts(lines, plan)}

## What you own

{owns_txt}
## Stub spec (language-neutral)

Write one stub per `produces` item below (only the signatures another task consumes; an empty list means no stubs) in the project's own language, in the files listed, with exactly these names, parameters and return types (map the types to the language). Each stub fails with a clear not-implemented error naming its task id (never returns a plausible value). Keep it to signatures: no logic. Everything you need is in this brief: read no SPEC or plan section, search nothing, and create the files in as few tool calls as you can. If a listed file already exists, an implementer is building it: leave it untouched, never overwrite.

```yaml
{stub_spec(plan)}```

## Done when

1. The project installs (the manifest is valid and dependencies resolve with the project's own tool).
2. The project's own test command, or its compile/type-check, runs on the stub tree: the tree builds, and any test fails as "not implemented", never as an import, syntax or configuration error.
3. You write and verify `{pt.run_dir(plan_path).name}/test-cmd.json` on the stub tree: `{{"language","test","test_file","junit","setup"}}` (JSON; `test` runs everything, `test_file` takes `{{files}}`, `junit` is the report path or `{{junit}}` for the one run_tests supplies, `setup` installs dependencies). Verify it by running `bash "$CLI" run_tests --full`: it must report the stubs as failing, never "no test command found".
4. `.gitignore` already exists: the coordinator's script wrote it before you started (dependency, build and cache folders for the plan's languages). Do not rewrite it; extend it only if your own tooling creates another generated folder, adding just that entry.
5. Never run `git add`, `commit`, `switch` or `checkout`; the coordinator runs `bash "$CLI" checkpoint {tid} --plan PLAN`.

Reply in at most 200 words: Status (DONE, DONE_WITH_CONCERNS, NEEDS_CONTEXT or BLOCKED), files changed (paths only), test or build command and its result line, concerns.
"""


def synth_info(plan_path: str):
    """{"id", "title", "owns"} of a synthesized T0 recorded by `wave0`, else None."""
    d = base.read_json(sl_dir(plan_path) / "briefs" / "wave0.json", {})
    return d if isinstance(d, dict) and d.get("synthetic") else None


def agent_line(brief: Path, tid: str) -> str:
    prompt = (f"SUPERLITE={SUPERLITE.as_posix()}. This message is your whole task, not the shared plan: do not reply READY. "
              f"Read {brief.as_posix()} and do it. Reply in the implementer report format.")
    return f'Agent(subagent_type="superlite:implementer-haiku", description="{tid} Wave 0 skeleton", prompt={json.dumps(prompt)})'


def write_gitignore(plan, plan_path: str) -> list[str]:  # O(L + g + e) + file I/O
    """Append the missing dependency/build/cache entries for the plan's languages, `common` and its `**Ignore:**` line to the repo's .gitignore."""
    return gitignore.ensure(pt.stable_root(plan_path), detect_langs(constraint_lines(plan), plan), extra=plan.header_ignore(),
                            owned=[o for t in plan.tasks for o in t.owns])


def gitignore_owner(plan_path: str):
    """Task id of the Wave 0 task when the script touched .gitignore (it checkpoints that file), else None."""
    d = base.read_json(sl_dir(plan_path) / "briefs" / "wave0.json", {})
    return d.get("id") if isinstance(d, dict) and d.get("gitignore") else None


def write_brief(plan_path: str, out_path: str | None = None):  # O(L + q*s)
    """Write the brief and its json; (agent line, task id, plan task or None, brief path)."""
    plan = pt.Plan.load(plan_path)
    task = find_wave0(plan)
    tid = task.id if task else "T0"
    added = write_gitignore(plan, plan_path)  # before any agent runs
    out = Path(out_path) if out_path else sl_dir(plan_path) / "briefs" / "wave0.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_brief(plan, plan_path, task, tid), encoding="utf-8", newline="\n")
    prev = gitignore_owner(plan_path)  # an earlier run's edit still sits uncommitted
    meta = {"id": tid, "synthetic": task is None, "title": "Wave 0 skeleton", "owns": SYNTH_OWNS if task is None else task.owns,
            "gitignore": bool(added) or prev == tid, "gitignore_added": added}
    base.write_json(out.with_suffix(".json"), meta)
    return agent_line(out.resolve(), tid), tid, task, out


def main(argv=None):  # O(L + q*s)
    t0 = time.perf_counter()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--plan", required=True)
    ap.add_argument("--out", help="brief path (default briefs/wave0.md in the run folder)")
    a = ap.parse_args(argv)
    import orchestrator
    blocked = orchestrator.require(a.plan, "wave0")  # human mode waits for approval
    if blocked:
        return blocked
    try:
        line, tid, task, out = write_brief(a.plan, a.out)
    except (pt.PlanError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(line)
    print(f"wave0: {tid} brief {out.as_posix()} ({'plan task' if task else 'synthesized'}) in {(time.perf_counter() - t0) * 1000:.0f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
