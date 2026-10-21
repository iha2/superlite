"""`sl wave0`: the Wave 0 brief built from the plan alone, language-neutral; T0 in dispatch and checkpoint."""
from __future__ import annotations

import json
import subprocess

from test_plan_tools import SCRIPTS, run
from test_stable_split import DISPATCH

WAVE0 = SCRIPTS / "wave0.py"
COMMIT = SCRIPTS / "checkpoint.py"

HEAD = """# Plan
**Goal:** build it.
{stack}

Global constraints (from SPEC): {constraints}

```mermaid
%% taskgraph
flowchart LR
  T1 --> T2
```

"""
TASK = """### {tid} {title} · Complexity 3 · Est. context 30k
**Owns:** {owns}
**Est. time:** 2 min
{extra}**Produces:** {produces}

"""


def plan(tmp_path, stack, constraints, tasks, name="plan.md"):
    text = HEAD.format(stack=stack, constraints=constraints) + "".join(
        TASK.format(extra=t.get("extra", ""), **{k: t[k] for k in ("tid", "title", "owns", "produces")}) for t in tasks)
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


PY = [{"tid": "T1", "title": "slug", "owns": "src/pkg/slug.py, tests/test_slug.py", "produces": "pkg.slug.slugify(text: str) -> str"},
      {"tid": "T2", "title": "feed", "owns": "src/pkg/feed.py, tests/test_feed.py", "produces": "pkg.feed.build(items: list[str]) -> str",
       "extra": "**Consumes:** pkg.slug.slugify(text: str) -> str\n"}]
TS = [{"tid": "T1", "title": "slug", "owns": "src/slug.ts, src/slug.test.ts", "produces": "slug.slugify(text: string) -> string"},
      {"tid": "T2", "title": "feed", "owns": "src/feed.ts, src/feed.test.ts", "produces": "feed.build(items: string[]) -> string",
       "extra": "**Consumes:** slug.slugify(text: string) -> string\n"}]


def brief(tmp_path):
    return (tmp_path / ".superlite" / "briefs" / "wave0.md").read_text(encoding="utf-8")


def test_python_plan_brief_uses_plan_facts(tmp_path):
    p = plan(tmp_path, "**Tech stack:** Python 3.12; pytest", "root `pyproject.toml`, pytest testpaths tests", PY)
    cp = run(WAVE0, "--plan", p, cwd=tmp_path)
    assert cp.returncode == 0, cp.both
    b = brief(tmp_path)
    assert "The plan names: Python" in b and "pyproject.toml" in b
    assert 'name: "pkg.slug.slugify"' in b and 'params: ["text: str"]' in b and 'returns: "str"' in b
    assert "pkg.feed.build" not in b  # nobody consumes it: no stub
    assert "tests/test_slug.py" not in b.split("```yaml")[1]  # test files are not stub targets


def test_typescript_plan_brief_has_no_python_assumptions(tmp_path):
    p = plan(tmp_path, "**Tech stack:** TypeScript on Node 20; vitest", "package.json scripts: test = vitest run", TS)
    run(WAVE0, "--plan", p, cwd=tmp_path)
    b = brief(tmp_path)
    assert "The plan names: TypeScript, JavaScript" in b
    low = b.replace(str(p), "PLAN").lower()
    for word in ("pytest", "__init__", "raise notimplementederror"):
        assert word not in low
    assert "pyproject.toml" in b  # only inside the neutral marker-file list
    assert "Do not assume Python" not in b  # the plan names a language
    assert 'params: ["text: string"]' in b and 'returns: "string"' in b
    assert ".py" not in b.split("## Stub spec")[1]


def test_no_language_plan_says_infer_from_marker_files(tmp_path):
    p = plan(tmp_path, "", "keep it small", [{"tid": "T1", "title": "a", "owns": "lib/a.x", "produces": "a.f(x: int) -> int"}])
    run(WAVE0, "--plan", p, cwd=tmp_path)
    b = brief(tmp_path)
    assert "name no language" in b and "Do not assume Python" in b
    for m in ("package.json", "go.mod", "Cargo.toml", "pyproject.toml", "*.csproj", "Makefile"):
        assert m in b


def test_plan_with_wave0_task_uses_its_block_and_id(tmp_path):
    tasks = [{"tid": "T1", "title": "Wave 0 skeleton", "owns": "package.json, tsconfig.json, src/errors.ts",
              "produces": "errors.AppError(message: string) -> AppError", "extra": "**Kind:** mechanical\n**Model:** haiku\n"}] + [
        dict(t, tid=f"T{i + 2}") for i, t in enumerate(TS)]
    p = plan(tmp_path, "**Tech stack:** TypeScript", "package.json", tasks)
    cp = run(WAVE0, "--plan", p, cwd=tmp_path)
    assert 'description="T1 Wave 0 skeleton"' in cp.out
    b = brief(tmp_path)
    assert "The plan's own Wave 0 task, verbatim" in b and "package.json, tsconfig.json, src/errors.ts" in b
    assert json.loads((tmp_path / ".superlite" / "briefs" / "wave0.json").read_text(encoding="utf-8"))["synthetic"] is False


def test_agent_line_names_haiku_task_and_brief(tmp_path):
    p = plan(tmp_path, "**Tech stack:** Go", "go.mod", TS)
    cp = run(WAVE0, "--plan", p, cwd=tmp_path)
    line = cp.out.splitlines()[0]
    assert line.startswith('Agent(subagent_type="superlite:implementer-haiku", description="T0 Wave 0 skeleton"')
    assert "briefs/wave0.md" in line and "do not reply READY" in line and "SUPERLITE=" in line
    assert " ms" in cp.out.splitlines()[1]


def test_dispatch_lists_t0_first_until_committed(tmp_path):
    p = plan(tmp_path, "**Tech stack:** Python", "x", PY)
    first = run(DISPATCH, "--plan", p, "--pool", tmp_path / "pool.json", "--ledger", tmp_path / "l.md", cwd=tmp_path)
    assert "T0" not in first.out  # no brief yet
    run(WAVE0, "--plan", p, cwd=tmp_path)
    cp = run(DISPATCH, "--plan", p, "--pool", tmp_path / "pool.json", "--ledger", tmp_path / "l.md", cwd=tmp_path)
    assert cp.out.splitlines()[0].startswith("T0 wave0 tier=haiku")
    (tmp_path / "pool.json").write_text(json.dumps({"agents": [{"id": "h", "tasks": ["T0"], "current": "T0", "busy": True}]}), encoding="utf-8")
    assert "T0" not in run(DISPATCH, "--plan", p, "--pool", tmp_path / "pool.json", "--ledger", tmp_path / "l.md", cwd=tmp_path).out
    (tmp_path / "pool.json").unlink()
    (tmp_path / "l.md").write_text("- commit T0 abcdef1234 skeleton\n", encoding="utf-8")
    assert "T0" not in run(DISPATCH, "--plan", p, "--pool", tmp_path / "pool.json", "--ledger", tmp_path / "l.md", cwd=tmp_path).out


def test_checkpoint_t0_takes_its_files_not_other_tasks(tmp_path):
    def git(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    p = plan(tmp_path, "**Tech stack:** TypeScript", "x", TS)
    run(WAVE0, "--plan", p, cwd=tmp_path)
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "slug.ts").write_text("// T1 in progress\n", encoding="utf-8")  # another task's file: left alone
    cp = run(COMMIT, "T0", "--plan", p, cwd=tmp_path)
    assert cp.returncode == 0, cp.both
    files = subprocess.run(["git", "show", "--name-only", "--format=", "HEAD"], cwd=tmp_path, capture_output=True, text=True).stdout.split()
    assert sorted(files) == [".gitignore", "package.json"]  # the .gitignore wave0 wrote is committed by the Wave 0 task


def test_lint_stable_prints_t0_agent_line_and_send_before_edit(tmp_path):
    tasks = PY + [{"tid": "T3", "title": "big", "owns": "src/pkg/big.py", "produces": "pkg.big.f(x: int) -> int", "extra": "**Consumes:** nobody.made(x: int) -> int\n"}]
    p = plan(tmp_path, "**Tech stack:** Python", "x", tasks)
    cp = run(SCRIPTS / "plan_tools.py", "lint", "--stable", "--plan", p, cwd=tmp_path)
    lines = cp.out.rstrip().splitlines()
    assert any(ln.startswith('START FIRST (Wave 0 skeleton, T0): Agent(subagent_type="superlite:implementer-haiku"') for ln in lines)
    assert any(ln.startswith("SEND BEFORE ANY PLAN EDIT: T0 T1 T2") and "only then patch T3" in ln for ln in lines)
    assert lines[-1] == "DISPATCH NOW: T1 T2"
    again = run(SCRIPTS / "plan_tools.py", "lint", "--stable", "--plan", p, cwd=tmp_path).out
    assert "START FIRST" not in again  # printed once


def test_lint_prints_log_line_and_unblocked_line_counts_as_dispatched(tmp_path):
    p = plan(tmp_path, "**Tech stack:** Python", "x", PY)
    out = run(SCRIPTS / "plan_tools.py", "lint", "--stable", "--plan", p, cwd=tmp_path).out
    log = [ln for ln in out.splitlines() if ln.startswith("THEN LOG")]
    assert log and 'unblocked: dispatched T0 T1 T2 at $(date +%H:%M:%S); waiting on fixes: none' in log[0]
    (tmp_path / ".superlite" / "progress.md").write_text("- unblocked: dispatched T0 T1 at 10:00:00; waiting on fixes: none\n", encoding="utf-8")
    again = run(SCRIPTS / "plan_tools.py", "lint", "--stable", "--plan", p, cwd=tmp_path).out
    assert again.rstrip().splitlines()[-1] == "DISPATCH NOW: T2"
