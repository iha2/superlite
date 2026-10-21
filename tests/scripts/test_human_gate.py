import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / "hooks" / "block-coordinator-edits"
REPLY = ROOT / "hooks" / "record-human-reply"
_GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"
BASH = _GIT_BASH if os.name == "nt" and os.path.exists(_GIT_BASH) else shutil.which("bash")

NEXT = "Human orchestrator: no build is orchestrated until the human approves the plan."


def make(tmp_path, state):
    sd = tmp_path / ".superlite"
    sd.mkdir()
    if state == "human":
        (sd / "orchestrator.json").write_text('{"mode": "human", "approved": false}')
    elif state == "approved":
        (sd / "orchestrator.json").write_text('{"mode": "human", "approved": true}')
    elif state == "ai":
        (sd / "orchestrator.json").write_text('{"mode":"ai","approved":false}')
    return tmp_path


@pytest.fixture
def human(tmp_path):
    return make(tmp_path, "human")


def guard(proj, tool, tool_input, **extra):
    raw = json.dumps({"tool_name": tool, "tool_input": tool_input, **extra})
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(proj)}
    return subprocess.run([BASH, str(GUARD)], input=raw, capture_output=True, text=True, env=env)


def bash(proj, cmd):
    return guard(proj, "Bash", {"command": cmd})


def test_human_allows_inline_code_writes(human):
    # Small inline work needs only the human's yes in chat: before approval only orchestration is blocked.
    for tool, ti in (("Write", {"file_path": str(human / "hello.txt")}), ("Edit", {"file_path": "src/a.py"}),
                     ("NotebookEdit", {"notebook_path": "n.ipynb"}), ("Write", {"file_path": "SPEC.md"}),
                     ("Write", {"file_path": "docs/guide.md"}), ("Write", {"file_path": "docs/plans/p.md"})):
        assert guard(human, tool, ti).returncode == 0, (tool, ti)


def test_human_denial_names_the_review_plan_and_the_inline_path(human):
    r = bash(human, 'bash "$SL" dispatch implementer t1')
    assert r.returncode == 2
    assert NEXT in r.stderr and "sl orchestrator approve --review" in r.stderr and "yes in chat" in r.stderr


def test_human_allows_review_plan_and_state_files(human):
    for p in ("docs/plans/2026-01-01-x.review.md", str(human / "docs" / "plans" / "a.review.md"),
              ".superlite/progress.md", ".superlite/briefs/t1.md", "/elsewhere/x.py"):
        assert guard(human, "Write", {"file_path": p}).returncode == 0, p
    assert bash(human, "cat > docs/plans/a.review.md <<'EOF'\nplan\nEOF").returncode == 0


@pytest.mark.parametrize("cmd", [
    'bash "$SL" dispatch implementer t1', "sl assign t1", 'bash "$SL" wave0', 'bash "$SL" build_prime --plan p.md',
    'bash "$SL" checkpoint T1', 'bash "$SL" commit_task T1', 'bash "$SL" finish --plan p.md',
])
def test_human_denies_bash_builds(human, cmd):
    r = bash(human, cmd)
    assert r.returncode == 2, cmd
    assert NEXT in r.stderr


@pytest.mark.parametrize("cmd", [
    "ls", "git status", "cat README.md", "grep -rn x .", "python3 -m pytest -q", 'bash "$SL" run_tests --full',
    "echo hi > hello.txt", "sed -i s/a/b/ src/a.py", "echo x | tee hello.txt", "touch x; echo a >> y.py",
    "python3 -c \"open('hello.txt','w').write('hi')\"", "git apply p.patch", "rm src/a.py",
    'bash "$SL" orchestrator approve --review docs/plans/a.review.md', "sl orchestrator status",
    'bash "$SL" next_tasks', "echo hi > .superlite/progress.md",
])
def test_human_allows_reads_and_orchestrator(human, cmd):
    assert bash(human, cmd).returncode == 0, cmd


def test_human_agent_calls(human):
    for t in ("superlite:implementer-haiku", "superlite:reviewer", "general-purpose", ""):
        r = guard(human, "Agent", {"subagent_type": t, "prompt": "x"})
        assert r.returncode == 2, t
        assert NEXT in r.stderr
    for t in ("Explore", "Plan", "claude-code-guide"):
        assert guard(human, "Agent", {"subagent_type": t, "prompt": "x"}).returncode == 0, t


def test_subagents_untouched(human):
    for tool, ti in (("Write", {"file_path": "hello.txt"}), ("Bash", {"command": "echo a > hello.txt"}),
                     ("Write", {"file_path": ".superlite/orchestrator.json"})):
        assert guard(human, tool, ti, agent_id="a1", agent_type="implementer").returncode == 0


@pytest.mark.parametrize("state", ["ai", "approved", "none"])
def test_nothing_blocked_without_unapproved_human(tmp_path, state):
    proj = make(tmp_path, state)
    assert guard(proj, "Write", {"file_path": "hello.txt"}).returncode == 0
    assert bash(proj, "echo hi > hello.txt").returncode == 0
    assert bash(proj, "git apply p.patch").returncode == 0
    assert guard(proj, "Agent", {"subagent_type": "general-purpose"}).returncode == 0
    assert guard(proj, "Agent", {"subagent_type": "superlite:implementer-haiku"}).returncode == 0


def test_no_superlite_dir_untouched(tmp_path):
    assert guard(tmp_path, "Write", {"file_path": "hello.txt"}).returncode == 0
    assert guard(tmp_path, "Write", {"file_path": ".superlite/orchestrator.json"}).returncode == 0


@pytest.mark.parametrize("state", ["human", "ai", "approved", "none"])
@pytest.mark.parametrize("name", ["orchestrator.json", "human-replies.jsonl"])
def test_protected_files_denied_in_every_state(tmp_path, state, name):
    proj = make(tmp_path, state)
    for tool, ti in (("Write", {"file_path": f".superlite/{name}"}),
                     ("Edit", {"file_path": str(proj / ".superlite" / name)})):
        r = guard(proj, tool, ti)
        assert r.returncode == 2, (tool, ti)
        assert "sl orchestrator" in r.stderr
    for cmd in (f"echo x > .superlite/{name}", f"echo x >> .superlite/{name}", f"sed -i s/a/b/ .superlite/{name}",
                f"echo x | tee .superlite/{name}", f"rm .superlite/{name}",
                f"python3 -c \"open('.superlite/{name}','w').write('x')\"",
                f"cat > .superlite/{name} <<'EOF'\n{{}}\nEOF"):
        r = bash(proj, cmd)
        assert r.returncode == 2, cmd
        assert "sl orchestrator" in r.stderr


def test_protected_reads_and_sl_orchestrator_allowed(tmp_path):
    proj = make(tmp_path, "ai")
    for cmd in ("cat .superlite/orchestrator.json", "grep approved .superlite/orchestrator.json",
                'bash "$SL" orchestrator mode human', "sl orchestrator approve --review docs/plans/a.review.md"):
        assert bash(proj, cmd).returncode == 0, cmd


def test_guard_fails_open(human):
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(human)}
    for raw in ("", "not json", "[]", "{", json.dumps({"tool_name": "Agent"}),
                json.dumps({"tool_name": "Bash", "tool_input": {"command": 5}})):
        r = subprocess.run([BASH, str(GUARD)], input=raw, capture_output=True, text=True, env=env)
        if '"Agent"' in raw:
            continue  # an Agent call with no subagent_type is general-purpose
        assert r.returncode == 0 and r.stderr == "", raw


# ---- record-human-reply ----

def reply(proj, prompt, raw=None):
    if raw is None:
        raw = json.dumps({"hook_event_name": "UserPromptSubmit", "cwd": str(proj), "prompt": prompt})
    env = {**os.environ}
    env.pop("CLAUDE_PROJECT_DIR", None)
    return subprocess.run([BASH, str(REPLY)], input=raw, capture_output=True, text=True, env=env, cwd=str(proj))


def lines(proj):
    p = proj / ".superlite" / "human-replies.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


@pytest.mark.parametrize("prompt", [
    "approve", "Approved", "yes", "Yes, approve", "go ahead", "Go for it!", "looks good", "LGTM",
    "proceed", "ship it", "do it", "yes, approve it", "OK looks good to me, go ahead",
])
def test_reply_approves(human, prompt):
    t0 = time.time() * 1000
    r = reply(human, prompt)
    assert r.returncode == 0 and r.stdout == "" and r.stderr == ""
    (rec,) = lines(human)
    assert rec["approves"] is True and rec["text"] == prompt
    assert t0 - 50 <= rec["t"] <= time.time() * 1000 + 50
    assert list(rec) == ["t", "approves", "text"]


@pytest.mark.parametrize("prompt", [
    "no", "not yet", "don't approve", "dont proceed", "wait, change the scope", "yes but add tests",
    "stop", "instead, do X", "approve, but change the name", "hmm", "Create hello.txt containing hi",
    "unapproved", "yesterday", "nothing", "what about the schema?",
])
def test_reply_does_not_approve(human, prompt):
    reply(human, prompt)
    (rec,) = lines(human)
    assert rec["approves"] is False


def test_reply_inactive_states_write_nothing(tmp_path):
    for state in ("ai", "approved", "none"):
        d = tmp_path / state
        d.mkdir()
        make(d, state)
        reply(d, "yes approve")
        assert lines(d) == []
    d = tmp_path / "nodir"
    d.mkdir()
    reply(d, "yes approve")
    assert not (d / ".superlite").exists()


def test_reply_appends_and_escapes(human):
    nasty = 'yes "approve" \\ it\nnext line {x}'
    reply(human, "first")
    reply(human, nasty)
    reply(human, "é" * 200)
    a, b, c = lines(human)
    assert a["text"] == "first"
    assert b["text"] == 'yes "approve" \\ it next line {x}' and b["approves"] is True
    assert len(c["text"]) == 160


def test_reply_fails_open(human):
    for raw in ("", "not json", "[]", "{", '{"prompt": 5}', '{"cwd": "x"}'):
        r = reply(human, None, raw=raw)
        assert r.returncode == 0 and r.stdout == "" and r.stderr == "", raw
    for rec in lines(human):
        assert set(rec) == {"t", "approves", "text"}


def test_registration_and_endings():
    data = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    guard_entry = [e for e in data["PreToolUse"] if e["hooks"][0]["command"].endswith("block-coordinator-edits\"")][0]
    assert "Agent" in guard_entry["matcher"].split("|")
    assert data["UserPromptSubmit"][0]["hooks"][0]["command"] == 'bash "${CLAUDE_PLUGIN_ROOT}/hooks/record-human-reply"'
    for h in (GUARD, REPLY):
        assert b"\r" not in h.read_bytes()


def test_latency(human):
    def best(argv, raw, cwd):
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(human)}
        times = []
        for _ in range(3):
            t = time.perf_counter()
            subprocess.run(argv, input=raw, capture_output=True, text=True, env=env, cwd=cwd)
            times.append(time.perf_counter() - t)
        return min(times)

    base = best([BASH, "-c", "exit 0"], "", str(human))
    cases = (
        (GUARD, json.dumps({"tool_name": "Bash", "tool_input": {"command": "git status && ls"}})),
        (GUARD, json.dumps({"tool_name": "Write", "tool_input": {"file_path": "a.py"}})),
        (REPLY, json.dumps({"cwd": str(human), "prompt": "yes, approve"})),
    )
    for hook, raw in cases:
        t = best([BASH, str(hook)], raw, str(human))
        assert t < max(0.6, base + 0.35), (hook.name, t, base)
