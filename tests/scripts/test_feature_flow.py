"""Feature flow: superlite-cli proto (sandbox, feature.json, the try-out gate), the hooks' try-out state, the report's two parts,
and the command and skill texts."""
import json
import os
import subprocess
import sys
import time

import pytest

from test_human_gate import BASH, guard, lines, reply
from test_plan_tools import SCRIPTS

sys.path.insert(0, str(SCRIPTS))
import orchestrator  # noqa: E402
import report  # noqa: E402
from test_proto_sandbox import fakes, inproc, rows  # noqa: E402,F401  (fakes: the autouse seam for T1, T2, T4)

TRYOUT_NEXT = "Feature flow: the integration build waits for the human's try-out of the prototype."


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "app"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "t")
    (r / ".gitignore").write_text(".superlite/\n")
    (r / "main.go").write_text("package main\n")
    (r / "docs" / "plans").mkdir(parents=True)
    (r / "docs" / "plans" / "2026-10-21-export.feature.md").write_text(
        "# Export\n\n## Acceptance checks\n- open /report and press Export\n- the file has a header row\n\n## Code map\n- x\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    return r


def proto(repo, *args):
    return inproc(*args, cwd=repo)


def state(repo):
    return json.loads((repo / ".superlite" / "feature.json").read_text(encoding="utf-8"))


def set_mode(repo, mode, approved):
    (repo / ".superlite").mkdir(exist_ok=True)
    (repo / ".superlite" / "orchestrator.json").write_text(json.dumps({"mode": mode, "approved": approved}))


def test_start_makes_a_sandbox_and_records_feature_json(repo):
    cp = proto(repo, "start", "--topic", "export", "--run", "go run .", "--reach", "http://localhost:8080")
    assert cp.returncode == 0, cp.both
    st = state(repo)
    sb = repo / ".superlite" / "sandboxes" / "host" / "export"
    assert st["status"] == "building" and st["level"] == "L1" and st["run"] == "go run ." and sb.is_dir()
    assert st["sandbox"] == sb.as_posix() and "worktree" not in st and "branch" not in st
    assert st["plan"] == "docs/plans/2026-10-21-export.feature.md"
    assert "superlite/export-proto" not in git(repo, "branch", "--list")
    assert git(repo, "status", "--porcelain") == ""  # the user's checkout is untouched
    again = proto(repo, "start", "--topic", "export")  # same topic: a fix round, not a second sandbox
    assert again.returncode == 0 and "fix round 2" in again.out and state(repo)["rounds"] == 2
    assert proto(repo, "start", "--topic", "other").returncode == 2  # one open prototype at a time
    assert proto(repo, "start", "--topic", "Bad Topic").returncode == 2


def test_tryout_prints_how_to_try_it_and_the_checks(repo):
    proto(repo, "start", "--topic", "export", "--run", "go run .")
    cp = proto(repo, "tryout", "--reach", "http://localhost:8080")
    assert cp.returncode == 0, cp.both
    assert 'run: cd "' in cp.out and "&& go run ." in cp.out and "reach: http://localhost:8080" in cp.out
    assert "- open /report and press Export" in cp.out and "- the file has a header row" in cp.out
    assert "superlite-cli proto tryout --approve" in cp.out and state(repo)["status"] == "tryout" and state(repo)["tryout_time"]


def test_human_mode_tryout_approval_needs_a_reply_newer_than_the_tryout(repo):
    set_mode(repo, "human", True)  # the feature plan was approved earlier
    proto(repo, "start", "--topic", "export", "--run", "true")
    sl = repo / ".superlite"
    (sl / "human-replies.jsonl").write_text(json.dumps({"t": 1, "approves": True, "text": "approve"}) + "\n")  # the plan approval
    proto(repo, "tryout")
    assert orchestrator.gate(sl, "dispatch") == orchestrator.TRYOUT_BLOCK  # the integration build waits
    cp = proto(repo, "tryout", "--approve")
    assert cp.returncode == 3 and "BLOCKED: no human reply since the prototype was shown" in cp.err
    time.sleep(0.02)
    with (sl / "human-replies.jsonl").open("a") as fh:
        fh.write(json.dumps({"t": time.time() * 1000, "approves": False, "text": "ship it, the button can wait"}) + "\n")
    cp = proto(repo, "tryout", "--approve", "--by", "dana")
    assert cp.returncode == 0, cp.both
    st = state(repo)
    assert st["status"] == "approved" and st["tryout_approved"] is True and st["tryout_by"] == "dana" and st["tryout_reply"] == "ship it, the button can wait"
    assert "try-out approved" in cp.out and orchestrator.gate(sl, "dispatch") is None


def test_ai_mode_records_a_ruling_and_is_never_gated(repo):
    set_mode(repo, "ai", True)
    proto(repo, "start", "--topic", "export")
    proto(repo, "tryout")
    sl = repo / ".superlite"
    assert orchestrator.gate(sl, "dispatch") is None
    cp = proto(repo, "tryout", "--approve", "--ruling", "acceptance checks 2/2 passed")
    assert cp.returncode == 0 and state(repo)["status"] == "approved"
    assert "Ruling: [tryout] acceptance checks 2/2 passed" in (sl / "progress.md").read_text()


def test_gate_fails_open_and_skips_the_prototype_build(repo, tmp_path):
    set_mode(repo, "human", True)
    sl = repo / ".superlite"
    (sl / "feature.json").write_text("{not json")
    assert orchestrator.gate(sl, "dispatch") is None
    (sl / "feature.json").write_text(json.dumps({"status": "tryout"}))
    plan_in_proto = sl.parent / ".claude" / "worktrees" / "export-proto" / "docs" / "plans" / "p.proto.md"
    assert orchestrator.gate(plan_in_proto, "dispatch") is None  # the prototype build has its own .superlite
    assert orchestrator.gate(repo / "docs" / "plans" / "p.md", "dispatch") == orchestrator.TRYOUT_BLOCK


def test_discard_saves_the_report_and_removes_the_sandbox(repo):
    proto(repo, "start", "--topic", "export")
    sb = repo / ".superlite" / "sandboxes" / "host" / "export"
    head = {"wall_min": {"projected": 4.0, "actual": 5.0}, "cost_usd_agents": {"projected": 0.5, "actual": 0.6}}
    (repo / ".superlite" / "report.json").write_text(json.dumps({"headline": head, "tasks": [{}, {}]}))
    cp = proto(repo, "discard")
    assert cp.returncode == 0, cp.both
    st = state(repo)
    assert st["status"] == "discarded" and st["proto_report"]["headline"] == head and st["proto_report"]["tasks"] == 2
    assert not sb.exists()
    assert "status" in proto(repo, "status", "--json").out and proto(repo, "discard").out.startswith("proto: nothing")


def test_report_shows_prototype_and_integration_as_two_parts(tmp_path):
    sl = tmp_path / ".superlite"
    sl.mkdir()
    head = {"wall_min": {"projected": 4.0, "actual": 5.0}, "cost_usd_agents": {"projected": 0.5, "actual": 0.6}}
    (sl / "feature.json").write_text(json.dumps({"topic": "export", "status": "discarded", "proto_report": {"headline": head, "tasks": 3}}))
    integ = {"wall_min": {"projected": 12.0, "actual": 15.0}, "cost_usd_agents": {"projected": 2.0, "actual": 2.4}}
    f = report.feature_parts(sl, integ)
    assert f["prototype"]["wall_min"] == {"projected": 4.0, "actual": 5.0} and f["integration"]["cost_usd"]["actual"] == 2.4
    assert f["share_pct"] == 25.0 and f["cap_pct"] == 25
    a, b = report.feature_lines(f)
    assert a.startswith("prototype   time 4.0 -> 5.0 min, cost $0.50 -> $0.60 (25% of the integration")
    assert b.startswith("integration time 12.0 -> 15.0 min, cost $2.00 -> $2.40")
    assert report.feature_parts(tmp_path / "none", integ) is None


def test_report_end_to_end_keeps_the_summary_short(tmp_path):
    from test_report import fixture, go
    p = fixture(tmp_path)
    sl = tmp_path / ".superlite"
    head = {"wall_min": {"projected": 1.0, "actual": 1.5}, "cost_usd_agents": {"projected": 0.1, "actual": 0.2}}
    (sl / "feature.json").write_text(json.dumps({"topic": "export", "status": "approved", "proto_report": {"headline": head}}))
    txt = go(tmp_path, p)
    assert "  prototype   time 1.0 -> 1.5 min" in txt and "  integration time" in txt and len(txt.splitlines()) <= 10
    assert "feature flow (export): two builds" in go(tmp_path, p, "--full")


# ------------------------------------------------------------------ hooks: the try-out state

def feature(proj, mode, approved, status):
    sd = proj / ".superlite"
    sd.mkdir(exist_ok=True)
    (sd / "orchestrator.json").write_text(json.dumps({"mode": mode, "approved": approved}))
    (sd / "feature.json").write_text(json.dumps({"topic": "export", "status": status}) if status else "{oops")
    return proj


def test_hook_denies_integration_dispatch_until_the_tryout_is_approved(tmp_path):
    p = feature(tmp_path, "human", True, "tryout")
    r = guard(p, "Agent", {"subagent_type": "superlite:implementer", "prompt": "T1"})
    assert r.returncode == 2 and TRYOUT_NEXT in r.stderr and "superlite-cli proto tryout --approve" in r.stderr
    assert guard(p, "Bash", {"command": 'bash "$CLI" dispatch implementer t1'}).returncode == 2
    assert guard(p, "Agent", {"subagent_type": "Explore", "prompt": "map"}).returncode == 0  # research stays open
    assert guard(p, "Bash", {"command": 'bash "$CLI" proto tryout --approve'}).returncode == 0
    assert guard(p, "Write", {"file_path": "docs/plans/p.md"}).returncode == 0
    assert guard(p, "Agent", {"subagent_type": "superlite:implementer"}, agent_id="a1").returncode == 0  # subagents pass


@pytest.mark.parametrize("mode,approved,status", [("human", True, "approved"), ("human", True, "building"),
                                                  ("ai", True, "tryout"), ("human", True, None)])
def test_hook_allows_after_approval_in_ai_mode_and_on_bad_input(tmp_path, mode, approved, status):
    p = feature(tmp_path, mode, approved, status)
    assert guard(p, "Agent", {"subagent_type": "superlite:implementer"}).returncode == 0
    assert guard(p, "Bash", {"command": 'bash "$CLI" dispatch implementer t1'}).returncode == 0


def test_hook_protects_feature_json_and_the_sandbox(tmp_path):
    p = feature(tmp_path, "human", True, "building")
    r = guard(p, "Write", {"file_path": ".superlite/feature.json"})
    assert r.returncode == 2 and "superlite-cli proto" in r.stderr
    (p / ".superlite" / "pool.json").write_text("{}")  # an active run
    assert guard(p, "Edit", {"file_path": ".superlite/sandboxes/export-proto/main.go"}).returncode == 2
    assert guard(p, "Write", {"file_path": ".superlite/progress.md"}).returncode == 0


def test_reply_hook_records_while_the_tryout_waits(tmp_path):
    p = feature(tmp_path, "human", True, "tryout")
    reply(p, "it works")
    reply(p, "it works but the button is too small")
    reply(p, "the button is too small")
    a, b, c = lines(p)
    assert a["approves"] is True and b["approves"] is True and c["approves"] is False
    (tmp_path / "q").mkdir()
    q = feature(tmp_path / "q", "human", True, "approved")  # nothing waits on the human: nothing is recorded
    reply(q, "it works")
    assert lines(q) == []
