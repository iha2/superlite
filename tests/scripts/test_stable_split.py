"""Size band, `lint --stable`, interface freeze and stable.json-aware dispatch."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from test_plan_tools import CONFIG, PREDICT, SCRIPTS, make_plan, pt, run

DISPATCH = SCRIPTS / "dispatch.py"
NEXT_TASKS = SCRIPTS / "next_tasks.py"

PROD = "f(x: int) -> int"


def stable_json(tmp_path):
    return json.loads((tmp_path / ".superlite" / "stable.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- size band

def test_config_has_size_band():
    band = json.loads((CONFIG / "predictor.json").read_text(encoding="utf-8"))["size_band"]
    assert band == {"low_min": 1.5, "high_min": 6.0}


def test_band_allows_tasks_the_old_3_min_cap_rejected(tmp_path):
    p = make_plan(tmp_path, [{"id": "T1", "time": 4}, {"id": "T2", "time": 5.9}])
    cp = pt("lint", "--plan", p)
    assert cp.returncode == 0, cp.both
    assert "SIZE" not in cp.out


def test_band_size_error_above_high(tmp_path):
    cp = pt("lint", "--plan", make_plan(tmp_path, [{"id": "T1", "time": 6.5}]))
    assert cp.returncode == 1
    assert "SIZE: T1 predicted 6.5 min > 6 max; split it along its components" in cp.out


def test_band_merge_note_never_fails(tmp_path):
    cp = pt("lint", "--plan", make_plan(tmp_path, [{"id": "T1", "time": 0.5}, {"id": "T2", "time": 1.0}]))
    assert cp.returncode == 0, cp.both
    assert "NOTE: T1 predicted 0.5 min < 1.5; consider merging with a related task" in cp.out
    assert "NOTE: T2 predicted 1.0 min < 1.5; consider merging with a related task" in cp.out
    assert "lint: ok" in cp.out


def _results(tmp_path, secs, overhead_s=None):
    recs = []
    for i, s in enumerate(secs):
        r = {"task": f"T{i + 1}", "components": 1 + i % 3, "loc": 100 + 40 * i, "actual_s": s}
        if overhead_s is not None:
            r["start_overhead_s"] = overhead_s[i]
        recs.append(r)
    f = tmp_path / "res.json"
    f.write_text(json.dumps({"tasks": recs}), encoding="utf-8")
    return f


def _refit(tmp_path, results):
    coef = tmp_path / "predictor.json"
    shutil.copy(CONFIG / "predictor.json", coef)
    cp = run(PREDICT, "--refit", results, "--coef", coef)
    assert cp.returncode == 0, cp.both
    return json.loads(coef.read_text(encoding="utf-8"))["size_band"]


def test_refit_sets_high_from_p90_and_keeps_low_without_overhead(tmp_path):
    band = _refit(tmp_path, _results(tmp_path, [101, 108, 207, 290, 302, 445]))
    assert band == {"low_min": 1.5, "high_min": 6.0}  # p90 6.2 min -> 6.0


def test_refit_clamps_high_to_4_and_8(tmp_path):
    assert _refit(tmp_path, _results(tmp_path, [60, 70, 80, 90, 100]))["high_min"] == 4.0
    assert _refit(tmp_path, _results(tmp_path, [900, 950, 1000, 1100, 1200]))["high_min"] == 8.0


def test_refit_low_is_2_5x_median_overhead(tmp_path):
    secs = [101, 108, 207, 290, 302, 445]
    band = _refit(tmp_path, _results(tmp_path, secs, overhead_s=[30, 36, 42, 48, 54, 60]))
    assert band["low_min"] == 1.88  # median 45 s = 0.75 min, x2.5
    assert band["high_min"] == 6.0


# ---------------------------------------------------------------- stable split

def _split_plan(tmp_path, edges):
    """T1 is unstable (consumes something nobody produces) but produces f; T2 and T3 consume f."""
    tasks = [
        {"id": "T1", "time": 2, "produces": PROD, "consumes": "ghost(x: int) -> int"},
        {"id": "T2", "time": 2, "consumes": f"{PROD} (T1)"},
        {"id": "T3", "time": 2, "consumes": f"{PROD} (T1)"},
        {"id": "T4", "time": 2},
    ]
    return make_plan(tmp_path, tasks, edges)


def test_stable_unstable_root_reached_only_through_consumes(tmp_path):
    cp = pt("lint", "--stable", "--plan", _split_plan(tmp_path, []))
    assert cp.returncode == 0, cp.both
    data = stable_json(tmp_path)
    assert data["stable"] == ["T2", "T3", "T4"]
    assert [f["task"] for f in data["fix"]] == ["T1"]
    assert "ghost" in data["fix"][0]["reasons"][0]
    assert data["interfaces"]["T2"] == {"consumes": [PROD], "produces": []}
    assert "T1" not in data["interfaces"]
    assert "STABLE: T2 T3 T4" in cp.out


def test_stable_real_edge_to_unstable_task_blocks_dependents(tmp_path):
    cp = pt("lint", "--stable", "--plan", _split_plan(tmp_path, [("T1", "T2"), ("T2", "T3")]))
    assert cp.returncode == 0, cp.both
    data = stable_json(tmp_path)
    assert data["stable"] == ["T4"]
    reasons = {f["task"]: f["reasons"] for f in data["fix"]}
    assert "depends on unstable T1" in reasons["T2"]
    assert "depends on unstable T2" in reasons["T3"]


def test_stable_plan_level_issues_do_not_block(tmp_path):
    p = make_plan(tmp_path, [{"id": "T1", "time": 2}, {"id": "T2", "time": 2}], header="**Target:** 1 min")
    spec = tmp_path / "spec.md"
    spec.write_text("# Spec\n\n## Uncovered Widget\n", encoding="utf-8")
    cp = pt("lint", "--stable", "--plan", p, "--spec", spec)
    data = stable_json(tmp_path)
    assert data["stable"] == ["T1", "T2"] and data["fix"] == []
    assert any(i.startswith("SPEC section") for i in data["plan_issues"])
    assert any(i.startswith("MISSED TARGET") for i in data["plan_issues"])
    assert cp.returncode == 0


def test_stable_size_error_is_attributed_structurally(tmp_path):
    p = make_plan(tmp_path, [{"id": "T1", "time": 7}, {"id": "T2", "time": 2}])
    pt("lint", "--stable", "--plan", p)
    data = stable_json(tmp_path)
    assert data["stable"] == ["T2"]
    assert data["fix"][0]["task"] == "T1" and data["fix"][0]["reasons"][0].startswith("SIZE: T1")


def test_stable_flagged_chain_members_are_unstable(tmp_path):
    tasks = [{"id": f"T{i}", "time": 1.5} for i in range(1, 6)] + [{"id": f"T{i}", "time": 2} for i in range(6, 16)]
    pt("lint", "--stable", "--plan", make_plan(tmp_path, tasks, [("T1", "T2"), ("T2", "T3"), ("T3", "T4"), ("T4", "T5")]))
    data = stable_json(tmp_path)
    assert "T6" in data["stable"]
    assert {f["task"] for f in data["fix"]} == {"T1", "T2", "T3", "T4", "T5"}
    assert any(i.startswith("CHAIN") for i in data["plan_issues"])
    assert "on flagged CHAIN" in data["fix"][0]["reasons"]


# ---------------------------------------------------------------- interface freeze

def _two(tmp_path, produces):
    return make_plan(tmp_path, [{"id": "T1", "time": 2, "produces": produces}, {"id": "T2", "time": 2}])


def test_frozen_interface_change_is_reported_and_old_entry_kept(tmp_path):
    assert pt("lint", "--stable", "--plan", _two(tmp_path, PROD)).returncode == 0
    cp = pt("lint", "--stable", "--plan", _two(tmp_path, "f(x: int, y: int) -> int"))
    assert cp.returncode == 1
    assert ("FROZEN: T1 interface changed after dispatch: Produces: f(x: int) -> int; Consumes: - -> "
            "Produces: f(x: int, y: int) -> int; Consumes: -; add a follow-up fix task") in cp.out
    data = stable_json(tmp_path)
    assert data["interfaces"]["T1"]["produces"] == [PROD]
    assert "T1" in data["stable"]
    assert any(i.startswith("FROZEN: T1") for i in data["plan_issues"])


def test_frozen_applies_to_ledger_dispatched_tasks(tmp_path):
    pt("lint", "--stable", "--plan", _two(tmp_path, PROD))
    f = tmp_path / ".superlite" / "stable.json"
    data = json.loads(f.read_text(encoding="utf-8"))
    data["stable"] = ["T2"]  # T1 no longer listed, but the ledger says it was dispatched
    f.write_text(json.dumps(data), encoding="utf-8")
    (tmp_path / ".superlite" / "progress.md").write_text("# Progress ledger\n\n- commit T1 abc1234567 task t1\n", encoding="utf-8")
    cp = pt("lint", "--stable", "--plan", _two(tmp_path, "f(x: str) -> int"))
    assert "FROZEN: T1 interface changed after dispatch" in cp.out


def test_unchanged_interface_is_not_frozen(tmp_path):
    p = _two(tmp_path, PROD)
    pt("lint", "--stable", "--plan", p)
    cp = pt("lint", "--stable", "--plan", p)
    assert cp.returncode == 0 and "FROZEN" not in cp.out


# ---------------------------------------------------------------- dispatch honours stable.json

def _roots(tmp_path):
    return make_plan(tmp_path, [{"id": "T1", "time": 2}, {"id": "T2", "time": 2}, {"id": "T3", "time": 2}])


def _lines(cp):
    return [ln.split()[0] for ln in cp.out.splitlines() if ln.strip() and not ln.startswith("T0 ")]


def test_dispatch_and_next_tasks_offer_everything_without_stable_json(tmp_path):
    p = _roots(tmp_path)
    assert sorted(_lines(run(NEXT_TASKS, "--plan", p, cwd=tmp_path))) == ["T1", "T2", "T3"]
    cp = run(DISPATCH, "--plan", p, "--pool", tmp_path / "pool.json", "--ledger", tmp_path / "l.md", "--batch", "1", cwd=tmp_path)
    assert sorted(_lines(cp)) == ["T1", "T2", "T3"], cp.both


def test_dispatch_and_next_tasks_offer_only_stable_tasks(tmp_path):
    p = _roots(tmp_path)
    (tmp_path / ".superlite").mkdir()
    (tmp_path / ".superlite" / "stable.json").write_text(json.dumps({"stable": ["T1", "T3"]}), encoding="utf-8")
    assert sorted(_lines(run(NEXT_TASKS, "--plan", p, cwd=tmp_path))) == ["T1", "T3"]
    cp = run(DISPATCH, "--plan", p, "--pool", tmp_path / "pool.json", "--ledger", tmp_path / "l.md", "--batch", "1", cwd=tmp_path)
    assert sorted(_lines(cp)) == ["T1", "T3"], cp.both


def test_stable_json_is_anded_with_the_draft_filter(tmp_path):
    p = _roots(tmp_path)
    text = p.read_text(encoding="utf-8").replace("### T3 task t3", "### T3 task t3 · Status: draft", 1)
    text = text.replace("**Owns:** src/pkg/t3.py", "**Status:** draft\n**Owns:** src/pkg/t3.py")
    p.write_text(text, encoding="utf-8")
    (tmp_path / ".superlite").mkdir()
    (tmp_path / ".superlite" / "stable.json").write_text(json.dumps({"stable": ["T2", "T3"]}), encoding="utf-8")
    assert _lines(run(NEXT_TASKS, "--plan", p, cwd=tmp_path)) == ["T2"]


def test_producer_note_after_task_ids_is_not_part_of_the_signature():
    # w3 first draft: "... -> HTTPServer (T14, names only)" was read as return type "HTTPServer (T14, names only)".
    import plan_tools as pt
    sig = pt.parse_signature("minisheet.server.create_server(wb, port: int = 0) -> HTTPServer (T14, names only)")
    assert sig["args"] == "wb, port: int = 0"
    assert sig["ret"] == "HTTPServer"
    assert pt.parse_signature("f(x) -> int (T4, T5)")["ret"] == "int"


# ---------------------------------------------------------------- DISPATCH NOW, lint log, rewrites

def _sl(tmp_path):
    return tmp_path / ".superlite"


def _log(tmp_path):
    return [json.loads(ln) for ln in (_sl(tmp_path) / "lint-log.jsonl").read_text(encoding="utf-8").splitlines()]


def _pool(tmp_path, *tasks):
    (_sl(tmp_path) / "pool.json").write_text(json.dumps(
        {"agents": [{"id": "a1", "tasks": list(tasks), "current": tasks[-1] if tasks else None, "busy": True}]}), encoding="utf-8")


def _consumer_plan(tmp_path, t1_produces=PROD, t3_consumes="f(x: int) -> int", t3_title="task t3"):
    tasks = [{"id": "T1", "time": 2, "produces": t1_produces}, {"id": "T2", "time": 2},
             {"id": "T3", "time": 2, "title": t3_title, "consumes": t3_consumes}]
    return make_plan(tmp_path, tasks)


def test_dispatch_now_line_and_lint_log(tmp_path):
    cp = pt("lint", "--stable", "--plan", _two(tmp_path, PROD))
    assert cp.out.rstrip().splitlines()[-1] == "DISPATCH NOW: T1 T2"
    (e,) = _log(tmp_path)
    assert (e["stable"], e["fix"]) == (2, 0)
    assert e["newly_stable"] == ["T1", "T2"] and e["dispatched"] == [] and e["pending"] == ["T1", "T2"]
    assert "time" in e


def test_dispatch_now_skips_dispatched_and_none_when_empty(tmp_path):
    p = _two(tmp_path, PROD)
    pt("lint", "--stable", "--plan", p)
    _pool(tmp_path, "T1")
    cp = pt("lint", "--stable", "--plan", p)
    assert cp.out.rstrip().splitlines()[-1] == "DISPATCH NOW: T2"
    _pool(tmp_path, "T1", "T2")
    cp = pt("lint", "--stable", "--plan", p)
    assert cp.out.rstrip().splitlines()[-1] == "DISPATCH NOW: none"
    last = _log(tmp_path)[-1]
    assert last["dispatched"] == ["T1", "T2"] and last["pending"] == [] and last["newly_stable"] == []


def test_fix_loop_newly_stable_listed_once(tmp_path):
    p = make_plan(tmp_path, [{"id": "T1", "time": 2}, {"id": "T2", "time": 9}, {"id": "T3", "time": 2}])  # T2 oversize
    cp = pt("lint", "--stable", "--plan", p)
    assert cp.out.rstrip().splitlines()[-1] == "DISPATCH NOW: T1 T3" and "T2:" in cp.out
    _pool(tmp_path, "T1", "T3")
    make_plan(tmp_path, [{"id": "T1", "time": 2}, {"id": "T2", "time": 3}, {"id": "T3", "time": 2}])  # edit T2 only
    cp = pt("lint", "--stable", "--plan", p)
    assert cp.out.rstrip().splitlines()[-1] == "DISPATCH NOW: T2"
    assert _log(tmp_path)[-1]["newly_stable"] == ["T2"]


def test_whole_file_rewrite_keeping_dispatched_blocks_is_not_frozen(tmp_path):
    p = _consumer_plan(tmp_path, t3_consumes="g(y: int) -> int")  # T3 consumes nothing produced: FIX
    cp = pt("lint", "--stable", "--plan", p)
    assert cp.out.rstrip().splitlines()[-1] == "DISPATCH NOW: T1 T2"
    _pool(tmp_path, "T1", "T2")
    p = _consumer_plan(tmp_path)  # whole file rewritten, T1 and T2 blocks byte-identical
    cp = pt("lint", "--stable", "--plan", p)
    assert "FROZEN" not in cp.out and cp.returncode == 0, cp.both
    assert cp.out.rstrip().splitlines()[-1] == "DISPATCH NOW: T3"


def test_whole_file_rewrite_changing_dispatched_block_is_frozen(tmp_path):
    p = make_plan(tmp_path, [{"id": "T1", "time": 2, "title": "first"}, {"id": "T2", "time": 2}])
    pt("lint", "--stable", "--plan", p)
    _pool(tmp_path, "T1")
    p = make_plan(tmp_path, [{"id": "T1", "time": 3, "title": "first reworded"}, {"id": "T2", "time": 2}])
    cp = pt("lint", "--stable", "--plan", p)
    assert cp.returncode == 1
    assert "FROZEN: T1 block changed after dispatch" in cp.out
    assert cp.out.rstrip().splitlines()[-1] == "DISPATCH NOW: T2"
    assert _log(tmp_path)[-1]["frozen"] == 1


def test_undispatched_stable_block_may_change(tmp_path):
    p = make_plan(tmp_path, [{"id": "T1", "time": 2, "title": "first"}])
    pt("lint", "--stable", "--plan", p)
    p = make_plan(tmp_path, [{"id": "T1", "time": 3, "title": "first reworded"}])
    cp = pt("lint", "--stable", "--plan", p)
    assert "FROZEN" not in cp.out and cp.returncode == 0


def test_rewrite_dropping_a_dispatched_block_is_frozen(tmp_path):
    p = make_plan(tmp_path, [{"id": "T1", "time": 2}, {"id": "T2", "time": 2}])
    pt("lint", "--stable", "--plan", p)
    _pool(tmp_path, "T1")
    p = make_plan(tmp_path, [{"id": "T2", "time": 2}])
    cp = pt("lint", "--stable", "--plan", p)
    assert "FROZEN: T1 dispatched block was removed" in cp.out
