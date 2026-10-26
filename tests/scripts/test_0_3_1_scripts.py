"""0.3.1 script fixes: F2 run_tests --background/--wait, F3 draft tasks, F5 packet source excerpts."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parents[1] / "skills" / "swarm" / "scripts"
FIX = HERE / "fixtures"
sys.path.insert(0, str(SCRIPTS))

# the real sibling tools, not the fake sl launcher
ENV = {k: v for k, v in os.environ.items() if k != "SUPERLITE_SL"}
ENV["PYTHONIOENCODING"] = "utf-8"


def run(script, *args, cwd=None):
    return subprocess.run([sys.executable, str(SCRIPTS / f"{script}.py"), *map(str, args)],
                          capture_output=True, text=True, cwd=cwd, env=ENV, encoding="utf-8",
                          timeout=240)


def project(tmp_path, *names):
    proj = tmp_path / "proj"
    proj.mkdir()
    for name in names:
        shutil.copy(FIX / "run_pytest" / f"check_{name}.py", proj / f"test_{name}.py")
    return proj


# ------------------------------------------------------------------ F2: background and wait

def test_background_returns_at_once_then_wait_reads_green(tmp_path):
    proj = project(tmp_path, "pass")
    r = run("run_tests", "--fast", "test_pass.py", "--background", cwd=proj)
    assert r.returncode == 0, r.stderr
    line = r.stdout.strip().splitlines()[-1]
    assert line.startswith("summary: ")
    w = run("run_tests", "--wait", "--timeout", "120", cwd=proj)
    assert w.returncode == 0, w.stdout + w.stderr
    assert json.loads(w.stdout)["failed"] == 0
    assert Path(line[len("summary: "):]).exists()
    published = json.loads((proj / ".superlite" / "test-summary.json").read_text(encoding="utf-8"))
    assert published["passed"] == 2 and published["mode"] == "fast"


def test_wait_exits_1_on_red(tmp_path):
    proj = project(tmp_path, "pass", "fail")
    assert run("run_tests", "--background", cwd=proj).returncode == 0
    w = run("run_tests", "--wait", "--timeout", "120", cwd=proj)
    assert w.returncode == 1, w.stdout + w.stderr
    assert json.loads(w.stdout)["failed"] == 1


def test_wait_ignores_stale_summary_and_times_out(tmp_path):
    rt = importlib.import_module("run_tests")
    proj = project(tmp_path, "pass")
    sl = proj / ".superlite"
    sl.mkdir()
    # an old green summary exists, but the latest run (marker) has not finished
    (sl / "test-summary.json").write_text(json.dumps({"passed": 9, "failed": 0, "skipped": 0}), encoding="utf-8")
    (sl / "test-summary.latest").write_text(
        json.dumps({"id": "fresh", "summary": str(sl / "test-summary-fresh.json")}), encoding="utf-8")
    t0 = time.monotonic()
    w = run("run_tests", "--wait", "--timeout", "1", cwd=proj)
    assert w.returncode == 2 and "timed out" in w.stderr
    assert time.monotonic() - t0 < 30
    # once that run writes its own summary, wait returns it
    (sl / "test-summary-fresh.json").write_text(json.dumps({"passed": 1, "failed": 2, "skipped": 0}), encoding="utf-8")
    w = run("run_tests", "--wait", "--timeout", "5", cwd=proj)
    assert w.returncode == 1 and json.loads(w.stdout)["failed"] == 2
    assert rt.pointer_path(sl / "test-summary.json").name == "test-summary.latest"


def test_wait_without_background_run_exits_2(tmp_path):
    proj = project(tmp_path, "pass")
    w = run("run_tests", "--wait", "--timeout", "1", cwd=proj)
    assert w.returncode == 2 and "no background run" in w.stderr


def test_two_background_runs_keep_separate_summaries(tmp_path):
    proj = project(tmp_path, "pass")
    a = run("run_tests", "--fast", "test_pass.py", "--background", cwd=proj)
    b = run("run_tests", "--fast", "test_pass.py", "--background", cwd=proj)
    pa, pb = (x.stdout.strip().splitlines()[-1].split(": ", 1)[1] for x in (a, b))
    assert pa != pb
    ptr = json.loads((proj / ".superlite" / "test-summary.latest").read_text(encoding="utf-8"))
    assert ptr["summary"] == pb  # the pointer names the newest run
    w = run("run_tests", "--wait", "--timeout", "180", cwd=proj)
    assert w.returncode == 0
    deadline = time.monotonic() + 180
    while not Path(pa).exists() and time.monotonic() < deadline:
        time.sleep(0.5)
    for p in (pa, pb):
        assert json.loads(Path(p).read_text(encoding="utf-8"))["passed"] == 2
    ptr = json.loads((proj / ".superlite" / "test-summary.latest").read_text(encoding="utf-8"))
    assert ptr["summary"] == pb  # the older run finishing did not move the pointer


def test_publish_skips_when_a_newer_run_owns_the_pointer(tmp_path):
    rt = importlib.import_module("run_tests")
    sl = tmp_path / ".superlite"
    pub = sl / "test-summary.json"
    rt.atomic_write(rt.pointer_path(pub), json.dumps({"id": "new", "summary": "x"}))
    mine = rt.run_summary_path(pub, "old")
    rt.atomic_write(mine, json.dumps({"passed": 1, "failed": 0, "skipped": 0}))
    ns = type("A", (), {"run_id": "old", "publish": str(pub), "summary": str(mine)})
    rt.publish_summary(ns)
    assert not pub.exists()
    ns.run_id = "new"
    rt.publish_summary(ns)
    assert pub.exists()


# ------------------------------------------------------------------ F3: draft tasks

def make_plan(tmp_path, statuses, edges, body_extra=None):
    """statuses: {task id: status line text or None}."""
    out = ["# Plan", "**Goal:** test.", "", "```mermaid", "%% taskgraph", "flowchart LR"]
    out += [f"  {a} --> {b}" for a, b in edges]
    out += ["```", ""]
    for tid, st in statuses.items():
        out.append(f"### {tid} task {tid.lower()} · Complexity 3 (O-1) · Est. context 40k")
        out.append(f"**Owns:** src/pkg/{tid.lower()}.py, tests/test_{tid.lower()}.py")
        out.append("**Est. time:** 5 min")
        if st:
            out.append(f"**Status:** {st}")
        if body_extra and tid in body_extra:
            out.append(body_extra[tid])
        out.append("")
    p = tmp_path / "plan.md"
    p.write_text("\n".join(out), encoding="utf-8")
    return p


def test_next_tasks_skips_draft_and_transitive_dependents(tmp_path):
    # T1 approved; T2 draft (no deps); T3 depends on T2; T4 depends on T3 (transitive); T5 approved, independent
    plan = make_plan(tmp_path, {"T1": None, "T2": "DRAFT", "T3": "approved", "T4": None, "T5": "Approved"},
                     [("T2", "T3"), ("T3", "T4")])
    r = run("next_tasks", "--plan", plan, "--cap", 10)
    assert r.returncode == 0, r.stderr
    assert sorted(r.stdout.split()) == ["T1", "T5"]
    r = run("next_tasks", "--plan", plan, "--committed", "T1,T5")
    assert r.stdout.split() == []


def test_dispatch_skips_draft_and_transitive_dependents(tmp_path):
    plan = make_plan(tmp_path, {"T1": None, "T2": "draft", "T3": None, "T4": None},
                     [("T2", "T3"), ("T3", "T4")])
    r = run("dispatch", "--plan", plan, "--pool", tmp_path / "pool.json", "--ledger", tmp_path / "l.md")
    assert r.returncode == 0, r.stderr
    assert [ln.split()[0] for ln in r.stdout.splitlines()] == ["T1"]


def test_draft_blocked_closure():
    nt = importlib.import_module("next_tasks")
    graph = {"T1": [], "T2": [], "T3": ["T2"], "T4": ["T3", "T1"], "T5": ["T1"]}
    assert nt.draft_blocked(graph, {"T2": "draft"}) == {"T2", "T3", "T4"}
    assert nt.draft_blocked(graph, {}) == set()


def test_taskgraph_reports_status(tmp_path):
    plan = make_plan(tmp_path, {"T1": None, "T2": "Draft", "T3": "approved"}, [("T1", "T2")])
    r = run("plan_tools", "taskgraph", "--plan", plan, "--format", "full")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["status"] == {"T1": "approved", "T2": "draft", "T3": "approved"}
    r = run("plan_tools", "taskgraph", "--plan", plan)
    assert "status T2 draft" in r.stderr and "status T1" not in r.stderr


def test_lint_skips_placeholders_in_draft_only(tmp_path):
    plan = make_plan(tmp_path, {"T1": "draft", "T2": None}, [("T1", "T2")],
                     body_extra={"T1": "Do the thing: TBD"})
    r = run("plan_tools", "lint", "--plan", plan)
    assert "placeholder" not in r.stdout, r.stdout
    plan = make_plan(tmp_path, {"T1": "approved", "T2": None}, [("T1", "T2")],
                     body_extra={"T1": "Do the thing: TBD"})
    r = run("plan_tools", "lint", "--plan", plan)
    assert "placeholder" in r.stdout and r.returncode == 1


def test_lint_still_checks_graph_with_drafts(tmp_path):
    plan = make_plan(tmp_path, {"T1": "draft", "T2": "draft"}, [("T1", "T2"), ("T2", "T1")])
    r = run("plan_tools", "lint", "--plan", plan)
    assert r.returncode == 1 and "cycle" in r.stdout


def test_schedule_ignores_draft_tasks(tmp_path):
    plan = make_plan(tmp_path, {"T1": None, "T2": "draft", "T3": None}, [("T1", "T2"), ("T2", "T3")])
    r = run("plan_tools", "schedule", "--plan", plan, "--json")
    assert r.returncode == 0, r.stderr
    assert set(json.loads(r.stdout)["tasks"]) == {"T1", "T3"}


def test_stubs_ignore_draft_tasks(tmp_path):
    out = ["# Plan", "", "```mermaid", "%% taskgraph", "flowchart LR", "  T1 --> T2", "```", ""]
    for tid, st in (("T1", None), ("T2", "draft")):
        out += [f"### {tid} t · Complexity 3 (O-1) · Est. context 40k",
                f"**Owns:** src/pkg/m{tid.lower()}.py",
                f"**Produces:** src.pkg.m{tid.lower()}.f_{tid.lower()}(x: int) -> int"]
        if st:
            out.append(f"**Status:** {st}")
        out.append("")
    plan = tmp_path / "plan.md"
    plan.write_text("\n".join(out), encoding="utf-8")
    r = run("plan_tools", "stubs", "--plan", plan, "--root", tmp_path / "root")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tmp_path / "root" / "src" / "pkg" / "mt1.py").exists()
    assert not (tmp_path / "root" / "src" / "pkg" / "mt2.py").exists()


# ------------------------------------------------------------------ F5: packet source excerpts

_spec = importlib.util.spec_from_file_location("pt_review_f5", SCRIPTS / "pt_review.py")
pt_review = importlib.util.module_from_spec(_spec)
sys.modules["pt_review_f5"] = pt_review
_spec.loader.exec_module(pt_review)


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, check=True)


def excerpt_project(tmp_path, files, added):
    proj = tmp_path / "proj"
    shutil.copytree(FIX / "review_basic", proj)
    pkg = proj / "src" / "minisheet"
    for i in range(files):
        (pkg / f"m{i}.py").write_text("x = 0\n", encoding="utf-8")
    git(proj, "init", "-q")
    git(proj, "add", "-A")
    git(proj, "commit", "-q", "-m", "base")
    base = git(proj, "rev-parse", "HEAD").stdout.strip()
    log = []
    for i in range(files):
        f = pkg / f"m{i}.py"
        f.write_text("x = 0\n" + "".join(f"y{j} = {j}\n" for j in range(added)), encoding="utf-8")
        log.append({"file": f"src/minisheet/m{i}.py", "line": 2, "context": f"assumed thing {i}",
                    "agent": "T1", "kind": "assumption", "confidence": "medium", "time": "10:00:00"})
    (proj / "incidents.jsonl").write_text("\n".join(json.dumps(e) for e in log) + "\n", encoding="utf-8")
    return proj, base


def make_packet(proj, base):
    out = proj / "packet.md"
    rc = pt_review.main(["packet", "--plan", str(proj / "plan.md"), "--root", str(proj),
                         "--log", str(proj / "incidents.jsonl"), "--out", str(out),
                         "--base", base, "--max-tokens", "500000"])
    assert rc == 0
    return out.read_text(encoding="utf-8")


def section(text):
    assert "## 8. Source excerpts" in text
    return text.split("## 8. Source excerpts", 1)[1]


def test_packet_excerpt_shows_changed_hunk(tmp_path):
    proj, base = excerpt_project(tmp_path, 1, 5)
    sec = section(make_packet(proj, base))
    assert "### src/minisheet/m0.py" in sec and "+y4 = 4" in sec
    assert "truncated" not in sec


def test_packet_excerpt_caps_per_file(tmp_path):
    proj, base = excerpt_project(tmp_path, 1, 300)
    sec = section(make_packet(proj, base))
    assert "(truncated: 120 of " in sec and "per-file cap" in sec
    assert sec.count("\n+y") == 120 - 5 or sec.count("\n+y") <= 120  # header lines count toward the cap
    assert "+y299" not in sec


def test_packet_excerpt_caps_total(tmp_path):
    proj, base = excerpt_project(tmp_path, 8, 300)
    sec = section(make_packet(proj, base))
    shown = [ln for ln in sec.splitlines() if ln.startswith(("+y", "@@", "+++", "---", "diff ", "index "))]
    assert len(shown) <= 600
    assert "total cap" in sec and "more file(s) omitted" in sec


def test_source_excerpts_unit_caps(tmp_path):
    proj, base = excerpt_project(tmp_path, 3, 200)
    out = pt_review.source_excerpts(proj, base, [f"src/minisheet/m{i}.py" for i in range(3)],
                                    per_file=50, total=80)
    body = [ln for ln in out.splitlines() if ln.startswith(("+", "-", "@@", "diff", "index", " "))
            and not ln.startswith("- (")]
    assert len(body) <= 80
    assert "per-file cap" in out and "total cap" in out


# ------------------------------------------------------------------ lint: CHAIN and OPUS-TASK

def _chain_plan(tmp_path):
    """A 4-task chain beside 6 independent tasks: critical path 20 min against max(longest 5, work 50 / width 7)."""
    st = {f"T{i}": None for i in range(1, 11)}
    return make_plan(tmp_path, st, [("T1", "T2"), ("T2", "T3"), ("T3", "T4")])


def _chain_id(out):
    import re
    return re.search(r"CHAIN \[(chain-[0-9a-f]{4})\]", out).group(1)


def test_lint_chain_flags_long_sequential_path(tmp_path):
    plan = _chain_plan(tmp_path)
    r = run("plan_tools", "lint", "--plan", plan, "--stated", "--max-task-min", "10")
    assert r.returncode == 1 and "CHAIN [chain-" in r.stdout
    assert "critical path T1 -> T2 -> T3 -> T4 is 20.0 min, over 2.5x max(longest task, work / peak width) = 7.1 min" in r.stdout
    assert "Ruling: sequential [chain-" in r.stdout


def test_lint_pure_chain_is_not_flagged_by_the_new_baseline(tmp_path):
    plan = make_plan(tmp_path, {f"T{i}": None for i in range(1, 5)}, [("T1", "T2"), ("T2", "T3"), ("T3", "T4")])
    assert "CHAIN" not in run("plan_tools", "lint", "--plan", plan, "--stated", "--max-task-min", "10").stdout  # width 1: work == path


def test_lint_chain_ruling_needs_the_printed_chain_id(tmp_path):
    plan = _chain_plan(tmp_path)
    cid = _chain_id(run("plan_tools", "lint", "--plan", plan, "--stated", "--max-task-min", "10").stdout)
    text = plan.read_text(encoding="utf-8")
    for bad in ("Ruling: sequential - one shared parser", "Ruling: sequential [chain-0000] - wrong id"):  # written before lint / wrong id
        plan.write_text(text + "\n" + bad + "\n", encoding="utf-8")
        r = run("plan_tools", "lint", "--plan", plan, "--stated", "--max-task-min", "10")
        assert f"CHAIN [{cid}]" in r.stdout and "without this id was ignored" in r.stdout
    plan.write_text(text + f"\nRuling: sequential [{cid}] - one shared parser\n", encoding="utf-8")
    r = run("plan_tools", "lint", "--plan", plan, "--stated", "--max-task-min", "10")
    assert "CHAIN" not in r.stdout
    assert f"NOTE: accepted Ruling: sequential [{cid}] chain T1 -> T2 -> T3 -> T4 20.0 min vs baseline 7.1 min (costs 12.9 min): one shared parser" in r.stdout


def test_lint_chain_needs_three_tasks_and_ignores_drafts(tmp_path):
    plan = make_plan(tmp_path, {"T1": None, "T2": None}, [("T1", "T2")])
    assert "CHAIN" not in run("plan_tools", "lint", "--plan", plan, "--stated", "--max-task-min", "10").stdout
    plan = make_plan(tmp_path, {"T1": None, "T2": None, "T3": "draft"}, [("T1", "T2"), ("T2", "T3")])
    assert "CHAIN" not in run("plan_tools", "lint", "--plan", plan, "--stated", "--max-task-min", "10").stdout


def test_lint_opus_task_flagged_unless_draft(tmp_path):
    plan = make_plan(tmp_path, {"T1": None, "T2": None}, [("T1", "T2")], body_extra={"T1": "**Model:** opus"})
    r = run("plan_tools", "lint", "--plan", plan, "--max-task-min", "10")
    assert r.returncode == 1 and "OPUS-TASK: T1 uses Model: opus; split it" in r.stdout
    plan = make_plan(tmp_path, {"T1": "draft", "T2": None}, [("T1", "T2")], body_extra={"T1": "**Model:** opus"})
    assert "OPUS-TASK" not in run("plan_tools", "lint", "--plan", plan, "--max-task-min", "10").stdout
    plan = make_plan(tmp_path, {"T1": None, "T2": None}, [("T1", "T2")], body_extra={"T1": "**Model:** sonnet"})
    assert "OPUS-TASK" not in run("plan_tools", "lint", "--plan", plan, "--max-task-min", "10").stdout


# ------------------------------------------------------------------ lint: Kind / Model: haiku

GOOD_TEST = "Test code: def test_add(): assert add(1, 2) == 3"


def kind_plan(tmp_path, extra, status=None, focus=""):
    plan = make_plan(tmp_path, {"T1": status}, [], body_extra={"T1": extra})
    if focus:
        plan.write_text(plan.read_text(encoding="utf-8") + f"\n## Review Focus\n{focus}\n", encoding="utf-8")
    return plan


def lint(plan):
    return run("plan_tools", "lint", "--plan", plan, "--max-task-min", "10")


def test_haiku_needs_mechanical_kind(tmp_path):
    r = lint(kind_plan(tmp_path, "**Model:** haiku\n" + GOOD_TEST))
    assert r.returncode == 1 and "KIND: T1 uses Model: haiku but is not Kind: mechanical" in r.stdout
    r = lint(kind_plan(tmp_path, "**Model:** haiku\n**Kind:** mechanical\n" + GOOD_TEST))
    assert "KIND" not in r.stdout, r.stdout


def test_mechanical_rejected_for_size_tests_focus_and_missing_contract(tmp_path):
    r = lint(kind_plan(tmp_path, "**Kind:** mechanical\n**LOC owned:** 200\n**Components:** 3\n**Tests:** slow\n",
                       focus="- pkg: deep, numeric"))
    out = r.stdout
    assert r.returncode == 1 and "KIND: T1 is not mechanical:" in out
    for frag in ("LOC owned 200 > 150", "Components 3 > 2", "Tests: slow", "Review Focus has deep, numeric",
                 "no test code or contract"):
        assert frag in out, out


def test_mechanical_accepted_with_named_tests_and_expected_values(tmp_path):
    r = lint(kind_plan(tmp_path, "**Kind:** mechanical\n**LOC owned:** 40\n**Components:** 1\n"
                                 "Tests: test_add returns 3 for (1, 2)", focus="- other: encoding"))
    assert "KIND" not in r.stdout, r.stdout


def test_kind_draft_exempt_and_exposed_in_taskgraph(tmp_path):
    r = lint(kind_plan(tmp_path, "**Model:** haiku\n**Kind:** mechanical\n**LOC owned:** 999", status="draft"))
    assert "KIND" not in r.stdout
    plan = kind_plan(tmp_path, "**Kind:** mechanical\n" + GOOD_TEST)
    g = run("plan_tools", "taskgraph", "--plan", plan, "--format", "full")
    assert json.loads(g.stdout)["kind"] == {"T1": "mechanical"}
    pt = importlib.import_module("plan_tools")
    task = pt.Plan.load(str(plan)).by_id["T1"]
    assert task.explicit_features["kind"] == "mechanical"


# ------------------------------------------------------------------ D: short-name Produces resolve to owned modules

def test_contract_resolves_short_module_names_and_class_methods(tmp_path):
    pkg = tmp_path / "src" / "sitegen"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "frontmatter.py").write_text("def parse(text: str) -> tuple[dict, str]:\n    return {}, text\n", encoding="utf-8")
    (pkg / "slug.py").write_text("def slugify(text: str) -> str:\n    return text\n\n\n"
                                 "def unique_slug(text: str, taken: set[str]) -> str:\n    return text\n", encoding="utf-8")
    (pkg / "search.py").write_text("def tokenize(s: str) -> list[str]:\n    return [s]\n\n\n"
                                   "class Index:\n    def add(self, doc: dict) -> None:\n        pass\n", encoding="utf-8")
    plan = tmp_path / "plan.md"
    plan.write_text("\n".join([
        "# Plan", "",
        "### T1 sitegen frontmatter+slug",
        "**Owns:** src/sitegen/frontmatter.py, src/sitegen/slug.py, tests/sitegen/test_slug.py",
        "**Produces:** frontmatter.parse(text: str) -> tuple[dict, str] · slug.slugify(text: str) -> str · "
        "slug.unique_slug(text: str, taken: set[str]) -> str", "",
        "### T4 sitegen search",
        "**Owns:** src/sitegen/search.py, tests/sitegen/test_search.py",
        "**Produces:** search.tokenize(s: str) -> list[str] · Index.add(doc: dict) -> None", "",
        "### T9 sitegen ghost",
        "**Owns:** src/sitegen/search.py",
        "**Produces:** search.missing(x: int) -> int", ""]), encoding="utf-8")
    mismatches, checked = pt_review.check_interfaces(pt_review.parse_plan(str(plan)), tmp_path, None)
    msgs = [m.msg for m in mismatches]
    assert checked == 6
    assert len(mismatches) == 1 and mismatches[0].task == "T9" and "missing" in msgs[0], msgs
    assert not any("not found in sitegen.frontmatter" in m for m in msgs)


def test_stubs_resolve_short_module_names(tmp_path):
    pt = importlib.import_module("plan_tools")
    owned = ["src/sitegen/frontmatter.py", "src/sitegen/slug.py", "tests/sitegen/test_slug.py"]
    assert pt.resolve_module(["slug"], owned) == ("src/sitegen/slug.py", None)
    assert pt.resolve_module(["sitegen", "slug"], owned) == ("src/sitegen/slug.py", None)
