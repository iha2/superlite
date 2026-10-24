"""cost_live + cost-report: host/model usage only; silent before the host reports."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "subagent-driven-development" / "scripts"
HOOK = ROOT / "hooks" / "cost-report"
CAPTURE = ROOT / "hooks" / "capture-host-usage"
_GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"
BASH = _GIT_BASH if os.name == "nt" and os.path.exists(_GIT_BASH) else shutil.which("bash")

sys.path.insert(0, str(SCRIPTS))
import cost_live  # noqa: E402


def _sl(tmp_path, *, orchestrator=True):
    sl = tmp_path / ".superlite"
    sl.mkdir()
    if orchestrator:
        (sl / "orchestrator.json").write_text('{"mode":"ai","approved":true}\n', encoding="utf-8")
    return sl


def _host_usage(sl, **fields):
    base = {"ev": "host_usage", "t": 1, "host": "cursor", "model": "sonnet",
            "generation_id": "g1", "input": 1000, "output": 200, "cache_read": 500, "cache_write": 50}
    base.update(fields)
    with (sl / "metrics.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(base) + "\n")


def test_silent_without_session(tmp_path):
    (tmp_path / ".superlite").mkdir()
    assert cost_live.line(str(tmp_path / ".superlite")) is None


def test_silent_before_host_reports(tmp_path):
    sl = _sl(tmp_path)
    assert cost_live.line(str(sl)) is None


def test_line_from_host_tokens_with_rates(tmp_path):
    sl = _sl(tmp_path)
    _host_usage(sl)
    s = cost_live.summary(str(sl))
    line = cost_live.line(str(sl))
    assert s and s["source"] == "host+rates" and s["usd"] > 0
    assert line.startswith("superlite cost (host+rates, cursor): $")
    assert "tokens" in line and "1 turn" in line
    assert "\n" in line


def test_line_prefers_host_usd(tmp_path):
    sl = _sl(tmp_path)
    _host_usage(sl, usd=1.25)
    line = cost_live.line(str(sl))
    assert "superlite cost (host, cursor): $1.2500" in line.splitlines()[0]
    assert "host+rates" not in line.splitlines()[0]


def test_dedupes_generation_id(tmp_path):
    sl = _sl(tmp_path)
    _host_usage(sl, generation_id="g1", input=100, output=10, cache_read=0, cache_write=0, usd=0.1)
    _host_usage(sl, generation_id="g1", input=500, output=40, cache_read=0, cache_write=0, usd=0.4)
    s = cost_live.summary(str(sl))
    assert s["turns"] == 1 and s["input"] == 500 and s["usd"] == 0.4


def test_capture_writes_host_usage(tmp_path):
    sl = _sl(tmp_path)
    raw = json.dumps({
        "hook_event_name": "stop", "cwd": str(tmp_path), "status": "completed",
        "model": "claude-sonnet-4", "generation_id": "gen-9",
        "input_tokens": 1180, "output_tokens": 80, "cache_read_tokens": 900, "cache_write_tokens": 40,
    })
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path), "CURSOR_AGENT": "1"}
    r = subprocess.run([BASH, str(CAPTURE)], input=raw, capture_output=True, text=True, env=env)
    assert r.returncode == 0
    rows = [json.loads(ln) for ln in (sl / "metrics.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(rows) == 1 and rows[0]["ev"] == "host_usage"
    assert rows[0]["input"] == 1180 and rows[0]["output"] == 80
    assert rows[0]["cache_read"] == 900 and rows[0]["generation_id"] == "gen-9"


def test_capture_skips_when_no_token_fields(tmp_path):
    sl = _sl(tmp_path)
    raw = json.dumps({"hook_event_name": "stop", "cwd": str(tmp_path), "status": "completed"})
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path)}
    r = subprocess.run([BASH, str(CAPTURE)], input=raw, capture_output=True, text=True, env=env)
    assert r.returncode == 0
    assert not (sl / "metrics.jsonl").exists()


def test_opt_out_env_silent(tmp_path):
    sl = _sl(tmp_path)
    _host_usage(sl)
    env = {**os.environ, "SUPERLITE_NO_COST_REPORT": "1", "CLAUDE_PROJECT_DIR": str(tmp_path)}
    r = subprocess.run([BASH, str(HOOK)], input="{}", capture_output=True, text=True, env=env)
    assert r.returncode == 0
    assert "cost" not in r.stdout.lower()


def test_hook_silent_before_host(tmp_path):
    _sl(tmp_path)
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path), "CURSOR_AGENT": "1"}
    env.pop("SUPERLITE_NO_COST_REPORT", None)
    raw = json.dumps({"hook_event_name": "beforeSubmitPrompt", "prompt": "hi", "cwd": str(tmp_path)})
    r = subprocess.run([BASH, str(HOOK)], input=raw, capture_output=True, text=True, env=env)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out == {"continue": True}


def test_hook_injects_after_host(tmp_path):
    sl = _sl(tmp_path)
    _host_usage(sl, usd=0.55)
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path), "CURSOR_AGENT": "1"}
    env.pop("SUPERLITE_NO_COST_REPORT", None)
    raw = json.dumps({"hook_event_name": "beforeSubmitPrompt", "prompt": "hi", "cwd": str(tmp_path)})
    r = subprocess.run([BASH, str(HOOK)], input=raw, capture_output=True, text=True, env=env)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out.get("continue") is True
    ctx = out.get("additional_context") or ""
    assert "superlite cost (host, cursor): $0.5500" in ctx
    assert "\\n" in ctx or "\n" in ctx


def test_per_agent_cost_when_host_names_agents(tmp_path):
    """Small proof: subagentStop with token fields → agents: … in the live line."""
    sl = _sl(tmp_path)
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path), "CURSOR_AGENT": "1"}
    for payload in (
        {"hook_event_name": "subagentStop", "cwd": str(tmp_path), "subagent_id": "a-impl",
         "subagent_type": "implementer", "model": "sonnet", "generation_id": "g-impl",
         "input_tokens": 2000, "output_tokens": 400, "cache_read_tokens": 0, "cache_write_tokens": 0,
         "total_cost_usd": 0.12},
        {"hook_event_name": "subagentStop", "cwd": str(tmp_path), "subagent_id": "a-rev",
         "subagent_type": "package-reviewer", "model": "sonnet", "generation_id": "g-rev",
         "input_tokens": 1500, "output_tokens": 200, "cache_read_tokens": 0, "cache_write_tokens": 0,
         "total_cost_usd": 0.08},
    ):
        r = subprocess.run([BASH, str(CAPTURE)], input=json.dumps(payload),
                           capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
    s = cost_live.summary(str(sl))
    line = cost_live.line(str(sl))
    assert s and len(s["agents"]) == 2
    by = {a["agent"]: a["usd"] for a in s["agents"]}
    assert by["implementer"] == 0.12 and by["package-reviewer"] == 0.08
    assert s["usd"] == 0.2
    assert "  agents:" in line
    assert "    implementer $0.1200 (2400 tok)" in line
    assert "    package-reviewer $0.0800 (1700 tok)" in line
    # end-to-end: cost-report injects the multi-line agent breakdown
    raw = json.dumps({"hook_event_name": "beforeSubmitPrompt", "prompt": "status", "cwd": str(tmp_path)})
    env.pop("SUPERLITE_NO_COST_REPORT", None)
    out = json.loads(subprocess.run([BASH, str(HOOK)], input=raw, capture_output=True, text=True, env=env).stdout)
    ctx = out.get("additional_context") or ""
    assert "implementer $0.1200" in ctx and "package-reviewer $0.0800" in ctx
