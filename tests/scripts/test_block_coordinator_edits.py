import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "hooks" / "pre-tool"
_GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"
BASH = _GIT_BASH if os.name == "nt" and os.path.exists(_GIT_BASH) else shutil.which("bash")
SCRATCH = "/tmp/claude-501/-Users-me-work-app/0000/scratchpad"


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
    "cp /tmp/x.py src/a.py 2>/dev/null",
    "echo \"multi\nline\" > src/a.py",
    "echo \"x\\\"y\" > src/a.py",
    # after a cd, a relative target is read from the folder it leads to
    "cd src && echo x > a.py",
    "cd src && rm a.py",
    "cd src; cp /tmp/x.py a.py",
    "cd {proj}/src && sed -i s/a/b/ a.py",
    "cd .superlite/sandboxes/x && echo x > a.py",
    "cd .superlite && mv ../src/a.py b.py",
    "cd .superlite && echo x > ../src/a.py",
    "cd docs && echo x > code.py",
    "cd {proj} && echo x > src/a.py",
    "cd /tmp && echo x > a.txt && cd {proj}/src && echo x > b.py",
    "cd $HOME && echo x > a.py",
    "cd ~ && echo x > a.py",
    "cd .. && echo x > a.py",
    "cd -P src && echo x > a.py",
    "cd a b && echo x > a.py",
    "cd /tmp && (cd src && rm a.py)",
    "cd /tmp && { cd src; rm a.py; }",
    "CDPATH=./src cd .superlite && echo x > a.json",
    "export CDPATH=./src; cd .superlite; echo x > a.json",
    # a cd the hook cannot follow leaves a relative target with none of the allow rules
    "cd skills/x && cd .. && echo x > SKILL.md",
    "cd skills/x && cd .. && echo x > README.md",
    "(cd skills/x && echo x > SKILL.md)",
    "{ cd skills/x; echo x > SKILL.md; }",
    "pushd skills/x && echo x > SKILL.md",
    "pushd skills/x >/dev/null; echo x > SKILL.md; popd",
    "cd -P skills/x && echo x > SKILL.md",
    "cd -- skills/x && echo x > SKILL.md",
    "cd .. && echo x > README.md",
    "cd -P . && echo x > README.md",
    "cd && echo x > README.md",
    "cd - && echo x > README.md",
    "cd $HOME/x && echo x > README.md",
    "cd . && cd .. && echo x > README.md",
    "cd .. && cd .superlite && echo x > README.md",
    "cd .. && echo x > .agents/n.txt",
    "cd .. && echo x > spikes/n.md",
    "cd .. && cp x README.md",
    "cd .. && tee README.md",
    "cd .. && sed -i s/a/b/ README.md",
    "cd .. && rm README.md",
    "cd .. && mv x README.md",
    "ls && cd skills/x && cd .. && echo x > SKILL.md",
    "cd skills/x && (cd .. ; echo x > SKILL.md)",
    # a . or .. component: the real file is not worked out
    "cd .superlite/. && echo x > orchestrator.json",
    "cd .superlite/. && echo x > feature.json",
    "cd .superlite/./ && echo x > human-replies.jsonl",
    "cd ./.superlite/. && echo x > orchestrator.json",
    "cd .superlite/./sandboxes && echo x > a.py",
    "cd .superlite/sandboxes/./x && echo x > a.py",
    "cd .superlite && echo x > ./sandboxes/./a.py",
    "echo x > .superlite/./orchestrator.json",
    "echo x > .superlite/../src/a.py",
    "echo x > .agents/../src/a.py",
    "echo x > spikes/../src/a.py",
    "cd .superlite && cp x ./../src/a.py",
    # cp -t DIR files: the folder is not the last word, so a root Markdown name does not vouch for it
    "cp -t src README.md",
    "cp -t skills/x SKILL.md",
    "cp -rt src README.md",
    "cp --target-directory=src README.md",
    "cp --target-directory src README.md",
    "cp -tsrc README.md",
    "cd .agents && cp -t ../src x",
    # a quoted path that is one plain word is still a path
    'rm "src/a.py"',
    "rm 'src/a.py'",
    'echo x > "src/a.py"',
    "echo x >> 'src/a.py'",
    "cp a 'src/b.py'",
    'cp "a.py" "src/b.py"',
    'mv "src/a.py" /tmp/x',
    'echo x | tee "src/a.py"',
    'sed -i "s/a/b/" "src/a.py"',
    'cat > "src/a.py" <<\'EOF\'\nx\nEOF',
    'echo x > src/"a.py"',
    'rm "src/my file.py"',
    "echo x > 'src/a b.py'",
    'cp a "src/my dir/b.py"',
    'mv "src/a b.py" /tmp/x',
    # a file called Q is a file: Q is no placeholder
    "rm Q",
    "echo x > Q",
    'echo "$S" > Q',
    'cd "$S" && echo x > out.txt',   # a cd it cannot read leaves the base at the project root
    "cd .agents; echo x > src/a.py",   # a cd into a missing folder fails, so the shell stays at the root
    "cd spikes/new; echo x > README.md",
])
def test_blocks_bash_writes(proj, cmd):
    cmd = cmd.replace("{proj}", str(proj))
    assert bash(proj, cmd).returncode == 2, cmd


def test_blocks_powershell_write(proj):
    assert bash(proj, "'x' | Out-File a.txt > src/a.py", "PowerShell").returncode == 2


@pytest.mark.parametrize("cmd", [
    "cat src/app.py", "grep -rn foo src", "ls -la", "git status", "git log --oneline", "git diff",
    "git commit -m 'fix a > b'", "python3 -m pytest -q", "pytest tests 2>&1 | tail -5",
    "npm test 2>/dev/null", 'bash "$CLI" dispatch implementer t1', 'bash "$CLI" fix F-T1-1 > .superlite/out.txt',
    "echo hi > .superlite/progress.md", "cat > docs/plans/p.md <<'EOF'\nuse a -> b > c\nEOF",
    "sed -n 1,5p src/app.py", "sed -i s/a/b/ .superlite/progress.md", "echo hi > /dev/null",
    "echo hi > /tmp/x.txt", "cp src/a.py /tmp/a.py", "rm -f /tmp/x", "python3 -c \"print(open('a.py').read())\"",
    "echo \"a > b\"", "tee .superlite/log.txt",
    # Redirects are not cp operands; escaped and multi-line quotes hide their '>'.
    "cp src/a.py /tmp/a.py 2>/dev/null", "grep -E \"href=\\\"[^>]*>\" x.html",
    "grep -c \"<li[^>]*>\nfoo\" x.html",
    # after a cd, a relative target is read from the folder it leads to
    "cd .superlite && mv stable.json prime.md pool.json archive/",
    "cd .superlite && mkdir -p archive-1 && mv progress.md incidents.jsonl archive-1/ && ls archive-1",
    "cd {proj}/.superlite && mv stable.json archive/",
    "cd {proj}/docs/plans && echo x > p.md",
    "cd docs && sed -i s/a/b/ guide.md",
    "cd docs/plans; rm -f old.md",
    "cd .superlite && cp pool.json pool.bak",
    "cd .superlite && echo x | tee notes.md",
    f"cd {SCRATCH} && echo x > out.txt",
    f"cd {SCRATCH} && rm -f a.py b.py",
    f"cd {SCRATCH} && python3 -c \"print(1)\" > msgs.txt; sed -n 1,3p msgs.txt",
    f"cd {SCRATCH} && cp /tmp/x.py a.py",
    "cd /tmp && cd {proj}/.superlite && mv stable.json archive/",
    "cd .superlite && cd archive && echo x > a.json",
    "cd docs && cd plans && echo x > p.md",
    "cd .superlite/ && mv stable.json archive/",
    "cd .. && echo x > docs/plans/p.md",
    "cd .. && echo x > .superlite/notes.md",
    # a quoted string that is not one plain word is an unknown path, as an unquoted $VAR is
    'rm -rf "$S/home"',
    'mv "$S/a" "$S/b"',
    'cp ~/x.jpg "$S/photo.jpg"',
    'mise run build > "$S/install.log" 2>&1; echo "exit $?"',
    f'S={SCRATCH}; rm -rf "$S/home" "$S/photos"; ls -A "$S" | wc -l',
    'echo x > "$(pwd)/a.txt"',
    'echo x > "/tmp/a b/x"',
    'rm "$S/my dir"',
    'echo "hello world" > /tmp/x.txt',
    'git commit -m "fix a b" && rm "/tmp/my file.txt"',
    'rm "/tmp/x.txt"',
    "cp 'a' '/tmp/b.py'",
    # an operator or a space inside quotes stays inside one word
    'echo x > "docs/plans/$f.md"',
    'echo x > ".superlite/$f.json"',
    'echo "a > b" > /tmp/o.txt',
    'echo "a; rm src/a.py" > /tmp/o.txt',
    'git commit -m "fix src/a.py > b" && ls',
    'cp "/tmp/a b.txt" /tmp/c.txt',
    'echo "x" > /tmp/""o.txt',
    # cp -t outside the project
    "cp -t /tmp README.md", "cp -rt /tmp x.md", "cp --target-directory=/tmp README.md",
])
def test_allows_bash_reads_and_safe_writes(proj, cmd, tmp_path_factory):
    # A cd is followed only into a folder that exists, so make the ones the cases use.
    for d in ("docs/plans", ".superlite/archive", ".agents", "spikes"):
        (proj / d).mkdir(parents=True, exist_ok=True)
    scratch = tmp_path_factory.mktemp("scratchpad")
    cmd = cmd.replace("{proj}", str(proj)).replace(SCRATCH, str(scratch))
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
    (e,) = data["hooks"]["PreToolUse"]
    assert e["matcher"] == "Edit|Write|NotebookEdit|Bash|PowerShell|Agent"
    assert [h["command"] for h in e["hooks"]] == ['bash "${CLAUDE_PLUGIN_ROOT}/hooks/pre-tool"']


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


def test_gate_blocks_new_name(tmp_path):
    (tmp_path / ".superlite").mkdir()
    (tmp_path / ".superlite" / "orchestrator.json").write_text('{"mode": "human", "approved": false}')
    for cmd in ("bash x/superlite-cli dispatch --plan p", 'bash "$CLI" dispatch --plan p',
                "bash ${CLI} assign t1", "bash skills/swarm/scripts/superlite-cli wave0"):
        assert bash(tmp_path, cmd).returncode == 2, cmd
    assert bash(tmp_path, "bash x/superlite-cli plan_tools lint --plan p").returncode == 0


def test_gate_blocks_every_launcher_form(tmp_path):
    (tmp_path / ".superlite").mkdir()
    (tmp_path / ".superlite" / "orchestrator.json").write_text('{"mode": "human", "approved": false}')
    for tool in ("dispatch", "assign", "wave0", "checkpoint", "finish", "build_prime"):
        for form in ("superlite-cli %s", 'bash "/a b/skills/swarm/scripts/superlite-cli" %s', '"$CLI" %s',
                     "${CLI} %s", 'bash "$CLI" %s', 'bash "${CLI}" %s',
                     "cd d && bash ./skills/swarm/scripts/superlite-cli %s", "FOO=1 bash ./x/superlite-cli %s"):
            cmd = form % tool
            assert bash(tmp_path, cmd).returncode == 2, cmd
    for cmd in ("superlite-cli orchestrator status", "superlite-cli task T1", 'bash "$CLI" task T1'):
        assert bash(tmp_path, cmd).returncode == 0, cmd


def test_one_process_per_bash_call(proj):
    """pre-tool runs the unsafe-kill, coordinator and test-run guards in that order; the first denial wins."""
    data = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    assert sum("Bash" in e["matcher"].split("|") for e in data) == 1
    r = bash(proj, "pytest tests && " + "pk" + "ill -f node")
    assert r.returncode == 2 and "by name or pattern" in r.stderr and r.stdout == ""
    r = bash(proj, "echo x > src/app.py && pytest tests")
    assert r.returncode == 2 and "must not write project code" in r.stderr
    r = bash(proj, "pytest")
    out = json.loads(r.stdout)
    assert r.returncode == 0
    assert "run_tests --full" in out["hookSpecificOutput"]["updatedInput"]["command"]


def test_allows_notes_and_root_markdown(proj):
    for p in ("spikes/COMPARISON.md", "spikes/a/notes.md", ".agents/handoffs/x.md", ".agents/PROGRESS-app.md",
              ".agents/a/b/c.txt", "README.md", "CHANGELOG.md", str(proj / "spikes" / "COMPARISON.md")):
        for tool in ("Edit", "Write"):
            assert run(proj, tool, {"file_path": p}).returncode == 0, (tool, p)


def test_blocks_code_beside_the_notes_folders(proj):
    # Markdown passes at the root and in spikes/ only: SKILL.md and agents/*.md are product code.
    for p in ("spikes/run-all.sh", "spikes/a/run.py", "src/README.md", "skills/x/SKILL.md", "agents/x.md",
              "handoffs/x.md", "app/pyproject.toml", "typos.toml", "Makefile", "docs/code.py",
              ".superlite/sandboxes/x/notes.md", str(proj / "src" / "README.md")):
        assert run(proj, "Write", {"file_path": p}).returncode == 2, p


@pytest.mark.parametrize("path", [
    ".superlite/./orchestrator.json", ".superlite/sandboxes/../orchestrator.json", ".superlite/x/../feature.json",
    ".superlite/./sandboxes/a.py", ".superlite/../src/a.py", ".agents/../src/a.py", "spikes/../src/a.md",
    "spikes/../a.md", "docs/plans/../../src/a.py", "docs/../src/a.md", ".superlite/./notes.md", "docs/./a.md",
    "././src/a.py",
])
def test_blocks_a_path_with_a_dot_component(proj, path):
    for tool in ("Edit", "Write"):
        assert run(proj, tool, {"file_path": path}).returncode == 2, (tool, path)


def test_dot_component_matters_only_in_a_run_and_for_protected_files(proj):
    (proj / ".superlite" / "pool.json").unlink()
    assert run(proj, "Write", {"file_path": "src/./a.py"}).returncode == 0
    assert run(proj, "Write", {"file_path": "docs/../a.md"}).returncode == 0
    assert bash(proj, "echo x > src/./a.py").returncode == 0
    for path in (".superlite/./orchestrator.json", ".superlite/x/../human-replies.jsonl"):
        r = run(proj, "Write", {"file_path": path})
        assert r.returncode == 2 and "never written directly" in r.stderr, path


@pytest.mark.parametrize("name", ["orchestrator.json", "feature.json", "human-replies.jsonl"])
def test_protected_files_stay_blocked(proj, name):
    assert run(proj, "Write", {"file_path": f".superlite/{name}"}).returncode == 2
    r = bash(proj, f"python3 - <<'EOF'\np = '.superlite/{name}'\nopen(p, 'w').write('x')\nEOF")
    assert r.returncode == 2 and "never written directly" in r.stderr
    r = bash(proj, f"cd .superlite && echo x > {name}")
    assert r.returncode == 2 and "never written directly" in r.stderr


@pytest.mark.parametrize("name", ["orchestrator.json", "feature.json", "human-replies.jsonl"])
@pytest.mark.parametrize("fmt", [
    'echo x > "$PWD/.superlite/{}"',
    "echo x > $PWD/.superlite/{}",
    'cp x "${{P}}/.superlite/{}"',
    'echo x > "$P"/.superlite/{}',
    'echo x > "$P/.superlite"/{}',
    'echo x > "$P"/.superlite/"{}"',
    'echo x > "$P"/"$Q"/.superlite/{}',
    'echo x >> "$P"/.superlite/{}',
    'echo x | tee "$P"/.superlite/{}',
    'cp x "$P"/.superlite/{}',
    'cp x "$P/.superlite"/{}',
    'echo x > "$P"/.SuperLite/{}',
    'cd .superlite && echo x > "$P"/.superlite/{}',
])
def test_blocks_a_protected_file_under_an_unknown_folder(proj, name, fmt):
    r = bash(proj, fmt.format(name))
    assert r.returncode == 2 and "never written directly" in r.stderr, fmt


def test_blocks_a_protected_file_under_an_unknown_folder_without_a_run(proj):
    (proj / ".superlite" / "pool.json").unlink()
    for name in ("orchestrator.json", "feature.json", "human-replies.jsonl"):
        for cmd in (f'echo x > "$P"/.superlite/{name}', f"echo x > $P/.superlite/{name}"):
            r = bash(proj, cmd)
            assert r.returncode == 2 and "never written directly" in r.stderr, cmd


@pytest.mark.parametrize("cmd", [
    # an empty pair of quotes, or a quoted piece inside a word, does not hide the word
    'echo x > sr""c/a.py',
    'echo x > src/a"".py',
    "echo x > src/''a.py",
    'rm src/a"".py',
    "rm sr''c/a.py",
    'cp x src/a""',
    'tee src/a"".py',
    'mv x src/a"".py',
    'sed -i s/a/b/ src/a"".py',
    'cd .superlite && cat > orch""estrator.json',
    'cd .superlite && echo x > feature.json""',
    'echo x > .superlite/human-repl""ies.jsonl',
    # a quoted piece with a $ keeps the text before it: src/ is still project code
    'echo x > "src/$f.py"',
    'cp x "src/$f.py"',
    'rm -rf "src/$d"',
    'sed -i "s/$x/y/" "src/$f"',
    'echo x > src/"$f"',
    'echo x > src/"$f".py',
    'rm src/"$f"',
    'rm -rf src/"$d"',
    'cp x src/"$f"',
    'mv x src/"a$f"',
    'tee src/"$f"',
    "echo x > src/'$f'*",
    'sed -i s/a/b/ src/"$f"',
    'echo x > "src"/"$f"',
])
def test_blocks_a_word_broken_up_by_quotes(proj, cmd):
    r = bash(proj, cmd)
    assert r.returncode == 2, cmd
    protected = any(s in cmd for s in ("estrator", "feature.json", "ies.jsonl"))
    assert ("never written directly" in r.stderr) == protected, cmd


def test_incident_line_stays_valid_json(proj):
    for cmd in ('echo x > "src/$f.py"', 'echo x > src/"$f"', 'rm -rf "src/my dir/$f"', 'echo x > "$P"/.superlite/orchestrator.json'):
        assert bash(proj, cmd).returncode == 2, cmd
    lines = (proj / ".superlite" / "incidents.jsonl").read_text().splitlines()
    assert len(lines) == 4
    rows = [json.loads(line) for line in lines]
    assert rows[0]["target"] == "> src/$"
    assert rows[2]["target"] == "rm src/my dir/$"


def test_many_quoted_variables_are_decided_quickly(proj):
    import time

    cmd = "mkdir -p " + " ".join(f'"$S/f{i}"' for i in range(1500)) + " && echo done"
    t = time.perf_counter()
    assert bash(proj, cmd).returncode == 0
    assert time.perf_counter() - t < 3
