"""metrics.py, history.py and budget.py: per-task records from hook lines, the history store, stats, the budget line."""
from __future__ import annotations

import json
import os
import time

from test_plan_tools import SCRIPTS, run
from test_routing import mech_plan, mixed, plan, task

METRICS = SCRIPTS / "metrics.py"
HISTORY = SCRIPTS / "history.py"
BUDGET = SCRIPTS / "budget.py"
SONNET = "claude-sonnet-5-5"
HAIKU = "claude-haiku-5-5"


def transcript(path, turns, model=SONNET):
    """turns: [(task heading text, [(tool name, text)] , status text)] -> an agent JSONL."""
    lines, n = [], 0
    for i, (head, tools, final) in enumerate(turns):
        t0 = 1_700_000_000 + i * 100
        lines.append({"type": "user", "timestamp": f"2026-10-18T10:{(t0 // 60) % 60:02d}:{t0 % 60:02d}Z", "message": {"role": "user", "content": head}})
        for k, (name, txt) in enumerate(tools + [(None, final)]):
            n += 1
            content = [{"type": "tool_use", "id": f"tu{n}", "name": name, "input": {"x": 1}}] if name else [{"type": "text", "text": txt}]
            lines.append({"type": "assistant", "timestamp": f"2026-10-18T10:{((t0 + 10 * (k + 1)) // 60) % 60:02d}:{(t0 + 10 * (k + 1)) % 60:02d}Z",
                          "message": {"id": f"m{n}", "model": model, "content": content,
                                      "usage": {"input_tokens": 100, "output_tokens": 200, "cache_read_input_tokens": 1000,
                                                "cache_creation_input_tokens": 500}}})
            if name:  # a repeated snapshot of the same message id must not double count
                lines.append(dict(lines[-1]))
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")


def setup_run(tmp_path, p=None):
    sl = tmp_path / ".superlite"
    sl.mkdir(exist_ok=True)
    ta, tb = tmp_path / "a1.jsonl", tmp_path / "b1.jsonl"
    transcript(ta, [("Implement T1 slug", [("Bash", ""), ("Edit", "")], "Status: DONE. tests pass"),
                    ("Implement T2 feed", [("Bash", "")], "Status: DONE. ok")], model=HAIKU)  # one reused haiku agent, two tasks
    transcript(tb, [("Implement T9 big", [("Bash", ""), ("Bash", ""), ("Edit", "")], "Status: BLOCKED. need decision")])
    rows = [{"ev": "launch", "t": 1_700_000_000_000, "agent_id": "a1", "type": "superlite:implementer-haiku", "model": "haiku", "desc": "T1 slug"},
            {"ev": "launch", "t": 1_700_000_005_000, "agent_id": "b1", "type": "superlite:implementer", "model": "sonnet", "desc": "T9 big"},
            {"ev": "stop", "t": 1_700_000_190_000, "agent_id": "a1", "type": "superlite:implementer-haiku", "transcript": str(ta), "last": "Status: DONE"},
            {"ev": "stop", "t": 1_700_000_080_000, "agent_id": "b1", "type": "superlite:implementer", "transcript": str(tb), "last": "Status: BLOCKED"}]
    (sl / "metrics.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    (sl / "pool.json").write_text(json.dumps({"agents": [{"id": "a1", "tasks": ["T1", "T2"], "tier": "haiku"}, {"id": "b1", "tasks": ["T9"]}]}), encoding="utf-8")
    return sl


def records(tmp_path, p):
    cp = run(METRICS, "--dir", tmp_path / ".superlite", "--plan", p, "--json", cwd=tmp_path)
    assert cp.returncode == 0, cp.both
    return {r["task"]: r for r in json.loads(cp.out)}


def test_metrics_joins_launch_stop_and_splits_a_reused_agent(tmp_path):
    p = mech_plan(tmp_path, 2)
    setup_run(tmp_path)
    t0 = time.time()
    r = records(tmp_path, p)
    assert time.time() - t0 < 5
    assert set(r) == {"T1", "T2", "T9"}
    assert r["T1"]["tool_calls"] == 2 and r["T2"]["tool_calls"] == 1 and r["T9"]["tool_calls"] == 3
    assert r["T1"]["model"] == "haiku" and r["T9"]["model"] == "sonnet"
    assert r["T9"]["status"] == "BLOCKED" and r["T9"]["first_time_pass"] is False
    assert r["T1"]["status"] == "DONE" and r["T1"]["first_time_pass"] is True
    # snapshots of one message id count once: T9 has 4 assistant messages, 100 input each
    assert r["T9"]["tokens"]["in"] == 400 and r["T9"]["tokens"]["cache_read"] == 4000
    assert r["T9"]["duration_s"] == 40.0  # first -> last message of its own transcript (hook times are only a fallback)
    assert r["T1"]["cost_usd"] > 0 and r["T9"]["cost_usd"] > r["T1"]["cost_usd"] / 5  # priced per model from config/models.json
    for k in ("task", "tier_planned" if False else "agent", "dispatches", "start_s", "end_s", "tokens", "cost_usd", "rework_s", "done_s"):
        assert k in r["T1"]


def test_metrics_cost_uses_model_prices(tmp_path):
    p = mech_plan(tmp_path, 1)
    sl = setup_run(tmp_path)
    r = records(tmp_path, p)
    # T9: sonnet, 4 msgs x (100 in, 200 out, 1000 read, 500 write 5m) at 2/10/0.1/2.5 USD per M
    assert abs(r["T9"]["cost_usd"] - (400 * 2 + 800 * 10 + 4000 * 0.1 + 2000 * 2.5) / 1e6) < 1e-6


def test_history_write_stats_and_read(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("SUPERLITE_HOME", str(home))
    if True:
        p = mech_plan(tmp_path, 2)
        (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        setup_run(tmp_path)
        cp = run(HISTORY, "write", "--plan", p, "--dir", tmp_path / ".superlite", "--run", "r1", cwd=tmp_path)
        assert cp.returncode == 0, cp.both
        rows = [json.loads(x) for x in (home / "history.jsonl").read_text(encoding="utf-8").splitlines()]
        by = {r["task"]: r for r in rows}
        assert by["T1"]["language"] == "python" and by["T1"]["run"] == "r1" and by["T1"]["kind"] == "mechanical"
        assert by["T1"]["tier"] == "haiku" and by["T9"]["tier"] == "sonnet"
        assert by["T9"]["critical_path"] is True and by["T1"]["critical_path"] is False and by["T1"]["slack_min"] > 0
        assert by["T9"]["first_time_pass"] is False
        for forbidden in ("prompt", "code", "text"):
            assert forbidden not in by["T1"]
        stats = json.loads((home / "stats.json").read_text(encoding="utf-8"))
        b = stats["bands"]["mechanical/le60/fast"]
        assert b["n"] == 2 and b["ftp_rate"] == 1.0 and b["mean_s"] is not None
        assert stats["tiers"]["sonnet"]["ftp_rate"] == 0.0
        assert (tmp_path / ".superlite" / "history.jsonl").is_file() and (tmp_path / ".superlite" / "stats.json").is_file()
        out = run(HISTORY, "read", "--n", "2", cwd=tmp_path).out.splitlines()
        assert len(out) == 2
        # deterministic: same rows, same stats
        first = {k: v for k, v in stats.items() if k != "updated"}
        run(HISTORY, "derive", cwd=tmp_path)
        again = json.loads((home / "stats.json").read_text(encoding="utf-8"))
        assert {k: v for k, v in again.items() if k != "updated"} == first


def test_history_import_converts_bench_score_files_once(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("SUPERLITE_HOME", str(home))
    if True:
        f = tmp_path / "combo-superlite-w9.json"
        f.write_text(json.dumps({"tasks": [
            {"task": "T1", "kind": "mechanical", "loc": 50, "components": 1, "tests": "fast", "model": "haiku", "tier_planned": "haiku",
             "duration_s": 60.0, "tokens": {"in": 1, "out": 2, "cache_read": 3, "cache_write": 4}, "cost_usd": 0.02, "tool_calls": 5,
             "first_time_pass": True, "rework_s": 0, "rework_cost": 0, "on_critical_path": False, "slack_s": 120.0},
            {"task": "T2", "kind": "design", "loc": 300, "components": 3, "tests": "slow", "model": "sonnet", "duration_s": 100.0,
             "cost_usd": 0.2, "tool_calls": 12, "first_time_pass": False}]}), encoding="utf-8")
        assert "imported 2 rows" in run(HISTORY, "import", f, cwd=tmp_path).out
        assert "imported 0 rows" in run(HISTORY, "import", f, cwd=tmp_path).out  # idempotent
        stats = json.loads((home / "stats.json").read_text(encoding="utf-8"))
        assert stats["bands"]["mechanical/le60/fast"]["n"] == 1 and stats["bands"]["design/le300/slow"]["ftp_rate"] == 0.0
        row = json.loads((home / "history.jsonl").read_text(encoding="utf-8").splitlines()[0])
        assert row["source"] == "import" and row["slack_min"] == 2.0 and row["tier"] == "haiku"


def test_budget_line_warn_and_over_cap(tmp_path):
    header = "**Target-min:** 1\n**Budget-usd:** {cap}\n"
    p = mech_plan(tmp_path, 2)
    text = p.read_text(encoding="utf-8").replace("**Goal:** x.", "**Goal:** x.\n" + header.format(cap="0.001"))
    p.write_text(text, encoding="utf-8")
    setup_run(tmp_path)
    cp = run(BUDGET, "--plan", p, "--dir", tmp_path / ".superlite", cwd=tmp_path)
    assert cp.returncode == 0, cp.both
    assert cp.out.startswith("budget: coordinator") and "of $0.001 cap" in cp.out and "OVER CAP" in cp.out
    assert "no optional work" in cp.out or "start no optional work" in cp.out
    j = json.loads(run(BUDGET, "--plan", p, "--dir", tmp_path / ".superlite", "--json", cwd=tmp_path).out)
    assert j["state"] == "over" and j["cap_usd"] == 0.001 and j["target_min"] == 1.0
    text = text.replace("**Budget-usd:** 0.001", "**Budget-usd:** 100")
    p.write_text(text, encoding="utf-8")
    ok = run(BUDGET, "--plan", p, "--dir", tmp_path / ".superlite", cwd=tmp_path).out
    assert "OVER CAP" not in ok and "WARN" not in ok


def test_budget_changed_prints_once(tmp_path):
    p = mech_plan(tmp_path, 2)
    setup_run(tmp_path)
    first = run(BUDGET, "--plan", p, "--dir", tmp_path / ".superlite", "--changed", cwd=tmp_path).out
    second = run(BUDGET, "--plan", p, "--dir", tmp_path / ".superlite", "--changed", cwd=tmp_path).out
    assert first.startswith("budget:") and second == ""


def test_budget_names_the_late_critical_path(tmp_path):
    p = mixed(tmp_path)
    text = p.read_text(encoding="utf-8").replace("**Goal:** x.", "**Goal:** x.\n**Target-min:** 0.5")
    p.write_text(text, encoding="utf-8")
    setup_run(tmp_path)
    out = run(BUDGET, "--plan", p, "--dir", tmp_path / ".superlite", cwd=tmp_path).out
    assert "LATE: critical path" in out and "T4" in out.split("LATE:")[1]  # T1 is done; T4 is the longest remaining


def _hist(home, rows):
    home.mkdir(parents=True, exist_ok=True)
    (home / "history.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _row(i, tier="haiku", ftp=True, loc=60, dur=90.0):
    return {"v": 1, "run": f"r{i}", "task": f"T{i}", "kind": "mechanical", "loc": loc, "components": 1, "tests": "fast", "tier": tier,
            "model": tier, "duration_s": dur, "cost_usd": 0.02, "first_time_pass": ftp, "band": "mechanical/le60/fast", "source": "run"}


def test_finish_records_run_and_reports_predicted_vs_actual(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("SUPERLITE_HOME", str(home))
    p = mech_plan(tmp_path, 2)
    sl = setup_run(tmp_path)
    (sl / "progress.md").write_text("# Progress ledger\n", encoding="utf-8")
    cp = run(SCRIPTS / "finish.py", "--ledger", sl / "progress.md", "--log", sl / "i.jsonl", "--plan", p, cwd=tmp_path)
    assert cp.returncode == 0, cp.both
    assert "history: 3 rows appended" in cp.out and cp.out.count("predicted vs actual") == 0
    assert "build report (projected -> actual)" in cp.out and "takeaway:" in cp.out and cp.out.count("build report (projected") == 1
    assert "Full report:" in cp.out and (sl / "report.md").is_file() and "biggest miss" in cp.out
    assert (home / "history.jsonl").is_file() and (home / "stats.json").is_file()


def test_predict_refits_from_history_only_with_enough_rows(tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, str(SCRIPTS))
    import importlib
    import predict
    importlib.reload(predict)
    home = tmp_path / "home"
    monkeypatch.setenv("SUPERLITE_HOME", str(home))
    shipped = predict.load_coef()
    assert "source" not in (shipped.get("fit") or {})
    _hist(home, [_row(i, dur=60.0 + i) for i in range(10)])  # fewer than 30: shipped coefficients
    assert predict.load_coef() == shipped
    _hist(home, [_row(i, dur=60.0 + i, loc=40 + 10 * (i % 8)) for i in range(40)])
    fitted = predict.load_coef()
    assert fitted["fit"]["source"] == "history" and fitted["fit"]["n"] == 40 and fitted["time"] != shipped["time"]
    assert predict.load_coef() == fitted  # deterministic
    assert predict.load_coef(str(SCRIPTS.parent / "config" / "predictor.json")) == shipped  # an explicit path is never replaced


def test_routing_stats_gate_keeps_a_weak_band_on_sonnet(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("SUPERLITE_HOME", str(home))
    p = mixed(tmp_path)
    assert json.loads(run(SCRIPTS / "routing.py", "--plan", p, "--json", cwd=tmp_path).out)["T3"] == "haiku"
    _hist(home, [_row(i, ftp=(i < 3)) for i in range(10)])  # 3/10 first-time passes in the haiku band
    run(HISTORY, "derive", cwd=tmp_path)
    cp = run(SCRIPTS / "plan_tools.py", "lint", "--stable", "--plan", p, cwd=tmp_path)
    assert "stats: n=10 (history gate kept T3 off haiku)" in cp.out and "predicted: " in cp.out
    assert "T3=sonnet" in cp.out


def test_lint_prints_stats_and_prediction_line(tmp_path):
    cp = run(SCRIPTS / "plan_tools.py", "lint", "--stable", "--plan", mixed(tmp_path), cwd=tmp_path)
    assert "stats: n=0; predicted: " in cp.out and "(shipped coefficients)" in cp.out
    assert cp.out.rstrip().splitlines()[-1].startswith("DISPATCH NOW")


FIX = SCRIPTS.parent.parent.parent / "tests" / "scripts" / "fixtures" / "capture_real"


def test_golden_real_capture_log(tmp_path):
    """A real hook log: the stop line precedes the launch line; timing comes from the agent transcript."""
    sl = tmp_path / ".superlite"
    sl.mkdir()
    shutil_copy = __import__("shutil").copy
    shutil_copy(FIX / "agent.jsonl", tmp_path / "agent.jsonl")
    text = (FIX / "metrics.jsonl").read_text(encoding="utf-8").replace("@TRANSCRIPT@", (tmp_path / "agent.jsonl").as_posix())
    (sl / "metrics.jsonl").write_text(text, encoding="utf-8")
    lines = [json.loads(x) for x in text.splitlines()]
    assert lines[0]["ev"] == "stop" and lines[1]["ev"] == "launch" and lines[0]["t"] < lines[1]["t"]
    cp = run(METRICS, "--dir", sl, "--json", cwd=tmp_path)
    assert cp.returncode == 0, cp.both
    (r,) = json.loads(cp.out)
    assert r["task"] == "T1" and r["model"] == "sonnet"
    assert (r["tokens"]["in"], r["tokens"]["out"], r["tokens"]["cache_read"], r["tokens"]["cache_write"]) == (4, 53, 19809, 21784)
    assert r["tool_calls"] == 1
    assert r["start_s"] == 0.0 and r["duration_s"] > 0 and r["end_s"] > r["start_s"]


def test_stop_before_launch_never_gives_a_negative_duration(tmp_path):
    sl = tmp_path / ".superlite"
    sl.mkdir()
    rows = [{"ev": "stop", "t": 1_700_000_000_000, "agent_id": "a", "type": "superlite:implementer", "transcript": "", "last": "Status: DONE"},
            {"ev": "launch", "t": 1_700_000_000_400, "agent_id": "a", "type": "superlite:implementer", "model": "sonnet", "desc": "T1 x"}]
    (sl / "metrics.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    (r,) = json.loads(run(METRICS, "--dir", sl, "--json", cwd=tmp_path).out)
    assert r["duration_s"] >= 0 and r["start_s"] == 0.0 and r["end_s"] >= r["start_s"]


def test_metrics_warns_when_dir_has_no_metrics_file(tmp_path):
    cp = run(METRICS, "--dir", tmp_path, "--json", cwd=tmp_path)
    assert cp.out.strip() == "[]" and "no metrics.jsonl" in cp.err and ".superlite folder itself" in cp.err


def _main_transcript(path, turns, ctx=1000, model="claude-opus-5-5", text="x"):
    lines = []
    for i in range(turns):
        lines.append({"type": "assistant", "timestamp": f"2026-10-18T10:00:{i % 60:02d}Z",
                      "message": {"id": f"c{i}", "model": model, "content": [{"type": "text", "text": text}],
                                  "usage": {"input_tokens": 10, "output_tokens": 100, "cache_read_input_tokens": ctx, "cache_creation_input_tokens": 50}}})
        lines.append(dict(lines[-1]))  # the same message id again: counted once
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")


def _session_run(tmp_path, turns=4, ctx=1000):
    """<proj>/<session>.jsonl (coordinator) and <proj>/<session>/subagents/agent-a.jsonl, hooks pointing at the agent."""
    proj = tmp_path / "proj"
    (proj / "sess" / "subagents").mkdir(parents=True)
    _main_transcript(proj / "sess.jsonl", turns, ctx)
    agent = proj / "sess" / "subagents" / "agent-a.jsonl"
    transcript(agent, [("Implement T1 slug", [("Bash", "")], "Status: DONE")])
    sl = tmp_path / ".superlite"
    sl.mkdir()
    rows = [{"ev": "launch", "t": 1_700_000_000_000, "agent_id": "a", "type": "superlite:implementer", "model": "sonnet", "desc": "T1 slug"},
            {"ev": "stop", "t": 1_700_000_050_000, "agent_id": "a", "type": "superlite:implementer", "transcript": str(agent), "last": "Status: DONE"}]
    (sl / "metrics.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return sl


def test_coordinator_record_from_the_main_transcript(tmp_path):
    sl = _session_run(tmp_path, turns=4, ctx=1000)
    cp = run(METRICS, "--dir", sl, "--json", "--all", cwd=tmp_path)
    co = json.loads(cp.out)["coordinator"]
    assert co["turns"] == 4 and co["context_peak"] == 1060 and co["context_median"] == 1060
    assert co["tokens"]["in"] == 40 and co["tokens"]["cache_read"] == 4000 and set(co["cost_by_model"]) == {"opus"}
    assert abs(co["cost_usd"] - (40 * 4 + 400 * 20 + 4000 * 0.2 + 200 * 5) / 1e6) < 1e-6  # opus prices
    assert "coordinator 4 turns" in run(METRICS, "--dir", sl, cwd=tmp_path).out


def test_output_tokens_are_topped_up_from_text_length(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    sl = tmp_path / ".superlite"
    sl.mkdir()
    _main_transcript(proj / "s.jsonl", 1, text="y" * 3500)  # 1000 estimated tokens against a 100-token snapshot
    (sl / "metrics.jsonl").write_text(json.dumps({"ev": "launch", "t": 1, "agent_id": "z", "type": "x", "model": "sonnet", "desc": "T1",
                                                  "session_transcript": str(proj / "s.jsonl")}) + "\n", encoding="utf-8")
    co = json.loads(run(METRICS, "--dir", sl, "--json", "--all", cwd=tmp_path).out)["coordinator"]
    assert co["tokens"]["out"] >= 1000


def test_reconcile_against_the_stream_log(tmp_path):
    sl = _session_run(tmp_path)
    mine = json.loads(run(METRICS, "--dir", sl, "--json", "--all", cwd=tmp_path).out)
    total = mine["coordinator"]["cost_usd"] + sum(t["cost_usd"] for t in mine["tasks"])
    log = tmp_path / "stream.jsonl"
    log.write_text(json.dumps({"type": "result", "total_cost_usd": round(total * 1.03, 6)}) + "\n", encoding="utf-8")
    rc = json.loads(run(METRICS, "--dir", sl, "--reconcile", log, cwd=tmp_path).out)
    assert rc["higher"] == "stream" and abs(rc["diff_pct"]) < 4 and abs(rc["metrics_usd"] - total) < 1e-5


def test_budget_shows_the_cost_split_and_the_coordinator_hint(tmp_path):
    sl = _session_run(tmp_path, turns=40, ctx=80_000)
    p = mech_plan(tmp_path, 1)
    text = p.read_text(encoding="utf-8").replace("**Goal:** x.", "**Goal:** x.\n**Budget-usd:** 50")
    p.write_text(text, encoding="utf-8")
    out = run(BUDGET, "--plan", p, "--dir", sl, cwd=tmp_path).out
    assert "coordinator $" in out and "| agents $" in out and "| total $" in out and "of $50 cap" in out
    assert "HINT: the coordinator is" in out and "40 turns, median context 80k" in out
    small = _session_run(tmp_path / "s2" if (tmp_path / "s2").mkdir() is None else tmp_path, turns=3, ctx=1000)
    assert "HINT" not in run(BUDGET, "--plan", p, "--dir", small, cwd=tmp_path).out


def test_report_of_reads_the_handback_when_the_stop_text_is_empty(tmp_path):
    import sys
    sys.path.insert(0, str(SCRIPTS))
    import metrics
    tr = tmp_path / "agent.jsonl"
    tr.write_text("\n".join(json.dumps(x) for x in [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "working"}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "SubagentHandback",
                                                       "input": {"message": "Status: DONE. 4 tests pass"}}]}},
    ]) + "\n", encoding="utf-8")
    assert metrics.report_of({"last": "", "transcript": str(tr)}) == "Status: DONE. 4 tests pass"
    assert metrics.report_of({"last": "Status: BLOCKED", "transcript": str(tr)}) == "Status: BLOCKED"  # the stop text wins
    assert metrics.report_of({"last": "", "transcript": str(tmp_path / "missing.jsonl")}) == ""  # no transcript: no crash


def test_cursor_role_transcript_estimates_output_tokens(tmp_path):
    import sys
    sys.path.insert(0, str(SCRIPTS))
    import metrics
    tr = tmp_path / "cursor.jsonl"
    tr.write_text(json.dumps({
        "role": "assistant",
        "message": {"content": [{"type": "text", "text": "x" * 35}]},
    }) + "\n", encoding="utf-8")
    segs = metrics.transcript_segments(tr, [])
    assert segs and segs[0]["tok"]["output"] >= 10
