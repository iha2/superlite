import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "hooks" / "prompt"
_GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"
BASH = _GIT_BASH if os.name == "nt" and os.path.exists(_GIT_BASH) else shutil.which("bash")


def _asst(n, sidechain=False):
    return json.dumps({"type": "assistant", "isSidechain": sidechain, "message": {
        "role": "assistant", "usage": {"input_tokens": 10, "cache_read_input_tokens": n - 10,
                                       "cache_creation_input_tokens": 0}}}) + "\n"


def _transcript(tmp_path, n):
    t = tmp_path / "t.jsonl"
    t.write_text(_asst(n), encoding="utf-8")
    return t


def _prompt(tmp_path, transcript=None, cursor=False, env_extra=None, sid="s1"):
    ev = "beforeSubmitPrompt" if cursor else "UserPromptSubmit"
    payload = {"hook_event_name": ev, "prompt": "hi", "session_id": sid}
    if transcript is not None:
        payload["transcript_path"] = str(transcript)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CURSOR_", "SUPERLITE_"))}
    env.update({"CLAUDE_PROJECT_DIR": str(tmp_path), "SUPERLITE_NO_SESSION_HUD": "1",
                "SUPERLITE_NO_COST_REPORT": "1", "CLAUDE_CODE": "1"})
    env.update(env_extra or {})
    r = subprocess.run([BASH, str(HOOK)], input=json.dumps(payload), capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    return r.stdout


def _sl(tmp_path, stale=False):
    sl = tmp_path / ".superlite"
    sl.mkdir()
    if stale:
        (sl / "metrics.jsonl").write_text(json.dumps({"ev": "launch", "t": 0, "agent_id": "a"}) + "\n")
        m = sl / "last-compact"
        m.write_text("")
        os.utime(m, (0, 0))
    return sl


def _ctx(out):
    return json.loads(out)["hookSpecificOutput"]["additionalContext"]


def test_nudges_over_limit(tmp_path):
    _sl(tmp_path)
    ctx = _ctx(_prompt(tmp_path, _transcript(tmp_path, 160000)))
    assert "160k" in ctx and "/compact" in ctx


def test_silent_under_limit_with_fresh_last_compact(tmp_path):
    sl = _sl(tmp_path)
    (sl / "last-compact").write_text("")
    assert _prompt(tmp_path, _transcript(tmp_path, 90000)) == ""


def test_renudges_with_count(tmp_path):
    _sl(tmp_path)
    t = _transcript(tmp_path, 160000)
    _prompt(tmp_path, t)
    assert "request 2" in _ctx(_prompt(tmp_path, t))


def test_confirms_compact(tmp_path):
    sl = _sl(tmp_path)
    t = _transcript(tmp_path, 160000)
    _prompt(tmp_path, t)
    with open(t, "a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "system", "subtype": "compact_boundary",
                            "compactMetadata": {"trigger": "manual", "preTokens": 160000}}) + "\n")
        f.write(_asst(2000))
    assert _prompt(tmp_path, t) == ""
    assert "compact_done" in (sl / "compact.jsonl").read_text(encoding="utf-8")


def test_confirms_compact_after_boundary_and_summary_only_waits(tmp_path):
    sl = _sl(tmp_path)
    t = _transcript(tmp_path, 160000)
    _prompt(tmp_path, t)
    with open(t, "a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "system", "subtype": "compact_boundary",
                            "compactMetadata": {"trigger": "manual", "preTokens": 160000}}) + "\n")
        f.write(json.dumps({"type": "user", "isCompactSummary": True}) + "\n")
    assert _prompt(tmp_path, t) == ""  # no stale count, no new request
    assert "compact_done" not in (sl / "compact.jsonl").read_text(encoding="utf-8")
    with open(t, "a", encoding="utf-8") as f:
        f.write(_asst(2000))
    assert _prompt(tmp_path, t) == ""
    done = [json.loads(x) for x in (sl / "compact.jsonl").read_text(encoding="utf-8").splitlines()
            if "compact_done" in x]
    assert len(done) == 1 and done[0]["after"] == 2000


def test_user_only_transcript_stays_silent_not_timer(tmp_path):
    _sl(tmp_path, stale=True)
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n", encoding="utf-8")
    assert _prompt(tmp_path, t) == ""


def test_unreadable_transcript_path_falls_back_to_timer(tmp_path):
    _sl(tmp_path, stale=True)
    assert "compact" in _ctx(_prompt(tmp_path, tmp_path / "nope.jsonl")).lower()


def test_transcript_path_with_escapes_is_decoded(tmp_path):
    _sl(tmp_path)
    d = tmp_path / ('weird é dir' if os.name == "nt" else 'we"ird\\é dir')
    d.mkdir()
    t = _transcript(d, 160000)
    assert "160k" in _ctx(_prompt(tmp_path, t))


def test_sessions_sharing_superlite_keep_separate_counts(tmp_path):
    _sl(tmp_path)
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    a.write_text(_asst(160000), encoding="utf-8")
    b.write_text(_asst(160000), encoding="utf-8")
    _prompt(tmp_path, a)
    assert "request 2" in _ctx(_prompt(tmp_path, a))
    assert "request" not in _ctx(_prompt(tmp_path, b))


def test_missing_transcript_falls_back_to_timer(tmp_path):
    _sl(tmp_path, stale=True)
    assert "compact" in _ctx(_prompt(tmp_path)).lower()


def test_opt_out_env(tmp_path):
    _sl(tmp_path)
    out = _prompt(tmp_path, _transcript(tmp_path, 160000), env_extra={"SUPERLITE_NO_AUTOCOMPACT": "1"})
    assert "compact" not in out.lower()


def test_cursor_names_summarize(tmp_path):
    _sl(tmp_path)
    d = json.loads(_prompt(tmp_path, _transcript(tmp_path, 160000), cursor=True))
    assert d["continue"] is True and "/summarize" in d["additional_context"]


def test_existing_prompt_hook_tests_still_pass():
    r = subprocess.run([os.sys.executable, "-m", "pytest", "-q", str(ROOT / "tests/scripts/test_compact_due.py")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
