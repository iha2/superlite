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
    "yeah", "yep", "sure", "okay", "sounds good", "sgtm", "perfect", "absolutely, let's build it",
    "ready to approve", "I want to approve",
    "yes but add tests", "approve, but change the name", "no problem, go ahead", "fine by me",
    "lgtm, but rename the title", "Looks good. Can you also add a test?", "I like it, proceed",
    "approve?",
    "Go ahead and rename the module, then update every caller, rerun the tests, and send me the results after",
])
def test_reply_approves(human, prompt):
    t0 = time.time() * 1000
    r = reply(human, prompt)
    assert r.returncode == 0 and r.stdout == "" and r.stderr == ""
    (rec,) = lines(human)
    assert rec["approves"] is True and rec["text"] == prompt
    assert t0 - 50 <= rec["t"] <= time.time() * 1000 + 50
    assert list(rec) == ["t", "approves", "text"]


def test_reply_approves_a_thumbs_up(human):
    raw = json.dumps({"hook_event_name": "UserPromptSubmit", "cwd": str(human), "prompt": "👍"}, ensure_ascii=False)
    assert reply(human, None, raw=raw).returncode == 0
    (rec,) = lines(human)
    assert rec["approves"] is True and rec["text"] == "👍"


@pytest.mark.parametrize("prompt", [
    "no", "not yet", "don't approve", "dont proceed", "do not approve", "please don't proceed",
    "wait, change the scope", "stop", "instead, do X", "hmm", "Create hello.txt containing hi",
    "unapproved", "yesterday", "nothing", "what about the schema?", "Should I approve it?",
    "can we go ahead?", "not approved", "not sure", "not perfect", "make sure the tests cover empty files",
    "I'd like it if the button were bigger",
    "The guard is not registered, so the gate is held only by the sl tools refusing to run until you approve. "
    "Can we fix this as well or am I misunderstanding?",
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
    ups = [h["command"] for h in data["UserPromptSubmit"][0]["hooks"]]
    assert ups[0] == 'bash "${CLAUDE_PLUGIN_ROOT}/hooks/record-human-reply"'
    assert 'bash "${CLAUDE_PLUGIN_ROOT}/hooks/cost-report"' in ups
    for h in (GUARD, REPLY):
        assert b"\r" not in h.read_bytes()


def cursor_reply(proj, prompt, roots, cwd):
    raw = json.dumps({"hook_event_name": "beforeSubmitPrompt", "prompt": prompt, "conversation_id": "c1",
                      "generation_id": "g1", "attachments": [], "workspace_roots": roots})
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PROJECT_DIR", "CURSOR_PROJECT_DIR")}
    return subprocess.run([BASH, str(REPLY)], input=raw, capture_output=True, text=True, env=env, cwd=str(cwd))


def cursor_root(p):
    s = str(p).replace("\\", "/")
    return "/" + s[0].lower() + s[1:] if len(s) > 1 and s[1] == ":" else s


def test_cursor_reply_recorded_from_workspace_root(human, tmp_path_factory):
    elsewhere = tmp_path_factory.mktemp("plugin_root")  # Cursor runs plugin hooks outside the project
    r = cursor_reply(human, "I approve the plan. Go ahead.", [cursor_root(human)], elsewhere)
    assert r.returncode == 0 and json.loads(r.stdout) == {"continue": True} and r.stderr == ""
    (rec,) = lines(human)
    assert rec["approves"] is True and rec["text"] == "I approve the plan. Go ahead."
    assert not (elsewhere / ".superlite").exists()


def test_cursor_reply_picks_the_root_with_a_run(human, tmp_path_factory):
    other = tmp_path_factory.mktemp("other_root")
    r = cursor_reply(human, "approve", [cursor_root(other), cursor_root(human)], other)
    assert r.returncode == 0 and json.loads(r.stdout) == {"continue": True}
    assert lines(human)[0]["approves"] is True and not (other / ".superlite").exists()


def test_cursor_reply_inactive_still_continues(tmp_path):
    make(tmp_path, "ai")
    r = cursor_reply(tmp_path, "approve", [cursor_root(tmp_path)], tmp_path)
    assert r.returncode == 0 and json.loads(r.stdout) == {"continue": True} and lines(tmp_path) == []


def launcher(name):
    return f"git -c 'alias.sl-hook=!cd \"${{GIT_PREFIX:-.}}\" && exec bash ./hooks/{name}' sl-hook"


def test_cursor_registration():
    data = json.loads((ROOT / "hooks" / "hooks-cursor.json").read_text(encoding="utf-8"))["hooks"]
    want = {"sessionStart": ["session-start"],
            "beforeSubmitPrompt": ["record-human-reply", "cost-report"],
            "preToolUse": ["block-coordinator-edits"], "subagentStart": ["capture-agent-launch"],
            "subagentStop": ["capture-agent-stop", "capture-host-usage"],
            "stop": ["capture-host-usage"], "afterAgentResponse": ["capture-host-usage"]}
    assert {k: [e["command"] for e in v] for k, v in data.items()} == {
        k: [launcher(n) for n in names] for k, names in want.items()
    }


def windows_path():
    import winreg  # noqa: F401  (Windows only)
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "[Environment]::GetEnvironmentVariable('Path','Machine') + ';' + "
                          "[Environment]::GetEnvironmentVariable('Path','User')"],
                         capture_output=True, text=True)
    return out.stdout.strip()


@pytest.mark.skipif(os.name != "nt", reason="Cursor runs hooks through PowerShell on Windows")
def test_cursor_launcher_runs_without_bash_on_path(human, tmp_path_factory):
    # Git for Windows puts only git on PATH; the launcher reaches Git's own bash through a git alias.
    path = windows_path()
    clean = {k: v for k, v in os.environ.items() if k.upper() not in ("PATH", "CLAUDE_PROJECT_DIR", "CURSOR_PROJECT_DIR")}
    clean["Path"] = path
    probe = subprocess.run(["powershell", "-NoProfile", "-Command", "[bool](Get-Command git -ErrorAction SilentlyContinue)"],
                           capture_output=True, text=True, env=clean)
    if probe.stdout.strip() != "True":
        pytest.skip("git is not on the Windows PATH")
    payload = tmp_path_factory.mktemp("payload") / "in.json"
    payload.write_text(json.dumps({"hook_event_name": "beforeSubmitPrompt", "prompt": "approve",
                                   "workspace_roots": [cursor_root(human)]}), encoding="utf-8")
    ps = (f"Get-Content '{payload}' -Raw | & {{ $input | {launcher('record-human-reply')} }}")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, env=clean, cwd=str(ROOT))
    assert json.loads(r.stdout.lstrip("\ufeff")) == {"continue": True}, r.stderr
    assert lines(human)[0]["approves"] is True


# ---- block-coordinator-edits, Cursor payloads ----

def cursor_guard(proj, tool, tool_input, cwd, sub=False):
    d = {"conversation_id": "c1", "generation_id": "g1", "model": "claude-opus-5-5", "tool_name": tool,
         "tool_input": tool_input, "tool_use_id": "t1", "cwd": "", "hook_event_name": "preToolUse",
         "cursor_version": "3.25.9", "workspace_roots": [cursor_root(proj)]}
    if sub:
        d["parent_tool_call_id"] = "toolu_parent"
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PROJECT_DIR", "CURSOR_PROJECT_DIR")}
    return subprocess.run([BASH, str(GUARD)], input=json.dumps(d), capture_output=True, text=True, env=env, cwd=str(cwd))


def denied(r, needle):
    out = json.loads(r.stdout)
    assert r.returncode == 2 and out["permission"] == "deny", r
    assert needle in out["user_message"] and out["agent_message"] == out["user_message"]
    return out


@pytest.fixture
def plugin_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("plugin_root")  # Cursor runs plugin hooks from the plugin's folder


def test_cursor_coordinator_denied_from_workspace_root(human, plugin_dir):
    denied(cursor_guard(human, "Task", {"subagent_type": "implementer", "prompt": "x"}, plugin_dir), NEXT)
    denied(cursor_guard(human, "Task", {"subagent_type": "generalPurpose", "prompt": "x"}, plugin_dir), NEXT)
    denied(cursor_guard(human, "Shell", {"command": 'bash "$SL" dispatch --plan p.md', "cwd": ""}, plugin_dir), NEXT)
    denied(cursor_guard(human, "Write", {"file_path": str(human / ".superlite" / "orchestrator.json")}, plugin_dir),
           "sl orchestrator")
    denied(cursor_guard(human, "Delete", {"file_path": str(human / ".superlite" / "human-replies.jsonl")}, plugin_dir),
           "sl orchestrator")


def test_cursor_research_and_reads_allowed(human, plugin_dir):
    for t in ("explore", "Explore", "Plan", "cursor-guide"):
        r = cursor_guard(human, "Task", {"subagent_type": t, "prompt": "x"}, plugin_dir)
        assert r.returncode == 0 and r.stdout == "", t
    for tool, ti in (("Shell", {"command": "git status", "cwd": ""}), ("Write", {"file_path": str(human / "hello.txt")}),
                     ("Read", {"file_path": str(human / ".superlite" / "orchestrator.json")})):
        r = cursor_guard(human, tool, ti, plugin_dir)
        assert r.returncode == 0 and r.stdout == "", (tool, ti)


def test_cursor_subagent_calls_pass(human, plugin_dir):
    for tool, ti in (("Write", {"file_path": str(human / ".superlite" / "orchestrator.json")}),
                     ("Shell", {"command": "echo a > src.py", "cwd": ""}),
                     ("Task", {"subagent_type": "implementer", "prompt": "x"})):
        r = cursor_guard(human, tool, ti, plugin_dir, sub=True)
        assert r.returncode == 0 and r.stdout == "", (tool, ti)


def test_cursor_active_run_keeps_project_code_from_the_coordinator(tmp_path, plugin_dir):
    proj = make(tmp_path, "ai")
    (proj / ".superlite" / "stable.json").write_text("{}")
    denied(cursor_guard(proj, "Write", {"file_path": str(proj / "src" / "app.py")}, plugin_dir), "must not write project code")
    denied(cursor_guard(proj, "Delete", {"file_path": str(proj / "src" / "app.py")}, plugin_dir), "must not write project code")
    denied(cursor_guard(proj, "Shell", {"command": "echo x > src/app.py", "cwd": ""}, plugin_dir), "must not write project code")
    for p in ("docs/plans/a.md", ".superlite/progress.md", "SPEC.md"):
        r = cursor_guard(proj, "Write", {"file_path": str(proj / p)}, plugin_dir)
        assert r.returncode == 0 and r.stdout == "", p
    r = cursor_guard(proj, "Write", {"file_path": str(proj / "src" / "app.py")}, plugin_dir, sub=True)
    assert r.returncode == 0 and r.stdout == ""


def test_claude_denials_stay_on_stderr(human):
    r = guard(human, "Agent", {"subagent_type": "superlite:implementer", "prompt": "x"})
    assert r.returncode == 2 and r.stdout == "" and NEXT in r.stderr


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
