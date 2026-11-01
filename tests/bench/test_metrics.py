"""Tests for the 0.3 benchmark metrics in bench/harness.py (plan T7).

Real-log tests run on trimmed copies of the e-series runs in fixtures/ (rebuild them with
fixtures/make_fixtures.py). Signals the old logs can't contain (polls, seams, prime hashes, plan
predictions) are tested on small synthetic event streams.
"""
import itertools
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bench"))
import harness  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


def stats(name: str) -> dict:
    return harness.dispatch_stats(harness.read_events(FIX / f"{name}.trim.jsonl"))


@pytest.fixture(scope="module")
def sl_e1():
    return stats("combo-superlite-e1")


@pytest.fixture(scope="module")
def sp_e1():
    return stats("combo-superpowers-e1")


# ---- real logs ----------------------------------------------------------------------------

def test_superlite_e1_queued_assignments_ge_1(sl_e1):
    # The final fix round for Tasks 3/5 ("do after Task 6") went to an agent still building Task 6.
    assert sl_e1["queued_assignments"] >= 1
    queued = [a for a in sl_e1["assignments"] if a["queued"]]
    assert queued and queued[0]["busy"] and queued[0]["kind"] == "fix"


def test_idle_reuse_not_counted_as_queued(sl_e1):
    # e1: Task 4 goes to the agent that finished Task 1: idle reuse, not queued.
    idle = [a for a in sl_e1["assignments"] if a["reuse_idle"]]
    assert sl_e1["agent_reuse_idle"] == len(idle) >= 4
    assert all(not a["queued"] for a in idle)
    # The first task after a fresh agent's READY is not reuse either.
    assert [a["reuse_idle"] for a in sl_e1["assignments"][:3]] == [False, False, False]

    log = Log()
    log.agent("c1", "a1", "T1 build", "do not act yet. READY", 0, 10, report="READY")
    log.send("a1", "assign Task 1", "Task 1: build it", 12)
    log.resume("a1", 12, 40, report="Status: DONE")
    log.send("a1", "assign Task 2", "Task 2: build more", 45)
    log.resume("a1", 45, 70, report="Status: DONE")
    s = harness.dispatch_stats(log.ev)
    assert (s["agent_reuse_idle"], s["queued_assignments"]) == (1, 0)


def test_queued_when_agent_busy_or_told_to_do_after():
    log = Log()
    log.agent("c1", "a1", "T1 build", "do not act yet. READY", 0, 10, report="READY")
    log.send("a1", "assign Task 1", "Task 1: build it", 12)
    # The fix round is sent at t=30, while the agent is still running (until t=60).
    log.resume("a1", 12, 60, report="Status: DONE",
               during=lambda: log.send("a1", "fix list", "Fix round for Task 1", 30))
    s = harness.dispatch_stats(log.ev)
    assert s["queued_assignments"] == 1 and s["agent_reuse_idle"] == 0

    idle = Log()
    idle.agent("c1", "a1", "T1 build", "do not act yet. READY", 0, 10, report="READY")
    idle.send("a1", "assign Task 1", "Task 1: build it", 12)
    idle.resume("a1", 12, 40, report="Status: DONE")
    idle.send("a1", "assign Task 2", "Task 2 (do after Task 6): build more", 50)
    assert harness.dispatch_stats(idle.ev)["queued_assignments"] == 1


def test_superpowers_e1_agent_reuse_0(sp_e1):
    assert sp_e1["agent_reuse_idle"] == 0
    assert sp_e1["queued_assignments"] == 0
    assert sp_e1["assignments"] == []


def test_tail_after_build_superlite_e1_about_16min(sl_e1):
    assert 900 <= sl_e1["tail_after_build_s"] <= 1100
    assert sl_e1["timeline"]["last_build_end_s"] < sl_e1["timeline"]["final_review_start_s"]


def test_superlite_e1_schedule_metrics(sl_e1):
    assert 300 <= sl_e1["longest_task_s"] <= 400                 # Task 6, the server
    assert 350 <= sl_e1["final_review_s"] <= 450                 # one Opus review, ~6.7 min
    assert sl_e1["final_fix_s"] > 0
    assert sl_e1["reuse_limit_breaches"] >= 1                    # B-engine reused past 80k context
    assert sl_e1["critical_path_s"] <= sl_e1["actual_total_s"]
    assert sl_e1["idle_gaps_s"] >= 0
    assert sl_e1["coordinator_turns"] == sl_e1["coordinator_calls"] > 0
    assert sl_e1["coordinator_report_lag_s"] is not None and sl_e1["coordinator_report_lag_s"] < 60
    assert sl_e1["reviews_batched"] == 2                         # the two "wave" reviews
    assert 0 < sl_e1["reviews_per_task"] < 1
    assert 0.5 < sl_e1["review_cost_usd"] < 2.0
    assert sl_e1["graphify_used"] is False
    assert sl_e1["incidents"] == {"count": 0, "by_kind": {}}


def test_metrics_absent_fields_default_none(sl_e1, sp_e1):
    for s in (sl_e1, sp_e1):
        for k in ("relevance_poll_s", "seam_checks", "seam_mismatches", "prime_hash_mismatches",
                  "est_time_error", "predicted_total_s", "critical_path_predicted"):
            assert s[k] is None, k
        assert s["relevance_polls"] == 0
    empty = harness.dispatch_stats([])                           # no events at all must not crash
    assert empty["queued_assignments"] == 0 and empty["tail_after_build_s"] is None
    assert empty["longest_task_s"] is None and empty["unsafe_kill_commands"] == 0


def test_unsafe_kill_detected_in_superlite_e3():
    assert stats("combo-superlite-e3")["unsafe_kill_commands"] == 1
    assert stats("combo-superlite-e1")["unsafe_kill_commands"] == 0
    assert stats("combo-superpowers-e2")["unsafe_kill_commands"] == 0


@pytest.mark.parametrize("cmd,hit", [
    ("taskkill //F //IM python.exe", True), ("taskkill /F /IM node.exe", True), ("pkill -f server", True),
    ("killall python", True), ("Stop-Process -Name python -Force", True), ("kill -9 -1", True),
    ("taskkill /F /PID 1234", False), ("kill 1234", False), ("Stop-Process -Id 1234", False),
    ("kill -9 12", False), ("uv run pytest -q", False),
])
def test_unsafe_kill_patterns(cmd, hit):
    log = Log()
    log.bash(cmd, 1)
    assert harness.dispatch_stats(log.ev)["unsafe_kill_commands"] == int(hit)


def test_superpowers_e2_implementers_6():
    s = stats("combo-superpowers-e2")
    assert s["implementer_dispatches"] == 6                      # "Task A1 ... Task B3"
    assert s["orchestrated"] is True
    names = [d["description"] for d in s["dispatches"]]
    assert sum(1 for n in names if n.startswith("Task ")) == 6


@pytest.mark.parametrize("desc,n", [("Task A1 sitegen core", 1), ("Task 3 engine", 1), ("T7+T8 server", 1),
                                    ("Implement parser", 1), ("Fix sitegen review findings", 0),
                                    ("Review sitegen vs spec", 0)])
def test_implementer_detection_descriptions(desc, n):
    log = Log()
    log.agent("c1", "a1", desc, "Do the thing.", 0, 10, subagent_type="general-purpose")
    assert harness.dispatch_stats(log.ev)["implementer_dispatches"] == n


# ---- synthetic streams for signals the old logs don't have ----------------------------------

def test_reuse_limit_breach_on_fourth_task():
    log = Log()
    log.agent("c1", "a1", "T1 build", "do not act yet. READY", 0, 5, report="READY")
    for i, t in enumerate((10, 30, 50, 70), 1):
        log.send("a1", f"assign Task {i}", f"Task {i}: build", t)
        log.resume("a1", t, t + 15, report="Status: DONE")
    s = harness.dispatch_stats(log.ev)
    assert s["agent_reuse_idle"] == 3 and s["queued_assignments"] == 0
    assert s["reuse_limit_breaches"] == 1                        # the 4th task


def test_relevance_polls_count_rounds_and_latency():
    log = Log()
    for n, aid in enumerate(("a1", "a2"), 1):
        log.agent(f"c{n}", aid, f"T{n} build", "do not act yet. READY", 0, 5, report="READY")
        log.send(aid, f"assign Task {n}", f"Task {n}: build", 10)
        log.resume(aid, 10, 30, report="Status: DONE")
    ends = {"a1": 48, "a2": 52}
    sids = {aid: log.send(aid, "poll", "Relevance 0-10 for task T3 (files x)? Reply `score: N`", 40,
                          mid="msg_poll") for aid in ends}      # one message, two tool calls
    for aid, end in ends.items():
        log.resume(aid, 41, end, report="score: 5", sid=sids[aid])
    s = harness.dispatch_stats(log.ev)
    assert s["relevance_polls"] == 1                             # two messages, one round
    assert s["relevance_poll_s"] == pytest.approx(12.0, abs=2.5)  # 40 s -> last answer at ~52 s
    assert s["queued_assignments"] == 0 and s["agent_reuse_idle"] == 0   # polls are not assignments


def test_seam_checks_and_mismatches():
    # Real pt_review output shapes ("PASS: verified: seam ..." / "FAIL: seam ...: N mismatch(es)");
    # tests/bench/test_real_formats.py runs the real script for them.
    log = Log()
    log.bash("bash superlite-cli pt_review contract --plan P --seam T3,T5", 1, result="PASS: verified: seam T3↔T5")
    log.bash("bash superlite-cli pt_review contract --plan P --seam T5,T7", 5,
             result="x.py:3  part1 T5: missing parameter 'a'\nFAIL: seam T5↔T7: 1 mismatch(es)")
    s = harness.dispatch_stats(log.ev)
    assert (s["seam_checks"], s["seam_mismatches"]) == (2, 1)


def test_prime_hash_mismatches():
    log = Log()
    log.bash("python .superlite/bin/build_prime.py", 1, result="wrote .superlite/prime.md\nhash aaaaaaaaaaaa1111")
    for n, h in enumerate(("aaaaaaaaaaaa1111", "aaaaaaaaaaaa1111", "bbbbbbbbbbbb2222")):
        log.agent(f"c{n}", f"a{n}", f"T{n + 1} build", f"prime-hash: {h}\nREADY", 5 + n, 9 + n, report="READY")
    assert harness.dispatch_stats(log.ev)["prime_hash_mismatches"] == 1


def test_predicted_vs_actual_and_est_time_error():
    plan = ("# Plan\n### T1 core  . Complexity 3\n**Est. time:** 2 min (1 component)\n"
            "### T2 server . Complexity 5\n**Est. time:** 2.0 min\n")
    log = Log()
    log.write("docs/plans/2026-plan.md", plan, 1)
    log.bash("python .superlite/bin/plan_tools.py schedule --plan P --insert", 2,
             result="critical path: T1 -> T2 (4.0 min) . peak width 2 . predicted total ≈ 8.5 min (target ok)")
    log.agent("c1", "a1", "T1 core", "Build T1", 10, 190, subagent_type="superlite:implementer")      # 3 min vs 2
    log.agent("c2", "a2", "T2 server", "Build T2", 10, 130, subagent_type="superlite:implementer")    # 2 min vs 2
    log.bash("echo done", 600)
    s = harness.dispatch_stats(log.ev)
    assert s["est_time_error"] == pytest.approx(0.25, abs=0.02)
    assert s["predicted_total_s"] == pytest.approx(510)
    assert s["critical_path_predicted"] == pytest.approx(240)
    assert s["longest_task_s"] == pytest.approx(180, abs=2)
    assert s["actual_total_s"] == pytest.approx(600, abs=2)
    assert s["tail_after_build_s"] == pytest.approx(410, abs=3)


def test_incidents_by_kind_and_graphify():
    log = Log()
    log.bash("python .superlite/bin/incident.py add --file a.py --line 3 --kind exception --context x", 1)
    log.bash("python .superlite/bin/incident.py add --file b.py --line 4 --kind workaround --context y", 2)
    log.bash("python .superlite/bin/incident.py add --file c.py --line 5 --kind exception --context z", 3)
    log.bash("graphify update .", 4)
    s = harness.dispatch_stats(log.ev)
    assert s["incidents"] == {"count": 3, "by_kind": {"exception": 2, "workaround": 1}}
    assert s["graphify_used"] is True


def test_final_review_and_fix_split():
    log = Log()
    log.agent("c1", "a1", "T1 build", "Build T1", 0, 100, subagent_type="superlite:implementer")
    log.agent("c2", "a2", "Final review", "Review all", 120, 150, subagent_type="superlite:final-reviewer",
              text="Verdict: CHANGES_REQUESTED", verdict_at=140)
    log.agent("c3", "a3", "Final review fixes", "Fix it", 160, 230, subagent_type="superlite:implementer")
    s = harness.dispatch_stats(log.ev)
    assert s["final_review_s"] == pytest.approx(20 + 1, abs=3)    # dispatch at ~119 s, verdict at 140 s
    assert s["final_fix_s"] == pytest.approx(90, abs=3)           # verdict to the last fix end
    assert s["tail_after_build_s"] == pytest.approx(130, abs=3)


# ---- synthetic stream builder --------------------------------------------------------------

class Log:
    """Builds the stream-json shapes the metrics read: coordinator tool_use events, agent
    task_started / task_notification system events, and subagent assistant events."""

    def __init__(self):
        self.ev = []
        self._ids = itertools.count(1)
        self.calls = {}            # agent id -> its Agent tool_use id (the subagents' parent id)

    @staticmethod
    def stamp(t: float) -> str:
        return (EPOCH + timedelta(seconds=t)).isoformat().replace("+00:00", "Z")

    def _assistant(self, t, blocks, parent=None, mid=None, usage=None):
        msg = {"id": mid or f"msg_{next(self._ids)}", "model": "claude-sonnet-5-5", "content": blocks}
        if usage:
            msg["usage"] = usage
        e = {"type": "assistant", "timestamp": self.stamp(t), "message": msg}
        if parent:
            e["parent_tool_use_id"] = parent
        self.ev.append(e)

    def _tool(self, t, name, inp, mid=None):
        tid = f"toolu_{next(self._ids):05d}"
        self._assistant(t, [{"type": "tool_use", "id": tid, "name": name, "input": inp}], mid=mid)
        return tid

    def bash(self, cmd, t, result=None):
        tid = self._tool(t, "Bash", {"command": cmd})
        if result is not None:
            self.ev.append({"type": "user", "timestamp": self.stamp(t + 0.1), "message": {"content": [
                {"type": "tool_result", "tool_use_id": tid, "content": result}]}})

    def write(self, path, content, t):
        self._tool(t, "Write", {"file_path": path, "content": content})

    def _sub(self, parent, t, text=None, ctx=20000):
        blocks = [{"type": "text", "text": text}] if text else []
        self._assistant(t, blocks, parent=parent, usage={"input_tokens": 1, "output_tokens": 5,
                                                         "cache_read_input_tokens": ctx})

    def _notify(self, aid, tid, report):
        self.ev.append({"type": "system", "subtype": "task_notification", "task_id": aid, "tool_use_id": tid,
                        "status": "completed", "handback_report": {"text": report}})

    def agent(self, call, aid, desc, prompt, t0, t1, report="Status: DONE", subagent_type="general-purpose",
              text=None, verdict_at=None):
        """A fresh agent run from t0 to t1 (the dispatch is issued 1 s before it starts)."""
        tid = self._tool(t0 - 1, "Agent", {"description": desc, "subagent_type": subagent_type,
                                           "prompt": prompt})
        self.calls[aid] = tid
        self.ev.append({"type": "system", "subtype": "task_started", "task_id": aid, "tool_use_id": tid,
                        "task_type": "local_agent", "description": desc, "subagent_type": subagent_type,
                        "prompt": prompt})
        self._sub(tid, t0)
        if text:
            self._sub(tid, verdict_at or t0 + 1, text=text)
        self._sub(tid, t1)
        self._notify(aid, tid, report)

    def send(self, aid, summary, message, t, mid=None):
        self._last_send = self._tool(t, "SendMessage", {"to": aid, "summary": summary, "message": message},
                                     mid=mid)
        return self._last_send

    def resume(self, aid, t0, t1, report="Status: DONE", sid=None, during=None):
        """The run a SendMessage woke (events carry the original Agent call as parent). `during`
        emits coordinator events while the agent is running, before its report arrives."""
        sid = sid or self._last_send
        self.ev.append({"type": "system", "subtype": "task_started", "task_id": aid, "tool_use_id": sid,
                        "task_type": "local_agent"})
        self._sub(self.calls[aid], t0 + 0.5)
        if during:
            during()
        self._sub(self.calls[aid], t1)
        self._notify(aid, sid, report)
