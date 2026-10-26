"""The harness metrics against what the real superlite scripts write.

Nothing here hand-writes a script's output: build_prime.py, pt_review.py, plan_tools.py and incident.py
are run as subprocesses (on a copy of tests/scripts/fixtures/review_basic) and their stdout, files and
.superlite/incidents.jsonl feed harness.dispatch_stats, with and without the run dir.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bench"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness  # noqa: E402
from test_metrics import Log  # noqa: E402

SCRIPTS = ROOT / "skills" / "swarm" / "scripts"
REVIEW_BASIC = ROOT / "tests" / "scripts" / "fixtures" / "review_basic"
CONSUMER_TEST = ("import os\n\n\ndef test_runs_against_real_producer():\n"
                 "    assert os.environ.get('SUPERLITE_REAL') == '1'\n")
SL = "bash skills/swarm/scripts/sl"       # how the skill invokes tools: no .py


def script(name, *args, cwd):
    cp = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPTS / f"{name}.py"), *map(str, args)], cwd=cwd,
                        capture_output=True, text=True, encoding="utf-8", timeout=180)
    return cp.returncode, cp.stdout


@pytest.fixture()
def proj(tmp_path):
    dest = tmp_path / "proj"
    shutil.copytree(REVIEW_BASIC, dest)
    (dest / "tests").mkdir()
    (dest / "tests" / "test_ui.py").write_text(CONSUMER_TEST, encoding="utf-8")
    (dest / ".superlite").mkdir()
    (dest / ".superlite" / "incidents.jsonl").unlink(missing_ok=True)
    (dest / "incidents.jsonl").unlink(missing_ok=True)
    return dest


# ---- 1. prices -------------------------------------------------------------------------------

def test_prices_come_from_models_json(tmp_path, monkeypatch):
    cfg = json.loads(harness.MODELS_JSON.read_text(encoding="utf-8"))
    s = cfg["models"]["sonnet"]
    assert harness.PRICES["sonnet"] == tuple(x / cfg["per_tokens"] for x in (
        s["input"], s["output"], s["cache_write_5m"], s["cache_write_1h"], s["cache_read"]))
    # An edited file changes the review cost; cache write prices fall back to input x multiplier.
    s.update(input=10.0, output=100.0, cache_read=1.0)
    del s["cache_write_5m"], s["cache_write_1h"]
    edited = tmp_path / "models.json"
    edited.write_text(json.dumps(cfg), encoding="utf-8")
    prices = harness.load_prices(edited)
    assert prices["sonnet"] == pytest.approx((10e-6, 100e-6, 12.5e-6, 20e-6, 1e-6))
    log = Log()
    log.agent("c1", "a1", "Review T1", "Review the diff.", 10, 40, subagent_type="superlite:package-reviewer")
    base = harness.dispatch_stats(log.ev)["review_cost_usd"]
    monkeypatch.setattr(harness, "PRICES", prices)
    edited_cost = harness.dispatch_stats(log.ev)["review_cost_usd"]
    # two usage records of 1 input, 5 output and 20000 cache-read tokens each
    assert edited_cost == pytest.approx(2 * (1 * 10 + 5 * 100 + 20000 * 1.0) / 1e6, abs=1e-3)
    assert edited_cost > base * 3


# ---- 2a. prime hash: build_prime.py ----------------------------------------------------------

def _prime(proj):
    spec = proj / "SPEC.md"
    spec.write_text("# Spec\n\n## 1. Engine\nText one.\n\n## 2. Server\nText two.\n", encoding="utf-8")
    rc, out = script("build_prime", "--plan", "plan.md", "--spec", "SPEC.md", "--sections", "1,2",
                     "--out", ".superlite/prime.md", cwd=proj)
    assert rc == 0, out
    return out, (proj / ".superlite" / "prime.md").read_bytes().decode("utf-8")


def _prime_run(proj, prompts):
    out, _ = _prime(proj)
    log = Log()
    log.bash(f"{SL} build_prime --plan plan.md --spec SPEC.md --sections 1,2", 1, result=out)
    for n, p in enumerate(prompts):
        log.agent(f"c{n}", f"a{n}", f"T{n + 1} implement", p, 5 + n, 9 + n, report="READY")
    return log


def test_prime_hash_agrees_on_real_build_prime(proj):
    _, prime = _prime(proj)
    log = _prime_run(proj, [prime + "\n\nYour task: T1", prime + "\n\nYour task: T2", prime])
    assert harness.dispatch_stats(log.ev)["prime_hash_mismatches"] == 0           # from build_prime's output
    assert harness.dispatch_stats(log.ev, proj)["prime_hash_mismatches"] == 0     # from .superlite/prime.md


def test_prime_hash_flags_a_retyped_prime(proj):
    _, prime = _prime(proj)
    retyped = prime.replace("Do not commit.", "Do not commit!")
    assert retyped != prime
    log = _prime_run(proj, [prime + "\n\nT1", retyped + "\n\nT2", prime + "\n\nT3"])
    assert harness.dispatch_stats(log.ev)["prime_hash_mismatches"] == 1
    assert harness.dispatch_stats(log.ev, proj)["prime_hash_mismatches"] == 1
    # an implementer dispatched without the prime also counts
    log = _prime_run(proj, [prime + "\n\nT1", "Implement T2 from scratch."])
    assert harness.dispatch_stats(log.ev, proj)["prime_hash_mismatches"] == 1


def test_prime_marker_is_the_real_preambles_first_line(proj):
    _, prime = _prime(proj)
    assert prime.startswith(harness.PRIME_MARKER)


# ---- 2b. seam checks and incidents: pt_review.py, incident.py --------------------------------

def test_seam_checks_from_real_pt_review_output_and_log(proj):
    plan = str(proj / "plan.md")
    cmd = f"{SL} pt_review contract --plan {plan} --seam T1,T2"
    rc_ok, out_ok = script("pt_review", "contract", "--plan", plan, "--root", proj, "--seam", "T1,T2", cwd=proj)
    assert rc_ok == 0 and "PASS: verified: seam T1" in out_ok, out_ok
    wb = proj / "src" / "minisheet" / "workbook.py"
    wb.write_text(wb.read_text(encoding="utf-8").replace("def set(self, ref: str, raw: str)", "def set(self, ref: str)"),
                  encoding="utf-8")
    rc_bad, out_bad = script("pt_review", "contract", "--plan", plan, "--root", proj, "--seam", "T1,T2", cwd=proj)
    assert rc_bad == 1 and out_bad.count("FAIL: seam T1") == 1, out_bad
    # Stream only: the commands and their real output.
    log = Log()
    log.bash(cmd, 1, result=out_ok)
    log.bash(cmd, 5, result=out_bad)
    s = harness.dispatch_stats(log.ev)
    assert (s["seam_checks"], s["seam_mismatches"]) == (2, 1)
    # Run dir only: the background launch left no command in the stream, the log has the record.
    quiet = Log()
    quiet.bash("echo nothing to see", 1)
    s = harness.dispatch_stats(quiet.ev, proj)
    assert (s["seam_checks"], s["seam_mismatches"]) == (2, 1)
    assert s["incidents"]["count"] >= 1 and "verified" not in s["incidents"]["by_kind"]
    assert s["incidents"]["by_kind"].get("exception", 0) >= 1


def test_no_seam_activity_stays_none(proj):
    s = harness.dispatch_stats(Log().ev, proj)
    assert s["seam_checks"] is None and s["seam_mismatches"] is None and s["prime_hash_mismatches"] is None


def test_incident_counts_from_real_incident_py_and_sl_command(proj):
    logf = proj / ".superlite" / "incidents.jsonl"
    for kind in ("workaround", "workaround", "flaky"):
        rc, _ = script("incident", "add", "--file", "src/a.py", "--line", 3, "--context", "x", "--kind", kind,
                       "--log", logf, cwd=proj)
        assert rc == 0
    s = harness.dispatch_stats(Log().ev, proj)
    assert s["incidents"] == {"count": 3, "by_kind": {"workaround": 2, "flaky": 1}}
    log = Log()                                  # no run dir: the `sl incident add` commands are counted
    log.bash(f"{SL} incident add --file a.py --line 1 --context c --kind workaround", 1)
    log.bash("python .superlite/bin/incident.py add --file a.py --line 1 --context c --kind flaky", 2)
    assert harness.dispatch_stats(log.ev)["incidents"] == {"count": 2, "by_kind": {"workaround": 1, "flaky": 1}}


# ---- 2c. predicted against actual: plan_tools.py schedule ------------------------------------

PLAN = """# Plan

```mermaid
%% taskgraph
flowchart LR
  T1 --> T2
```

### T1 engine · Complexity 3 · Est. context 40k
**Owns:** src/a.py
**Est. time:** 9 min
**Components:** 1
**LOC owned:** 120
**Tests:** slow
**Model:** sonnet

### T2 server · Complexity 3 · Est. context 40k
**Owns:** src/b.py
**Est. time:** 9 min
**Components:** 2
**LOC owned:** 300
**Tests:** fast
**Model:** sonnet
"""


@pytest.mark.parametrize("flags", [(), ("--json",)])
def test_predicted_vs_actual_on_real_schedule_output(proj, flags):
    (proj / "plan.md").write_text(PLAN, encoding="utf-8")
    rc, out = script("plan_tools", "schedule", "--plan", "plan.md", *flags, cwd=proj)
    assert rc == 0, out
    rc, js = script("plan_tools", "schedule", "--plan", "plan.md", "--json", cwd=proj)
    rep = json.loads(js)
    t1, t2 = rep["tasks"]["T1"]["min"], rep["tasks"]["T2"]["min"]
    assert t1 != 9 and t2 != 9                                       # the predictor won over the stated 9 min
    log = Log()
    log.write("docs/plans/2026-plan.md", PLAN, 1)
    log.bash(f"{SL} plan_tools schedule --plan docs/plans/2026-plan.md {' '.join(flags)}", 2, result=out)
    log.agent("c1", "a1", "T1 engine", "Build T1", 10, 10 + t1 * 60, subagent_type="superlite:implementer")
    log.agent("c2", "a2", "T2 server", "Build T2", 400, 400 + t2 * 60, subagent_type="superlite:implementer")
    s = harness.dispatch_stats(log.ev)
    tol = 6 if not flags else 0.5                                    # plain output prints minutes to 1 decimal
    assert s["predicted_total_s"] == pytest.approx(rep["total_min"] * 60, abs=tol)
    assert s["critical_path_predicted"] == pytest.approx(rep["critical_path_min"] * 60, abs=tol)
    assert s["est_time_error"] == pytest.approx(0.0, abs=0.06)       # against the stated 9 min it would be ~0.7


def test_summary_line_critical_path_and_total_formats():
    est, total, cp = harness._plan_numbers("", "critical path: T1 → T7 (5.0 min) · peak width 2 · "
                                               "predicted build 5.0 min · predicted total ≈ 8.5 min (target ✓)")
    assert (total, cp) == (510.0, 300.0) and est == {}


# ---- 5. the task's own prompt -----------------------------------------------------------------

def test_prompt_path_prefers_the_tasks_own_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "BENCH", tmp_path)
    (tmp_path / "prompt.md").write_text("Build the project", encoding="utf-8")
    (tmp_path / "tasks" / "t1").mkdir(parents=True)
    (tmp_path / "tasks" / "t2").mkdir(parents=True)
    (tmp_path / "tasks" / "t1" / "prompt.md").write_text("Review this branch", encoding="utf-8")
    assert harness.prompt_path(False, "t1") == tmp_path / "tasks" / "t1" / "prompt.md"
    assert harness.prompt_path(False, "t2") == tmp_path / "prompt.md"       # no prompt.md: fall back
    assert harness.prompt_path(False, "sitegen") == tmp_path / "prompt.md"
    assert harness.prompt_path(True, "t1") == tmp_path / "prompt-human.md"   # no prompt-human.md for t1


@pytest.mark.parametrize("task", ["combo-planted", "brownfield"])
def test_real_task_prompts_exist_and_end_with_the_marker(task):
    p = harness.prompt_path(False, task)
    assert p == ROOT / "bench" / "tasks" / task / "prompt.md" and p.is_file()
    text = p.read_text(encoding="utf-8")
    assert "Build the project" not in text and text.rstrip().endswith(harness.MARKER)


@pytest.mark.parametrize("task", ["combo-planted", "brownfield"])
def test_human_mode_uses_the_tasks_own_human_prompt(task):
    p = harness.prompt_path(True, task)
    assert p == ROOT / "bench" / "tasks" / task / "prompt-human.md" and p.is_file()
    text = p.read_text(encoding="utf-8")
    assert "Build the project" not in text and "product owner" in text and text.rstrip().endswith(harness.MARKER)
    assert harness.prompt_path(True, "combo") == ROOT / "bench" / "prompt-human.md"   # no own file: fallback
