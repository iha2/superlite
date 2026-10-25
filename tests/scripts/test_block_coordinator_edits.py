import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "hooks" / "block-coordinator-edits"
_GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"
BASH = _GIT_BASH if os.name == "nt" and os.path.exists(_GIT_BASH) else shutil.which("bash")


@pytest.fixture
def proj(tmp_path):
    (tmp_path / ".superlite").mkdir()
    (tmp_path / ".superlite" / "pool.json").write_text("{}")
    return tmp_path


def run(proj, tool, tool_input, raw=None, **extra):
    if raw is None:
        raw = json.dumps({"tool_name": tool, "tool_input": tool_input, **extra})
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(proj)}
    return subprocess.run([BASH, str(HOOK)], input=raw, capture_output=True, text=True, env=env)


def bash(proj, cmd, tool="Bash"):
    return run(proj, tool, {"command": cmd})


def test_blocks_coordinator_edit_of_source(proj):
    for tool in ("Edit", "Write"):
        r = run(proj, tool, {"file_path": str(proj / "src" / "app.py")})
        assert r.returncode == 2
    assert run(proj, "NotebookEdit", {"notebook_path": str(proj / "a.ipynb")}).returncode == 2
    assert run(proj, "Write", {"file_path": "src/main.rs"}).returncode == 2
    assert run(proj, "Write", {"file_path": str(proj / "pyproject.toml")}).returncode == 2


def test_message_names_fixer_tiers(proj):
    r = run(proj, "Edit", {"file_path": str(proj / "a.go")})
    for s in ("fixer-sonnet", "fixer-sonnet-high", "fixer-opus",
              "F-<Tn>-<n>", ".superlite/fixes/", "implementer-haiku", "fixing.md"):
        assert s in r.stderr
    incidents = (proj / ".superlite" / "incidents.jsonl").read_text().splitlines()
    assert json.loads(incidents[0])["kind"] == "rule-block"


def test_subagent_allowed(proj):
    r = run(proj, "Edit", {"file_path": str(proj / "src" / "app.py")}, agent_id="a1", agent_type="implementer")
    assert r.returncode == 0 and r.stderr == ""
    assert bash(proj, "sed -i s/a/b/ src/app.py").returncode == 2
    r = run(proj, "Bash", {"command": "sed -i s/a/b/ src/app.py"}, agent_id="a1")
    assert r.returncode == 0


def test_allowed_paths(proj):
    for p in ("docs/plans/x/plan.md", "SPEC.md", "SPEC-v2.md", "docs/guide.md", "docs/a/b/c.md",
              ".superlite/progress.md", ".superlite/fixes/F-T1-1.md", ".superlite/briefs/t1.md",
              str(proj / "docs" / "plans" / "p.md"), "/elsewhere/outside/file.py"):
        assert run(proj, "Write", {"file_path": p}).returncode == 0, p
    assert run(proj, "Write", {"file_path": "docs/code.py"}).returncode == 2


def test_inactive_run_allowed(tmp_path):
    assert run(tmp_path, "Edit", {"file_path": str(tmp_path / "a.py")}).returncode == 0
    assert bash(tmp_path, "sed -i s/a/b/ a.py").returncode == 0


def test_stable_json_marker_activates_before_wave_0(tmp_path):
    # lint --stable writes stable.json before Wave 0, so the guard is already on when the skeleton is written.
    (tmp_path / ".superlite").mkdir()
    (tmp_path / ".superlite" / "stable.json").write_text("{}")
    assert run(tmp_path, "Write", {"file_path": "pyproject.toml"}).returncode == 2


def test_prime_marker_activates(tmp_path):
    (tmp_path / ".superlite").mkdir()
    (tmp_path / ".superlite" / "prime.md").write_text("x")
    assert run(tmp_path, "Edit", {"file_path": "a.py"}).returncode == 2


@pytest.mark.parametrize("cmd", [
    "sed -i 's/a/b/' src/app.py",
    "sed -i.bak s/a/b/ src/app.py",
    "perl -pi -e 's/a/b/' src/app.py",
    "echo hi | tee src/app.py",
    "tee -a src/app.py < /dev/null",
    "echo hi > src/app.py",
    "echo hi >> src/app.py",
    "cat > src/app.py <<'EOF'\nprint(1)\nEOF",
    "cat <<EOF > src/app.py\nx\nEOF",
    "python3 -c \"open('src/a.py','w').write('x')\"",
    "node -e \"require('fs').writeFileSync('a.js','x')\"",
    "python - <<'EOF'\nfrom pathlib import Path\nPath('a.py').write_text('x')\nEOF",
    "git apply fix.patch",
    "patch -p1 < fix.patch",
    "mv src/a.py src/b.py",
    "cp /tmp/x.py src/a.py",
    "rm src/a.py",
    "ls && rm -f src/a.py",
])
def test_blocks_bash_writes(proj, cmd):
    assert bash(proj, cmd).returncode == 2, cmd


def test_blocks_powershell_write(proj):
    assert bash(proj, "'x' | Out-File a.txt > src/a.py", "PowerShell").returncode == 2


@pytest.mark.parametrize("cmd", [
    "cat src/app.py", "grep -rn foo src", "ls -la", "git status", "git log --oneline", "git diff",
    "git commit -m 'fix a > b'", "python3 -m pytest -q", "pytest tests 2>&1 | tail -5",
    "npm test 2>/dev/null", 'bash "$SL" dispatch implementer t1', 'bash "$SL" fix F-T1-1 > .superlite/out.txt',
    "echo hi > .superlite/progress.md", "cat > docs/plans/p.md <<'EOF'\nuse a -> b > c\nEOF",
    "sed -n 1,5p src/app.py", "sed -i s/a/b/ .superlite/progress.md", "echo hi > /dev/null",
    "echo hi > /tmp/x.txt", "cp src/a.py /tmp/a.py", "rm -f /tmp/x", "python3 -c \"print(open('a.py').read())\"",
    "echo \"a > b\"", "tee .superlite/log.txt",
])
def test_allows_bash_reads_and_safe_writes(proj, cmd):
    r = bash(proj, cmd)
    assert r.returncode == 0, cmd


def test_malformed_input_fails_open(proj):
    for raw in ("", "not json", "[]", json.dumps({"tool_name": "Bash"}),
                json.dumps({"tool_name": "Edit", "tool_input": {}}),
                json.dumps({"tool_name": "Bash", "tool_input": {"command": 5}})):
        r = run(proj, None, None, raw=raw)
        assert r.returncode == 0, raw
        assert r.stdout == "" and r.stderr == ""


def test_other_tools_ignored(proj):
    assert run(proj, "Read", {"file_path": str(proj / "a.py")}).returncode == 0


def test_hooks_json_registers_hook():
    data = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    e = data["hooks"]["PreToolUse"][1]
    assert e["matcher"] == "Edit|Write|NotebookEdit|Bash|PowerShell|Agent"
    assert e["hooks"][0]["command"] == 'bash "${CLAUDE_PLUGIN_ROOT}/hooks/block-coordinator-edits"'


def test_hook_has_lf_endings():
    assert b"\r" not in HOOK.read_bytes()


def test_batch_guard_denies_mismatched_implementer(proj):
    (proj / ".superlite" / "dispatch.json").write_text(
        json.dumps({"batches": [["T1", "T2"], ["T3"]], "lines": ["T1,T2 fresh", "T3 fresh"]}),
        encoding="utf-8",
    )
    r = run(proj, "Task", {"subagent_type": "implementer", "prompt": "Build T1,T2,T3 now"})
    assert r.returncode == 2
    assert "Printed batches" in r.stderr
    assert "T1 T2" in r.stderr or "T1,T2" in r.stderr.replace(",", " ")


def test_batch_guard_allows_exact_batch(proj):
    (proj / ".superlite" / "dispatch.json").write_text(
        json.dumps({"batches": [["T1", "T2"], ["T3"]]}),
        encoding="utf-8",
    )
    r = run(proj, "Task", {"subagent_type": "implementer", "prompt": "Your batch: T2 and T1 in order"})
    assert r.returncode == 0, r.stderr


def test_batch_guard_allows_prime_listing_all_tasks(proj):
    """Fresh spawn pastes the shared prime (lists every stable task) and waits for READY."""
    (proj / ".superlite" / "dispatch.json").write_text(
        json.dumps({"batches": [["T1", "T2"], ["T3"], ["T4", "T5"]]}),
        encoding="utf-8",
    )
    prime = (
        "# superlite prime (slim)\n"
        "### T1 Title\n### T2 Title\n### T3 Title\n### T4 Title\n### T5 Title\n"
        "Reply READY after reading this prime, then wait for your task.\n"
    )
    r = run(proj, "Task", {"subagent_type": "implementer", "prompt": prime})
    assert r.returncode == 0, r.stderr


def test_batch_guard_denies_empty_implementer(proj):
    (proj / ".superlite" / "dispatch.json").write_text(
        json.dumps({"batches": [["T1", "T2"], ["T3"]]}),
        encoding="utf-8",
    )
    r = run(proj, "Task", {"subagent_type": "implementer", "prompt": "Go implement whatever is ready"})
    assert r.returncode == 2
    assert "Printed batches" in r.stderr


def test_batch_guard_allows_t0_wave0_line(proj):
    (proj / ".superlite" / "dispatch.json").write_text(
        json.dumps({"batches": [["T0"], ["T1", "T2"]], "lines": ["T0 wave0", "T1,T2 fresh"]}),
        encoding="utf-8",
    )
    r = run(proj, "Task", {"subagent_type": "implementer-haiku", "prompt": "Wave 0 brief for T0 only"})
    assert r.returncode == 0, r.stderr


def test_question_guard_denies_fixer_while_waiting(proj):
    (proj / ".superlite" / "questions.json").write_text(
        json.dumps({"waiting": {"T2": {"agent": "a1"}}}),
        encoding="utf-8",
    )
    r = run(proj, "Task", {"subagent_type": "fixer-sonnet", "prompt": "Fix T2 which is blocked"})
    assert r.returncode == 2
    assert "answer the implementer" in r.stderr.lower()


def test_tier_guard_denies_low_fixer(proj):
    (proj / ".superlite" / "blocked.json").write_text(
        json.dumps({"blocked": {"T2": {"tier": "opus"}}}),
        encoding="utf-8",
    )
    r = run(proj, "Task", {"subagent_type": "fixer-sonnet", "prompt": "Fix BLOCKED T2"})
    assert r.returncode == 2
    assert "opus" in r.stderr.lower()


def test_typical_allowed_call_is_fast(proj):
    import time

    def best(argv, raw=""):
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(proj)}
        times = []
        for _ in range(3):
            t = time.perf_counter()
            subprocess.run(argv, input=raw, capture_output=True, text=True, env=env)
            times.append(time.perf_counter() - t)
        return min(times)

    base = best([BASH, "-c", "exit 0"])
    raw = json.dumps({"tool_name": "Bash", "tool_input": {"command": "git status && ls src"}})
    for hook_raw in (raw, json.dumps({"tool_name": "Edit", "tool_input": {"file_path": "x"}, "agent_id": "a"})):
        t = best([BASH, str(HOOK)], hook_raw)
        assert t < max(0.6, base + 0.35), (t, base)
