"""run_widget.py, finish --plan and lint --stable: the run cards before and after a build."""
from __future__ import annotations

import json
import re
import sys
from html.parser import HTMLParser

from test_metrics_history import setup_run
from test_plan_tools import SCRIPTS, run
from test_routing import mech_plan

sys.path.insert(0, str(SCRIPTS))
import report  # noqa: E402
import run_widget  # noqa: E402

WIDGET = SCRIPTS / "run_widget.py"
FINISH = SCRIPTS / "finish.py"
LEDGER = ("# Progress ledger\n"
          "- Ruling: kept the <b>old</b> parser & its tests\n"
          "- Ruling: [scope] dropped the CSV export\n"
          "- tests full passed=142 failed=0 skipped=1 seconds=12.5\n")


class Tags(HTMLParser):
    """Every start tag and its attributes, to check structure rather than text."""
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def tags(text):
    p = Tags()
    p.feed(text)
    return p.tags


def finished_run(tmp_path, monkeypatch, checkpoints=3):
    """A run folder as finish leaves it just before the card: metrics, tests, ledger, checkpoints, report.json."""
    monkeypatch.setenv("SUPERLITE_HOME", str(tmp_path / "home"))
    p = mech_plan(tmp_path, 2)
    sl = setup_run(tmp_path)
    (sl / "test-summary.json").write_text(json.dumps({"passed": 142, "failed": 0, "skipped": 1}), encoding="utf-8")
    (sl / "progress.md").write_text(LEDGER, encoding="utf-8")
    entries = [{"task": f"T{i}", "files": [f"src/f{i}.py"]} for i in range(checkpoints)]
    (sl / "checkpoints.json").write_text(json.dumps({"entries": entries}), encoding="utf-8")
    return p, sl


def test_card_shows_time_cost_tests_models_and_rulings(tmp_path, monkeypatch):
    p, sl = finished_run(tmp_path, monkeypatch)
    rep, _ = report.write_report(p, sl)
    cp = run(WIDGET, "--dir", sl, cwd=tmp_path)
    assert cp.returncode == 0, cp.both
    card = cp.out
    assert f"{rep['headline']['wall_min']['actual']:.1f} min" in card
    assert f"${rep['cost_split']['total_usd']:.2f}" in card
    assert "tests 142 passed, 0 failed" in card and "rulings 2" in card and "scope changes 1" in card
    assert "3 checkpoints taken; work uncommitted in the folder" in card
    bars = [a["data-bar"] for t, a in tags(card) if "data-bar" in a]
    assert sorted(bars) == sorted(rep["model_mix"])  # one bar per model, no coordinator record in this run
    names = {t for t, _ in tags(card)}
    assert not names & {"script", "html", "body", "head", "link", "img", "iframe", "style"}
    assert ("h2", {"class": "sr-only"}) in [(t, {"class": a.get("class")}) for t, a in tags(card)]
    assert "http" not in card and "#" not in card.replace("&#x27;", "")  # host CSS variables only, nothing fetched


def test_ruling_markup_is_escaped(tmp_path, monkeypatch):
    p, sl = finished_run(tmp_path, monkeypatch)
    report.write_report(p, sl)
    card = run(WIDGET, "--dir", sl, cwd=tmp_path).out
    assert "kept the &lt;b&gt;old&lt;/b&gt; parser &amp; its tests" in card
    assert "b" not in {t for t, _ in tags(card)}


def test_no_report_prints_nothing(tmp_path):
    sl = tmp_path / "run"
    sl.mkdir()
    cp = run(WIDGET, "--dir", sl, cwd=tmp_path)
    assert cp.returncode == 0 and cp.out == "" and cp.err == ""


def test_missing_values_read_na_and_the_coordinator_gets_a_bar():
    rep = {"headline": {"wall_min": {"projected": None, "actual": 14.24}, "agents": {"projected": 5, "actual": 6}},
           "cost_split": {"total_usd": 2.314, "coordinator_usd": 0.47},
           "model_mix": {"sonnet": {"tasks": 5, "cost_usd": 1.2}, "haiku": {"tasks": 1, "cost_usd": 0.22}},
           "quality": {"tests": None, "first_time_n": 0},
           "feature": {"topic": "run-widget", "prototype": {"wall_min": {"actual": 3.1}}, "integration": {"wall_min": {}}}}
    card = run_widget.render(rep, [], 0, "feature: run-widget", "human orchestrator")
    assert "14.2 min" in card and "proj n/a" in card and "proj 5 · +1" in card and "$2.31" in card
    assert "tests n/a passed, n/a failed" in card and "first-time pass n/a" in card and "1 task<" in card
    assert "prototype 3.1 min" in card and "integration n/a min" in card and "no checkpoint state recorded" in card
    assert [a["data-bar"] for _, a in tags(card) if "data-bar" in a] == ["sonnet", "haiku", "coordinator"]
    assert "superlite run · feature: run-widget · human orchestrator" in card


def test_many_rulings_are_capped(tmp_path):
    card = run_widget.render({}, [f"r{i}" for i in range(8)], 1, "", "")
    assert card.count("<li>") == run_widget.MAX_RULINGS + 1 and "+3 more in the finish summary" in card
    assert "1 checkpoint taken; work uncommitted in the folder" in card


def archived(tmp_path):  # finish moves the run's files into <hub>/.superlite/runs/<id>; the newest one is this run's
    return max((tmp_path / ".superlite" / "runs").iterdir(), key=lambda p: p.name)


def test_finish_with_plan_writes_the_card_and_ends_with_the_widget_line(tmp_path, monkeypatch):
    p, sl = finished_run(tmp_path, monkeypatch)
    cp = run(FINISH, "--ledger", sl / "progress.md", "--log", sl / "i.jsonl", "--plan", p, cwd=tmp_path)
    assert cp.returncode == 0, cp.both
    last = cp.out.rstrip("\n").splitlines()[-1]
    assert last.startswith("widget: ") and "show_widget" in last
    shown = last.split()[1]
    assert (tmp_path / shown).resolve() == (archived(tmp_path) / run_widget.AFTER).resolve()
    card = (archived(tmp_path) / run_widget.AFTER).read_text(encoding="utf-8")
    assert "3 checkpoints taken; work uncommitted in the folder" in card  # counted before finish cleans the checkpoint state
    assert not (sl / "checkpoints.json").exists()


def test_finish_card_reads_the_ledger_it_was_given(tmp_path, monkeypatch):
    p, sl = finished_run(tmp_path, monkeypatch)
    (sl / "progress.md").write_text("# Progress ledger\n- Ruling: stale ruling\n", encoding="utf-8")
    (sl / "custom.md").write_text(LEDGER, encoding="utf-8")
    cp = run(FINISH, "--ledger", sl / "custom.md", "--log", sl / "i.jsonl", "--plan", p, cwd=tmp_path)
    assert cp.returncode == 0, cp.both
    card = (archived(tmp_path) / run_widget.AFTER).read_text(encoding="utf-8")
    assert "dropped the CSV export" in card and "stale ruling" not in card


def test_finish_widget_line_keeps_a_nested_ledger_path(tmp_path, monkeypatch):
    p, sl = finished_run(tmp_path, monkeypatch)
    nested = tmp_path / "logs" / "run"
    nested.mkdir(parents=True)
    for name in ("progress.md", "report.json", "metrics.jsonl", "checkpoints.json"):
        if (sl / name).exists():
            (nested / name).write_text((sl / name).read_text(encoding="utf-8"), encoding="utf-8")
    cp = run(FINISH, "--ledger", "logs/run/progress.md", "--log", "logs/run/i.jsonl", "--plan", p, cwd=tmp_path)
    assert cp.returncode == 0, cp.both
    assert cp.out.rstrip("\n").splitlines()[-1].split()[1] == "logs/run/" + run_widget.AFTER
    assert (nested / run_widget.AFTER).is_file()


def test_widget_line_outside_root_is_absolute(tmp_path):
    out = tmp_path / "a" / run_widget.AFTER
    assert run_widget.widget_line(out, tmp_path / "b").split()[1] == str(out.resolve())


def test_float_actual_against_whole_projected_agents():
    card = run_widget.render({"headline": {"agents": {"projected": 3, "actual": 2.0}}}, [], 0)
    assert "proj 3 · -1" in card


def test_finish_without_plan_has_no_widget(tmp_path, monkeypatch):
    p, sl = finished_run(tmp_path, monkeypatch)
    report.write_report(p, sl)  # an old report.json from an earlier run
    cp = run(FINISH, "--ledger", sl / "progress.md", "--log", sl / "i.jsonl", cwd=tmp_path)
    assert cp.returncode == 0, cp.both
    assert "widget:" not in cp.out and not (sl / run_widget.AFTER).exists()


def test_finish_with_plan_but_no_metrics_ignores_an_old_report(tmp_path, monkeypatch):
    p, sl = finished_run(tmp_path, monkeypatch)
    report.write_report(p, sl)
    (sl / "metrics.jsonl").unlink()  # nothing to report this time, so report.json is not rewritten
    cp = run(FINISH, "--ledger", sl / "progress.md", "--log", sl / "i.jsonl", "--plan", p, cwd=tmp_path)
    assert cp.returncode == 0, cp.both
    assert "widget:" not in cp.out and not (sl / run_widget.AFTER).exists()


# ---- before the run


def linted(tmp_path, cap="3.00"):
    """A mech_plan fixture after its first stable lint: (plan, run folder, projection, lint output)."""
    p = mech_plan(tmp_path, 3)
    if cap:
        text = p.read_text(encoding="utf-8")
        p.write_text(text.replace("**Tech stack:** Python\n", f"**Tech stack:** Python\n**Budget-usd:** {cap}\n", 1), encoding="utf-8")
    cp = run(SCRIPTS / "plan_tools.py", "lint", "--stable", "--plan", p, cwd=tmp_path)
    assert cp.returncode in (0, 1), cp.both
    sl = tmp_path / ".superlite"
    return p, sl, json.loads((sl / "projection.json").read_text(encoding="utf-8")), cp.out


def test_before_card_from_a_real_forecast(tmp_path):
    p, sl, proj, _ = linted(tmp_path)
    cp = run(WIDGET, "--before", "--plan", p, "--dir", sl, cwd=tmp_path)
    assert cp.returncode == 0, cp.both
    card = cp.out
    assert f"{proj['wall_min']:.1f} min" in card and f"${proj['cost_usd']:.2f}" in card and "$3.00" in card
    assert f"{round(100 * proj['cost_usd'] / 3.0)}% of $3.00 cap" in card
    assert "critical path " + " &gt; ".join(proj["critical_path"]) in card
    amounts = [float(m) for m in re.findall(r'text-align: right; color: var\(--text-primary\);">\$([\d.]+)<', card)]
    assert abs(sum(amounts) - proj["cost_usd"]) < 0.02  # the bars add up to the headline, each rounded to a cent
    bars = [a["data-bar"] for _, a in tags(card) if "data-bar" in a]
    assert set(bars) - {"other"} == {t["model"] for t in proj["tasks"].values()}
    assert not {t for t, _ in tags(card)} & {"script", "html", "body", "head", "style", "img", "link"}


def test_before_card_without_a_cap_or_a_forecast(tmp_path):
    p, sl, _, _ = linted(tmp_path, cap=None)
    assert "no Budget-usd cap" in run(WIDGET, "--before", "--plan", p, "--dir", sl, cwd=tmp_path).out
    (sl / "projection.json").unlink()
    cp = run(WIDGET, "--before", "--plan", p, "--dir", sl, cwd=tmp_path)
    assert cp.returncode == 0 and cp.out == ""


def test_before_card_other_bar_and_escaping():
    proj = {"wall_min": 12.0, "cost_usd": 2.10, "agents": 5, "peak_width": 4, "critical_path": ["T1", "<b>T3</b>"],
            "tasks": {"T1": {"model": "sonnet", "cost_usd": 1.5}, "T3": {"model": "haiku", "cost_usd": 0.28}}}
    card = run_widget.render_before(proj, {"stable": ["T1"], "fix": ["T3"]}, 3.0, "<b>plan</b>", "human orchestrator")
    assert [a["data-bar"] for _, a in tags(card) if "data-bar" in a] == ["sonnet", "haiku", "other"]
    assert "$0.32" in card and "70% of $3.00 cap" in card and "stable 1" in card and "fix first 1" in card
    assert "&lt;b&gt;T3&lt;/b&gt;" in card and "&lt;b&gt;plan&lt;/b&gt;" in card and "b" not in {t for t, _ in tags(card)}


def test_first_stable_lint_writes_the_before_card_and_still_ends_with_dispatch(tmp_path):
    p, sl, _, out = linted(tmp_path)
    lines = out.rstrip().splitlines()
    assert lines[-1].startswith("DISPATCH NOW:")
    assert lines[-2].startswith("widget: ") and "show_widget" in lines[-2]
    assert (tmp_path / lines[-2].split()[1]).resolve() == (sl / run_widget.BEFORE).resolve()
    assert "superlite run forecast" in (sl / run_widget.BEFORE).read_text(encoding="utf-8")
    again = run(SCRIPTS / "plan_tools.py", "lint", "--stable", "--plan", p, cwd=tmp_path)
    assert "widget:" not in again.out and again.out.rstrip().splitlines()[-1].startswith("DISPATCH NOW:")


def test_lint_from_a_subdirectory_names_the_card_from_there(tmp_path):
    p = mech_plan(tmp_path, 3)
    sub = tmp_path / "sub"
    sub.mkdir()
    cp = run(SCRIPTS / "plan_tools.py", "lint", "--stable", "--plan", p, cwd=sub)
    assert cp.returncode in (0, 1), cp.both
    line = next(x for x in cp.out.splitlines() if x.startswith("widget: "))
    assert (sub / line.split()[1]).resolve() == (tmp_path / ".superlite" / run_widget.BEFORE).resolve()


def test_non_finite_numbers_read_na():
    inf = json.loads("1e309")
    card = run_widget.render({"headline": {"agents": {"projected": inf, "actual": 2}, "wall_min": {"actual": inf}}}, [], 0)
    assert "inf" not in card and "proj n/a" in card
    proj = {"wall_min": inf, "cost_usd": inf, "agents": inf, "critical_path": [], "tasks": {"T1": {"model": "sonnet", "cost_usd": inf}}}
    assert "inf" not in run_widget.render_before(proj, {}, 3.0)


def test_lint_then_finish_gives_both_cards(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERLITE_HOME", str(tmp_path / "home"))
    p, sl, proj, _ = linted(tmp_path)
    setup_run(tmp_path)
    (sl / "progress.md").write_text(LEDGER, encoding="utf-8")
    cp = run(FINISH, "--ledger", sl / "progress.md", "--log", sl / "i.jsonl", "--plan", p, cwd=tmp_path)
    assert cp.returncode == 0, cp.both
    assert cp.out.rstrip().splitlines()[-1].startswith("widget: .superlite/runs/")
    before, after = ((archived(tmp_path) / f).read_text(encoding="utf-8") for f in (run_widget.BEFORE, run_widget.AFTER))
    assert f"${proj['cost_usd']:.2f}" in before and f"proj {proj['cost_usd']:.2f}" in after  # the after card compares to the same forecast


def test_malformed_json_reads_na_instead_of_failing(tmp_path):
    rep = {"headline": {"agents": {"actual": 2.0, "projected": "x"}, "wall_min": []}, "quality": "x", "tasks": {"T1": 1},
           "cost_split": None, "model_mix": {"sonnet": {"cost_usd": "x"}, "haiku": 3}, "feature": [1]}
    card = run_widget.render(rep, ["r"], 0)
    assert "n/a" in card and ">2<" in card and "data-bar" not in card
    sl = tmp_path / "run"
    sl.mkdir()
    (sl / "projection.json").write_text(json.dumps({"tasks": ["T1"], "critical_path": "T1", "cost_usd": "x"}), encoding="utf-8")
    (sl / "stable.json").write_text(json.dumps({"stable": 3, "fix": None}), encoding="utf-8")
    (sl / "checkpoints.json").write_text(json.dumps({"entries": 7}), encoding="utf-8")
    assert "critical path n/a" in run_widget.build_before(sl, tmp_path / "missing.md")
    (sl / "report.json").write_text(json.dumps(rep), encoding="utf-8")
    assert "no checkpoint state recorded" in run_widget.build(sl)
