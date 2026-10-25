"""Tests for plan_tools.py (taskgraph, stubs, schedule, lint, dispatcher) and predict.py."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "subagent-driven-development" / "scripts"
CONFIG = ROOT / "skills" / "subagent-driven-development" / "config"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "plans"
PLAN_TOOLS = SCRIPTS / "plan_tools.py"
PREDICT = SCRIPTS / "predict.py"

sys.path.insert(0, str(SCRIPTS))


def run(script: Path, *args, cwd=None):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    cp = subprocess.run([sys.executable, str(script), *map(str, args)], capture_output=True, cwd=cwd,
                        env=env, timeout=120)
    cp.out = cp.stdout.decode("utf-8")
    cp.err = cp.stderr.decode("utf-8")
    cp.both = cp.out + cp.err
    return cp


def pt(*args, **kw):
    return run(PLAN_TOOLS, *args, **kw)


def make_plan(tmp_path, tasks, edges=(), header="", name="plan.md"):
    """tasks: list of dicts with id, and optional time, ctx (k), owns, produces, consumes, features."""
    out = ["# Generated Plan", "**Goal:** test.", header, "", "```mermaid", "%% taskgraph", "flowchart LR"]
    out += [f"  {a} --> {b}" for a, b in edges]
    out += ["```", ""]
    for t in tasks:
        tid = t["id"]
        out.append(f"### {tid} {t.get('title', 'task ' + tid.lower())} · Complexity 3 (O-1) · Est. context {t.get('ctx', 40)}k")
        out.append(f"**Owns:** {t.get('owns', f'src/pkg/{tid.lower()}.py, tests/test_{tid.lower()}.py')}")
        if "time" in t:
            out.append(f"**Est. time:** {t['time']} min")
        if "features" in t:
            out.append(f"**Features:** {t['features']}")
        for key, label in (("components", "Components"), ("loc", "LOC owned"), ("tests", "Tests"), ("model", "Model")):
            if key in t:
                out.append(f"**{label}:** {t[key]}")
        if "produces" in t:
            out.append(f"**Produces:** {t['produces']}")
        if "consumes" in t:
            out.append(f"**Consumes:** {t['consumes']}")
        out.append("")
    p = tmp_path / name
    p.write_text("\n".join(out), encoding="utf-8")
    return p


def sched(plan, *extra):
    cp = pt("schedule", "--plan", plan, "--json", *extra)
    assert cp.returncode == 0, cp.both
    return json.loads(cp.out)


def import_file(path: Path, name="stubmod"):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- stubs

def test_function_stub_written(tmp_path):
    cp = pt("stubs", "--plan", FIXTURES / "minisheet.md", "--root", tmp_path)
    assert cp.returncode == 0, cp.both
    target = tmp_path / "src" / "minisheet" / "evaluator.py"
    src = target.read_text(encoding="utf-8")
    assert "def evaluate(formula: str, get_cell) -> Value:" in src
    assert "Contract: never raises" in src
    assert "wrote src/minisheet/evaluator.py" in cp.out
    mod = import_file(target)
    with pytest.raises(NotImplementedError) as exc:
        mod.evaluate("=1", lambda ref: None)
    assert str(exc.value) == "T1"


def test_class_method_stub(tmp_path):
    assert pt("stubs", "--plan", FIXTURES / "minisheet.md", "--root", tmp_path).returncode == 0
    target = tmp_path / "src" / "minisheet" / "workbook.py"
    mod = import_file(target)
    assert hasattr(mod, "Workbook") and hasattr(mod.Workbook, "set") and hasattr(mod.Workbook, "get")
    src = target.read_text(encoding="utf-8")
    assert "def set(self, ref: str, raw: str) -> list[str]:" in src
    with pytest.raises(NotImplementedError) as exc:
        mod.Workbook().set("A1", "1")
    assert str(exc.value) == "T5"
    assert mod.__all__ == ["Workbook"]


def test_existing_file_untouched(tmp_path):
    target = tmp_path / "src" / "minisheet" / "evaluator.py"
    target.parent.mkdir(parents=True)
    target.write_text("# hand written\n", encoding="utf-8")
    cp = pt("stubs", "--plan", FIXTURES / "minisheet.md", "--root", tmp_path)
    assert cp.returncode == 0, cp.both
    assert target.read_text(encoding="utf-8") == "# hand written\n"
    assert "skipped src/minisheet/evaluator.py (exists)" in cp.out
    assert (tmp_path / "src" / "minisheet" / "workbook.py").exists()


def test_stubs_import_cleanly(tmp_path):
    assert pt("stubs", "--plan", FIXTURES / "minisheet.md", "--root", tmp_path).returncode == 0
    files = sorted((tmp_path / "src").rglob("*.py"))
    assert [f.name for f in files] == ["evaluator.py", "parser.py", "server.py", "workbook.py"]
    for i, f in enumerate(files):
        assert f.read_text(encoding="utf-8").count("from __future__ import annotations") == 1
        import_file(f, f"clean{i}")  # unresolved names (Value, HTTPServer) must not matter


def test_dry_run_writes_nothing(tmp_path):
    cp = pt("stubs", "--plan", FIXTURES / "minisheet.md", "--root", tmp_path, "--dry-run")
    assert cp.returncode == 0, cp.both
    assert "would write src/minisheet/evaluator.py" in cp.out
    assert list(tmp_path.iterdir()) == []


# ------------------------------------------------------------ taskgraph

def test_taskgraph_parses_edges(tmp_path):
    p = make_plan(tmp_path, [{"id": "T1"}, {"id": "T2"}, {"id": "T5"}], [("T1", "T5"), ("T2", "T5")])
    cp = pt("taskgraph", "--plan", p)
    assert cp.returncode == 0, cp.both
    assert json.loads(cp.out) == {"T1": [], "T2": [], "T5": ["T1", "T2"]}


def test_taskgraph_fixture(tmp_path):
    cp = pt("taskgraph", "--plan", FIXTURES / "minisheet.md")
    assert json.loads(cp.out) == {"T1": [], "T2": [], "T5": ["T1", "T2"], "T7": ["T5"]}


def test_taskgraph_warns_mergeable(tmp_path):
    p = make_plan(tmp_path, [{"id": "T1", "ctx": 24}, {"id": "T2", "ctx": 40}], [("T1", "T2")])
    cp = pt("taskgraph", "--plan", p, "--format", "full")
    assert cp.returncode == 0, cp.both
    data = json.loads(cp.out)
    assert [m["task"] for m in data["mergeable"]] == ["T1"]
    assert data["mergeable"][0]["merge_with"] == "T2"
    assert "variable context 6k" in data["mergeable"][0]["reasons"][0]
    plain = pt("taskgraph", "--plan", p)
    assert "mergeable T1" in plain.err
    assert json.loads(plain.out) == {"T1": [], "T2": ["T1"]}


def test_taskgraph_does_not_warn_on_big_tasks(tmp_path):
    data = json.loads(pt("taskgraph", "--plan", FIXTURES / "minisheet.md", "--format", "full").out)
    assert data["mergeable"] == []


def test_taskgraph_lists_seams(tmp_path):
    p = make_plan(tmp_path, [{"id": "T9a"}, {"id": "T9b"}], [("T9a", "T9b")])
    cp = pt("taskgraph", "--plan", p)
    assert "seam T9a↔T9b" in cp.err
    assert json.loads(cp.out) == {"T9a": [], "T9b": ["T9a"]}
    assert json.loads(pt("taskgraph", "--plan", p, "--format", "full").out)["seams"] == ["T9a↔T9b"]


def test_taskgraph_rejects_cycle():
    cp = pt("taskgraph", "--plan", FIXTURES / "cycle.md")
    assert cp.returncode == 2
    assert "cycle" in cp.both


def test_taskgraph_requires_block():
    cp = pt("taskgraph", "--plan", FIXTURES / "no_block.md")
    assert cp.returncode == 2
    assert "taskgraph" in cp.err


def test_taskgraph_rejects_unknown_task_id():
    cp = pt("taskgraph", "--plan", FIXTURES / "unknown_id.md")
    assert cp.returncode == 2
    assert "T9" in cp.err


def test_taskgraph_ignores_headings_inside_code_fences(tmp_path):
    p = make_plan(tmp_path, [{"id": "T1"}])
    p.write_text(p.read_text(encoding="utf-8") + "\n````markdown\n### T99 template only\n**Owns:** x.py\n````\n",
                 encoding="utf-8")
    assert json.loads(pt("taskgraph", "--plan", p).out) == {"T1": []}


# ------------------------------------------------------------- schedule

def test_schedule_critical_path(tmp_path):
    p = make_plan(tmp_path, [{"id": "T1", "time": 3}, {"id": "T7", "time": 2}, {"id": "T9a", "time": 3}],
                  [("T1", "T7")])
    rep = sched(p)
    assert rep["critical_path"] == ["T1", "T7"]
    assert rep["critical_path_min"] == pytest.approx(5.0)
    assert rep["peak_width"] == 2
    assert rep["build_min"] == pytest.approx(5.0)
    assert rep["pre_build_min"] == pytest.approx(5.0)
    assert rep["build_scale"] == pytest.approx(0.75)
    assert rep["post_build_min"] == pytest.approx(10.0)
    assert rep["total_min"] == pytest.approx(5.0 + 0.75 * 5.0 + 10.0)
    text = pt("schedule", "--plan", p, "--target-min", 30).out
    assert "predicted build 5.0 min" in text.splitlines()[0]
    assert "predicted total ≈ 18.8 min" in text.splitlines()[0]
    assert "(target ✓)" in text.splitlines()[0]


def test_schedule_respects_cap_and_longest_first(tmp_path):
    p = make_plan(tmp_path, [{"id": "T1", "time": 1}, {"id": "T2", "time": 2}, {"id": "T3", "time": 2},
                             {"id": "T4", "time": 3}])
    rep = sched(p, "--cap", 2)
    starts = {t: v["start"] for t, v in rep["tasks"].items()}
    assert starts["T4"] == 0 and starts["T2"] == 0  # the 3- and one 2-min task first
    assert starts["T3"] == pytest.approx(2.0) and starts["T1"] == pytest.approx(3.0)
    assert rep["build_min"] == pytest.approx(4.0)
    assert rep["peak_width"] == 2
    assert {v["lane"] for v in rep["tasks"].values()} == {1, 2}


def test_schedule_waits_for_dependencies(tmp_path):
    p = make_plan(tmp_path, [{"id": "T1", "time": 1}, {"id": "T2", "time": 2}, {"id": "T3", "time": 2}],
                  [("T1", "T3"), ("T2", "T3")])
    rep = sched(p)
    assert rep["tasks"]["T3"]["start"] == pytest.approx(2.0)
    assert rep["critical_path"] == ["T2", "T3"]


def test_schedule_insert_replaces_previous(tmp_path):
    p = make_plan(tmp_path, [{"id": "T1", "time": 3}, {"id": "T2", "time": 2}], [("T1", "T2")])
    for _ in range(2):
        cp = pt("schedule", "--plan", p, "--insert", "--target-min", 30)
        assert cp.returncode == 0, cp.both
    text = p.read_text(encoding="utf-8")
    assert text.count("%% schedule") == 1
    assert text.count("critical path:") == 1
    assert "gantt" in text and "(target ✓)" in text
    # the plan still parses and the graph is unchanged
    assert json.loads(pt("taskgraph", "--plan", p).out) == {"T1": [], "T2": ["T1"]}
    # a changed plan replaces the old numbers
    p.write_text(text.replace("**Est. time:** 2 min", "**Est. time:** 1 min"), encoding="utf-8")
    pt("schedule", "--plan", p, "--insert")
    again = p.read_text(encoding="utf-8")
    assert again.count("%% schedule") == 1 and "predicted build 4.0 min" in again


def test_schedule_target_from_header_and_flag_override(tmp_path):
    p = make_plan(tmp_path, [{"id": "T1", "time": 3}], header="**Target:** 4 min")
    assert sched(p)["target_min"] == 4.0
    assert sched(p)["target_met"] is False  # setup + scaled build + wrap-up >> 4
    assert sched(p, "--target-min", 30)["target_met"] is True


def test_schedule_uses_predictor_without_stated_time(tmp_path):
    p = make_plan(tmp_path, [{"id": "T1", "features": "components=1 · loc=120 · tests=slow · model=sonnet"}])
    rep = sched(p)
    assert rep["tasks"]["T1"]["source"] == "features"
    # shipped check value 2.354 * time.scale 0.9
    assert 2.0 <= rep["tasks"]["T1"]["min"] <= 2.3


# ----------------------------------------------------------------- lint

def test_lint_clean_fixture():
    cp = pt("lint", "--plan", FIXTURES / "minisheet.md")
    assert cp.returncode == 0, cp.both
    assert "MISSED TARGET" not in cp.out
    assert "lint: ok" in cp.out


def chain_plan(tmp_path, **extra):
    tasks = [{"id": "T1", "time": 3}, {"id": "T5", "time": 3}, {"id": "T6", "time": 2.5}, {"id": "T9", "time": 2}]
    extra.setdefault("header", "Ruling: sequential - test fixture is a deliberate chain")
    return make_plan(tmp_path, tasks, [("T1", "T5"), ("T5", "T6"), ("T6", "T9")], **extra)


def test_lint_warns_when_predicted_misses_target(tmp_path):
    p = chain_plan(tmp_path)
    cp = pt("lint", "--plan", p, "--target-min", 11)
    assert cp.returncode == 0, cp.both
    assert ("MISSED TARGET: predicted 22.9 min vs target 11.0 min — critical path "
            "T1 → T5 → T6 → T9") in cp.out
    assert "lint: ok" in cp.out


def test_lint_other_errors_still_exit_1_with_target_warning(tmp_path):
    p = chain_plan(tmp_path)
    p.write_text(p.read_text(encoding="utf-8") + "\nImplementation: TBD, handle edge cases.\n", encoding="utf-8")
    cp = pt("lint", "--plan", p, "--target-min", 11)
    assert cp.returncode == 1
    assert "MISSED TARGET" in cp.out
    assert "placeholder TBD" in cp.out and "placeholder 'handle edge cases'" in cp.out


def test_lint_placeholder_inside_code_fence_is_ignored(tmp_path):
    p = make_plan(tmp_path, [{"id": "T1", "time": 2}])
    p.write_text(p.read_text(encoding="utf-8") + "\n```python\n# TODO in verbatim test\n```\n", encoding="utf-8")
    assert pt("lint", "--plan", p).returncode == 0


def test_lint_size_band_errors_above_high_and_notes_below_low(tmp_path):
    p = make_plan(tmp_path, [{"id": "T1", "time": 6.5}, {"id": "T2", "time": 0.5, "ctx": 30}])
    cp = pt("lint", "--plan", p)
    assert cp.returncode == 1
    assert "SIZE: T1 predicted 6.5 min > 6 max; split it along its components" in cp.out
    assert "NOTE: T2 predicted 0.5 min < 1.5; consider merging with a related task" in cp.out
    assert "mergeable" not in cp.out and "-min cap" not in cp.out


def test_lint_flags_consumes_mismatch(tmp_path):
    tasks = [
        {"id": "T1", "time": 2, "produces": "evaluate(formula: str, get_cell) -> Value"},
        {"id": "T2", "time": 2, "consumes": "evaluate(formula) -> Value (T1)"},
        {"id": "T3", "time": 2, "consumes": "missing_fn(x) -> int (T1)"},
    ]
    cp = pt("lint", "--plan", make_plan(tmp_path, tasks, [("T1", "T2")]))
    assert cp.returncode == 1
    assert "T2 consumes evaluate(formula) but T1 produces evaluate(formula: str, get_cell)" in cp.out
    assert "T3 consumes missing_fn from T1, but no Produces line defines it" in cp.out


def test_lint_flags_parallel_tasks_sharing_a_file(tmp_path):
    tasks = [{"id": "T1", "time": 2, "owns": "src/shared.py"}, {"id": "T2", "time": 2, "owns": "src/shared.py"}]
    cp = pt("lint", "--plan", make_plan(tmp_path, tasks))
    assert cp.returncode == 1
    assert "T1 and T2 can run in parallel but both own src/shared.py" in cp.out


def test_lint_flags_bad_graph_and_diagram(tmp_path):
    cp = pt("lint", "--plan", FIXTURES / "cycle.md")
    assert cp.returncode == 1 and "cycle" in cp.out
    p = make_plan(tmp_path, [{"id": "T1", "time": 2}])
    p.write_text(p.read_text(encoding="utf-8") + "\n```mermaid\nflowchart LR\n  A[open --> B\n```\n", encoding="utf-8")
    cp = pt("lint", "--plan", p)
    assert cp.returncode == 1 and "unbalanced []" in cp.out


def test_lint_size_caps(tmp_path):
    p = make_plan(tmp_path, [{"id": "T1", "time": 2, "ctx": 85}])
    cp = pt("lint", "--plan", p, "--max-chars", 100)
    assert "over the 100 cap" in cp.out
    assert "T1: Est. context 85k is over the 80k ceiling" in cp.out


def test_lint_spec_sections_without_task(tmp_path):
    cp = pt("lint", "--plan", FIXTURES / "minisheet.md", "--spec", FIXTURES / "spec.md")
    assert cp.returncode == 1
    assert "SPEC section '11. Printing' has no task" in cp.out
    assert "SPEC section '1. Formula evaluation' has no task" in cp.out
    assert "Workbook" not in cp.out and "10. Errors" not in cp.out  # covered by title and by SPEC §10


# ------------------------------------------------------------ dispatcher

def test_review_subcommands_route_to_pt_review(monkeypatch):
    import plan_tools
    calls = []
    fake = types.ModuleType("pt_review")
    fake.main = lambda argv: calls.append(list(argv)) or 0
    monkeypatch.setitem(sys.modules, "pt_review", fake)
    for sub in ("adapter", "packet", "contract"):
        assert plan_tools.main([sub, "--plan", "x.md"]) == 0
    assert calls == [[s, "--plan", "x.md"] for s in ("adapter", "packet", "contract")]


def test_pt_review_is_only_imported_on_demand():
    cp = run(PLAN_TOOLS, "taskgraph", "--plan", FIXTURES / "minisheet.md")
    assert cp.returncode == 0, cp.both  # works even when pt_review.py does not exist yet


# -------------------------------------------------------------- predict

def predict_json(*args):
    cp = run(PREDICT, *args)
    assert cp.returncode == 0, cp.both
    return json.loads(cp.out)


def test_predict_anchors_match_e2(tmp_path):
    tasks = [
        {"id": "T1", "features": "components=1 · loc=120 · tests=slow"},
        {"id": "T2", "features": "components=1 · loc=60 · tests=fast"},
        {"id": "T3", "features": "components=1 · loc=100 · tests=fast"},
        {"id": "T4", "features": "components=1 · loc=150 · tests=fast"},
    ]
    data = predict_json("--plan", make_plan(tmp_path, tasks))["tasks"]
    assert data["T1"]["est_time_min"] == pytest.approx(2.12, abs=0.15)  # 2.354 * scale 0.9
    for tid in ("T2", "T3", "T4"):
        assert 1.4 <= data[tid]["est_time_min"] <= 1.8, (tid, data[tid])
    assert data["T1"]["est_time_min"] > data["T3"]["est_time_min"]


def test_predict_reads_features_from_est_time_parenthetical():
    data = predict_json("--plan", FIXTURES / "minisheet.md")["tasks"]
    assert data["T7"]["features"] == {"components": 1, "loc": 120, "tests": "slow"}
    assert data["T7"]["stated_time_min"] == 2.5
    assert data["T7"]["est_time_min"] == pytest.approx(2.12, abs=0.15)
    assert data["T7"]["est_context_k"] > 18


def test_predict_totals(tmp_path):
    out = predict_json("--plan", FIXTURES / "minisheet.md")
    t = out["totals"]
    assert t["tasks"] == 4
    assert t["sum_task_min"] == pytest.approx(sum(v["est_time_min"] for v in out["tasks"].values()), abs=0.01)
    assert t["cost_usd"]["total"] == pytest.approx(sum(v["cost"]["total"] for v in out["tasks"].values()), abs=1e-4)


def test_predict_uses_price_file(tmp_path):
    p = make_plan(tmp_path, [{"id": "T1", "features": "components=1 · loc=120 · tests=slow · model=sonnet"},
                             {"id": "T2", "features": "components=1 · loc=120 · tests=slow · model=opus"}])
    base = predict_json("--plan", p)["tasks"]
    models = json.loads((CONFIG / "models.json").read_text(encoding="utf-8"))
    models["models"]["sonnet"]["input"] *= 2
    mp = tmp_path / "models.json"
    mp.write_text(json.dumps(models), encoding="utf-8")
    new = predict_json("--plan", p, "--models", mp)["tasks"]
    assert new["T1"]["cost"]["input"] == pytest.approx(2 * base["T1"]["cost"]["input"])
    assert new["T1"]["cost"]["output"] == base["T1"]["cost"]["output"]
    assert new["T2"]["cost"] == base["T2"]["cost"]  # opus untouched
    assert new["T1"]["cost"]["total"] > base["T1"]["cost"]["total"]


def test_models_file_has_current_prices():
    m = json.loads((CONFIG / "models.json").read_text(encoding="utf-8"))["models"]
    assert (m["opus"]["input"], m["opus"]["output"], m["opus"]["cache_read"]) == (4.00, 20.00, 0.20)
    assert (m["sonnet"]["input"], m["sonnet"]["output"], m["sonnet"]["cache_read"]) == (2.00, 10.00, 0.10)
    assert (m["haiku"]["input"], m["haiku"]["output"], m["haiku"]["cache_read"]) == (0.10, 0.50, 0.01)
    for v in m.values():
        assert v["cache_write_5m"] == pytest.approx(1.25 * v["input"])
        assert v["cache_write_1h"] == pytest.approx(2 * v["input"])
        assert v["output_tps"] > 0


def test_predict_opus_is_slower_and_dearer(tmp_path):
    f = "components=1 · loc=200 · tests=fast · model="
    p = make_plan(tmp_path, [{"id": "T1", "features": f + "sonnet"}, {"id": "T2", "features": f + "opus"},
                             {"id": "T3", "features": f + "haiku"}])
    d = predict_json("--plan", p)["tasks"]
    assert d["T2"]["est_time_min"] > d["T1"]["est_time_min"] > d["T3"]["est_time_min"]
    assert d["T2"]["cost"]["total"] > d["T1"]["cost"]["total"] > d["T3"]["cost"]["total"]


def _results(tmp_path):
    rows = []
    for comps, loc, slow in [(1, 40, 0), (1, 80, 0), (1, 120, 1), (2, 150, 0), (2, 200, 1), (1, 60, 1),
                             (3, 260, 1), (1, 160, 0), (2, 90, 0), (1, 220, 1), (2, 120, 1), (1, 30, 0)]:
        actual = 0.5 + 0.3 * comps + 0.012 * loc + 0.9 * slow
        rows.append({"task": f"t{len(rows)}", "components": comps, "loc": loc, "tests": "slow" if slow else "fast",
                     "model": "sonnet", "actual_s": actual * 60})
    f = tmp_path / "bench_results.json"
    f.write_text(json.dumps({"tasks": rows}), encoding="utf-8")
    return f


def test_predict_refit_improves_error(tmp_path):
    results = _results(tmp_path)
    coef = tmp_path / "predictor.json"
    shutil.copy(CONFIG / "predictor.json", coef)
    out = predict_json("--refit", results, "--coef", coef)
    assert out["fit"]["n"] == 12
    assert out["fit"]["mae_after_min"] < out["fit"]["mae_before_min"]
    assert out["fit"]["mae_after_min"] < 0.15
    saved = json.loads(coef.read_text(encoding="utf-8"))
    assert saved["time"]["per_loc_min"] > json.loads((CONFIG / "predictor.json").read_text(encoding="utf-8"))["time"]["per_loc_min"]
    assert saved["fit"]["n"] == 12
    # predictions from the refitted file are closer to the data than the prior
    p = make_plan(tmp_path, [{"id": "T1", "features": "components=2 · loc=200 · tests=slow"}])
    truth = 0.5 + 0.6 + 0.012 * 200 + 0.9
    before = predict_json("--plan", p)["tasks"]["T1"]["est_time_min"]
    after = predict_json("--plan", p, "--coef", coef)["tasks"]["T1"]["est_time_min"]
    assert abs(after - truth) < abs(before - truth)


def test_predict_refit_dry_run_does_not_write(tmp_path):
    coef = tmp_path / "predictor.json"
    shutil.copy(CONFIG / "predictor.json", coef)
    original = coef.read_text(encoding="utf-8")
    out = predict_json("--refit", _results(tmp_path), "--coef", coef, "--dry-run")
    assert out["written"] is None
    assert coef.read_text(encoding="utf-8") == original


def test_predict_refit_needs_data(tmp_path):
    f = tmp_path / "few.json"
    f.write_text(json.dumps([{"loc": 10, "actual_min": 1.0}]), encoding="utf-8")
    cp = run(PREDICT, "--refit", f, "--coef", CONFIG / "predictor.json", "--dry-run")
    assert cp.returncode == 2 and "at least 4" in cp.err


# ------------------------------------------------- writing-plans skill format

def test_schedule_target_accepts_both_header_forms(tmp_path):
    p1 = make_plan(tmp_path, [{"id": "T1", "time": 3}], header="**Target-min:** 4", name="a.md")
    p2 = make_plan(tmp_path, [{"id": "T1", "time": 3}], header="**Target:** 4 min", name="b.md")
    p3 = make_plan(tmp_path, [{"id": "T1", "time": 3}], header="**Target-min:** 30", name="c.md")
    assert sched(p1)["target_min"] == 4.0 and sched(p1)["target_met"] is False
    assert sched(p2)["target_min"] == 4.0
    assert sched(p3)["target_met"] is True
    cp = pt("lint", "--plan", p1)
    # 5 + 0.75*3 + 10 = 17.25
    assert cp.returncode == 0 and "MISSED TARGET: predicted 17.2 min vs target 4.0 min" in cp.out


SKILL_FIELDS = {"components": 1, "loc": 120, "tests": "slow", "model": "sonnet"}
# Same time prediction, but docs=30000 gives a predicted context of about 40k (not mergeable).
SKILL_BIG = {"features": "components=1 loc=120 tests=slow model=sonnet docs=30000"}


def test_skill_fields_are_features(tmp_path):
    p = make_plan(tmp_path, [dict(id="T1", time=9, **SKILL_FIELDS)])
    t = predict_json("--plan", p)["tasks"]["T1"]
    assert t["features"] == {"components": 1, "loc": 120, "tests": "slow", "model": "sonnet"}
    assert t["source"] == "features"
    for model in ("opus", "haiku"):
        q = make_plan(tmp_path, [dict(id="T1", **dict(SKILL_FIELDS, model=model))], name=model + ".md")
        assert predict_json("--plan", q)["tasks"]["T1"]["model"] == model


def test_tests_list_is_not_mistaken_for_speed(tmp_path):
    p = make_plan(tmp_path, [{"id": "T1", "tests": "`test_a`: slow path returns 1; `test_b`: fast path"}])
    t = predict_json("--plan", p)["tasks"]["T1"]
    assert t["source"] == "defaults"  # a test list is not a speed; nothing was read as a feature


def test_predictor_wins_over_stated_time_when_features_present(tmp_path):
    p = make_plan(tmp_path, [dict(id="T1", time=9, **SKILL_FIELDS), {"id": "T2", "time": 1.5}], [("T1", "T2")])
    rep = sched(p)
    assert rep["tasks"]["T1"]["source"] == "features"
    assert rep["tasks"]["T1"]["min"] == pytest.approx(2.12, abs=0.15)  # not the stated 9
    assert rep["tasks"]["T2"]["source"] == "stated" and rep["tasks"]["T2"]["min"] == 1.5  # no features: stated
    forced = sched(p, "--stated")
    assert forced["tasks"]["T1"]["min"] == 9 and forced["tasks"]["T1"]["source"] == "stated"


def test_lint_cap_uses_predicted_time_over_stated(tmp_path):
    # stated 7 min would break the 6-min band, but the features predict ~2.5
    p = make_plan(tmp_path, [dict(id="T1", time=7, **SKILL_BIG)])
    assert pt("lint", "--plan", p).returncode == 0
    assert pt("lint", "--plan", p, "--stated").returncode == 1


def test_lint_warns_without_features_or_time(tmp_path):
    p = make_plan(tmp_path, [{"id": "T1"}, dict(id="T2", ctx=40, **SKILL_BIG), {"id": "T3", "time": 2}])
    cp = pt("lint", "--plan", p)
    assert cp.returncode == 0, cp.both
    assert "WARNING: T1 has no features" in cp.out
    assert "T2 has no features" not in cp.out and "T3 has no features" not in cp.out


def _pred_ctx(tmp_path):
    p = make_plan(tmp_path, [dict(id="T1", ctx=40, **SKILL_BIG)], name="probe_ctx.md")
    return predict_json("--plan", p)["tasks"]["T1"]["est_context"]


def test_predicted_context_wins_over_stated_when_features_present(tmp_path):
    assert _pred_ctx(tmp_path) > 36000
    # stated 24k would make T1 mergeable (variable 6k < 18k); the predicted ~40k does not
    p = make_plan(tmp_path, [dict(id="T1", ctx=24, **SKILL_BIG), {"id": "T2", "ctx": 24}], [("T1", "T2")])
    data = json.loads(pt("taskgraph", "--plan", p, "--format", "full").out)
    assert [m["task"] for m in data["mergeable"]] == ["T2"]       # T2 has no features: stated 24k used
    forced = json.loads(pt("taskgraph", "--plan", p, "--format", "full", "--stated").out)
    assert "T1" in [m["task"] for m in forced["mergeable"]]


def test_lint_notes_stated_context_off_by_more_than_25_percent(tmp_path):
    pred = _pred_ctx(tmp_path)
    near = round(pred / 1000)
    p = make_plan(tmp_path, [dict(id="T1", ctx=near, **SKILL_BIG)], header="**Ignore:** .env", name="near.md")
    cp = pt("lint", "--plan", p)
    assert cp.returncode == 0 and "NOTE" not in cp.out, cp.out
    far = int(pred / 1000 * 0.5)
    p = make_plan(tmp_path, [dict(id="T1", ctx=far, **SKILL_BIG)], header="**Ignore:** .env", name="far.md")
    cp = pt("lint", "--plan", p)
    assert cp.returncode == 0 and f"NOTE: T1 states Est. context {far}k" in cp.out and "predicted value is used" in cp.out


def test_lint_context_ceiling_uses_predicted_when_features_present(tmp_path):
    # stated 85k would break the 80k ceiling, but the features predict about 40k
    p = make_plan(tmp_path, [dict(id="T1", ctx=85, **SKILL_BIG)])
    cp = pt("lint", "--plan", p)
    assert "ceiling" not in cp.out and "NOTE: T1 states Est. context 85k" in cp.out
    assert "T1: Est. context 85k is over the 80k ceiling" in pt("lint", "--plan", p, "--stated").out
