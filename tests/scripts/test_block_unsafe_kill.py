import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "hooks" / "pre-tool"
# Git Bash on Windows (WSL's bash.exe in System32 would not see Windows paths), bash elsewhere.
_GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"
BASH = _GIT_BASH if os.name == "nt" and os.path.exists(_GIT_BASH) else shutil.which("bash")


PROJ = "/Users/me/work/app"


def run(payload, tool="Bash", raw=None, proj=None):
    if raw is None:
        raw = json.dumps({"tool_name": tool, "tool_input": {"command": payload}})
    env = {**os.environ, "CLAUDE_PROJECT_DIR": proj} if proj else None
    return subprocess.run(
        [BASH, str(HOOK)], input=raw, capture_output=True, text=True, env=env
    )


def test_blocks_taskkill_im():
    for cmd in ("taskkill /F /IM python.exe", "taskkill //F //IM python.exe", "TASKKILL /im node.exe"):
        r = run(cmd)
        assert r.returncode == 2, cmd
        assert "PID" in r.stderr


def test_blocks_pkill_killall_stop_process_name():
    for cmd, tool in (
        ("pkill -f server", "Bash"),
        ("killall node", "Bash"),
        ("Stop-Process -Name python -Force", "PowerShell"),
        ("stop-process -name python", "PowerShell"),
        ("kill -9 -1", "Bash"),
    ):
        assert run(cmd, tool).returncode == 2, cmd
    # the block message says how to find the PID
    r = run("pkill node")
    assert r.returncode == 2
    assert "pgrep -f" in r.stderr and "kill <PID>" in r.stderr


def test_allows_kill_by_pid():
    for cmd, tool in (
        ("kill 1234", "Bash"),
        ("kill -9 1234", "Bash"),
        ("taskkill /PID 1234", "Bash"),
        ("taskkill //F //PID 1234", "Bash"),
        ("Stop-Process -Id 12", "PowerShell"),
    ):
        r = run(cmd, tool)
        assert r.returncode == 0, cmd
        assert r.stdout == "" and r.stderr == ""


def test_allows_unrelated_commands():
    for cmd in ("ls -la", "python -m pytest", "echo skill"):
        assert run(cmd).returncode == 0, cmd
    # other tools are ignored
    assert run("pkill x", tool="Read").returncode == 0


def test_a_one_megabyte_command_is_allowed_and_a_kill_after_it_is_still_blocked():
    big = "echo " + "a" * 1_000_000
    assert run(big).returncode == 0
    assert run(big + "\npkill -f y").returncode == 2


def test_blocks_multiline_and_escaped_newlines():
    for cmd in ("echo x\npkill -f y", "echo x\r\npkill -f y", "echo x\n\tkillall node"):
        assert run(cmd).returncode == 2, repr(cmd)
    # a literal backslash-n typed by the model is treated the same way
    assert run("echo x\npkill -f y").returncode == 2


def test_blocks_get_process_pipe_taskkill_filter_kill_all():
    for cmd, tool in (
        ("Get-Process python | Stop-Process -Force", "PowerShell"),
        ("get-process -Name node|stop-process", "PowerShell"),
        ("taskkill /F /FI \"IMAGENAME eq python.exe\"", "Bash"),
        ("taskkill //FI \"STATUS eq running\"", "Bash"),
        ("kill -KILL -1", "Bash"),
        ("kill -s KILL -1", "Bash"),
    ):
        assert run(cmd, tool).returncode == 2, cmd


def test_allows_pid_forms_of_new_rules():
    for cmd, tool in (
        ("Get-Process -Id 12 | Stop-Process -Id 12", "PowerShell"),
        ("kill -KILL 1234", "Bash"),
        ("kill -s KILL 1234", "Bash"),
        ("kill -9 12345", "Bash"),
        ("echo x\nkill 1234", "Bash"),
    ):
        assert run(cmd, tool).returncode == 0, cmd


def test_allows_kill_word_in_read_only_text_commands():
    for cmd in (
        "grep -n pkill notes.md",
        'rg "killall|pkill" hooks/ | head -5',
        'echo "use pkill"',
        "man pkill",
        "pgrep -fl pkill",
        "which killall",
        "cd hooks && rg -n killall _kill",
        "LC_ALL=C grep -c pkill f",
        'FOO="a b" grep -n pkill f',
        "rg -n pkill hooks/ 2>&1 | head",
        'grep -E "x\\|pkill" f',
        'echo "a; pkill x"',
        "grep -n 'taskkill /IM' docs/",
        "cat notes.md | grep pkill | sort | uniq -c",
        'rg "foo(bar) pkill" .',
        'LC_ALL=C PAGER="less -R" grep -c pkill f',
    ):
        r = run(cmd)
        assert r.returncode == 0, cmd
        assert r.stdout == "" and r.stderr == ""
    for tool in ("Shell", "PowerShell"):
        assert run("echo pkill", tool).returncode == 0, tool


def test_blocks_kill_word_that_may_run():
    for cmd in (
        "echo hi; pkill node",
        "grep x f && killall y",
        "echo pkill node | bash",
        "echo pkill node | sh -s",
        "cat <<ZZ | sh\npkill node\nZZ",
        "cat <<ZZ\n$(pkill node)\nZZ",
        "cat <<ZZ\n`pkill node`\nZZ",
        "<<ZZ bash\npkill node\nZZ",
        '<<< "pkill node" bash',
        "echo $(pkill node)",
        "echo `pkill node`",
        "cat <(pkill node)",
        "$'pkill' node",
        '$"pkill" node',
        "x=pkill; $x node",
        "echo $'\\''; pkill node",
        "echo \\\"; pkill x; echo \\\"",
        "echo 'a \"b' ; pkill x ; echo 'c\" d'",
        "echo hi # it's\npkill x\n# it's",
        "time -p pkill x",
        "stdbuf -o0 pkill x",
        "busybox pkill x",
        'env -S "pkill x"',
        "sudo -nu root pkill x",
        "xargs pkill < pids",
        'python3 -c "import os; os.system(\'pkill x\')"',
        "/usr/bin/grep x; pkill y",
        "grep x | xargs pkill",
        "\\pkill node",
        '"pkill" node',
        "{ pkill node; }",
        "(grep x; pkill y)",
        "if grep x f; then pkill y; fi",
        "rg --pre pkill x f",
        'bat --pager "pkill x" f',
        "man -P pkill ls",
        "PAGER=pkill man ls",
        "sort --compress-program=pkill f",
        'less "+!pkill x" f',
        "echo pkill " + "a" * 5000,
        "ls *(e:'pkill x':)",
        "cat =(pkill x)",
        "grep x =(pkill node)",
        'LESSOPEN="|pkill -f %s" less x',
        "MANPAGER='pkill x' man ls",
        "BAT_PAGER=pkill bat f",
        "LESSOPEN+=pkill less x",
    ):
        assert run(cmd).returncode == 2, repr(cmd)
    for cmd in (
        "echo (Stop-Process -Name x)",
        "echo hi | sort { Stop-Process -Name x }",
        "echo x; Stop-Process -Name x",
        "echo 'Stop-Process -Name x' | Invoke-Expression",
        "Get-Process | Where-Object { ($_.CPU -gt 1) } | Stop-Process",
        "Stop-Process -Name:python",
    ):
        assert run(cmd, "PowerShell").returncode == 2, cmd


def test_allows_pkill_f_for_one_project_server_or_path():
    for cmd in (
        'pkill -f "vite preview --port 4317"',
        "pkill -f 'vite preview --port=4317'",
        'pkill -9 -f "wrangler dev --local --port 8799"',
        'pkill -f -TERM "node server.js localhost:8000"',
        'pkill -f "next start 127.0.0.1:3000"',
        'pkill -f "vite --port 1024 --host"',
        'pkill -f "vite --port=65535"',
        'pkill -SIGKILL -f "python3 -m http.server --port 18000"',
        f'pkill -f "{PROJ}/node_modules/.bin/vite"',
        f"pkill -f {PROJ}/scripts/dev.sh",
        f'pkill -15 -f "{PROJ}/.superlite/worktrees/t1"',
        'pkill -f "wrangler dev --local --port 8799"; echo stopped',
        'pkill -f "vite preview --port 4317" ; date; mise run check 2>&1 | tail -25',
        'pkill -f "vite preview --port 4317" && sleep 1',
        'pkill -f "vite preview --port 4317" || true',
        'pkill -f "vite preview --port 4317"\nsleep 1; echo ok',
    ):
        r = run(cmd, proj=PROJ)
        assert r.returncode == 0, cmd
        assert r.stdout == "" and r.stderr == ""


def test_blocks_pkill_f_that_could_match_other_sessions():
    for cmd in (
        "pkill node",
        "pkill -f vite",
        "pkill -x MyApp",
        "killall -9 node",
        'pkill -u me -f "vite --port 4317"',
        'pkill -f "ssh -p 22"',
        'pkill -f "curl localhost:80"',
        'pkill -f " -p 22"',
        'pkill -f "node --port 22"',
        'pkill -f "tcp:99"',
        'pkill -f "x --port 0443"',
        'pkill -f "x --port 4317123"',
        'pkill -f "--port 4317"',
        'pkill -f "vite --port 4317|node"',
        'pkill -f "vite.* --port 4317"',
        "pkill -f vite --port 4317",
        'pkill -f "vite --port 4317" 2>&1',
        'pkill -f "vite --port 4317"; pkill node',
        'pkill -f "vite preview --port 4317"; pkill node',
        'pkill -f "vite preview --port 4317"; killall x',
        'pkill -f "vite preview --port 4317" && pkill -f "wrangler dev --port 8799"',
        'pkill -f "vite preview --port 4317"; x=pkill; $x node',
        'pkill -f "vite preview --port 4317"; echo pkill y | bash',
        'pkill -f "vite preview --port 4317" | pkill z',
        'pkill -f "vite preview --port 4317" & sleep 1',
        'pkill -f "vite preview --port 4317"\npkill node',
        'pkill -f "vite preview --port 4317"; kill -9 -1',
        'pkill -f "vite preview --port 4317"; taskkill /F /IM x',
        'pkill -f "vite;preview --port 4317"; echo x',
        'pkill -f "vite preview" --port 4317; echo x',
        'pkill node; echo x',
        'pkill -f "ssh -L 2222:db:5432 host"',
        'pkill -f "sh -c :1000"',
        'pkill -f "node ........:4317"',
        'pkill -f "node ........ localhost:4317"',
        'pkill -f "node server.js:8000"',
        'pkill -f "curl localhost:4317 -v"',
        'pkill -f "vite --port 1000"',
        'pkill -f "vite --port 1023"',
        'pkill -f "vite --port 65536"',
        'pkill -f "vite --port 4317" ' + "; echo " + "a" * 5000,
        'pkill -f "vite preview --port 4317"; echo "use pkill"',
        'echo hi; pkill -f "vite preview --port 4317"',
        'pkill -f "vite --port 4317" extra',
        'pkill -f "./dev.sh"',
        'pkill -f "scripts/dev.sh"',
        'pkill -f "/tmp/........../x"',
        'pkill -f "/var/folders/ab/cdef/T/c"',
        'pkill -f "/tmp/claude-501/-Users-me-work-app/x/scratchpad"',
        f'pkill -f "{PROJ}"',
        f'pkill -f "{PROJ}/"',
        f'pkill -f "{PROJ}/../x"',
        f'pkill -f "{PROJ}/a/.."',
        f'pkill -f "{PROJ}/./x"',
        f'pkill -f "{PROJ}/........../x"',
        f'pkill -f "{PROJ}-evil/x"',
        f'pkill -f "{PROJ}/.superlite"',
        f'pkill -f "{PROJ}/.superlite/worktrees/"',
        f'pkill -f "{PROJ}/.claude/worktrees"',
        f'pkill -f "{PROJ}/.*"',
        f'pkill -f "{PROJ}/$HOME"',
        f"pkill -f '{PROJ}/x\"y'",
    ):
        assert run(cmd, proj=PROJ).returncode == 2, cmd
    # a project root with fewer than 3 segments is too broad to name a path under it
    assert run('pkill -f "/home/me/x"', proj="/home/me").returncode == 2
    assert run('pkill -f "/x"', proj="/").returncode == 2
