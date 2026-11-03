import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"))
import context_tokens as ct  # noqa: E402


def _asst(i, c, r, side=False):
    d = {"type": "assistant", "message": {"usage": {
        "input_tokens": i, "cache_read_input_tokens": r, "cache_creation_input_tokens": c}}}
    if side:
        d["isSidechain"] = True
    return json.dumps(d)


def _w(tmp_path, lines):
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(p)


def _boundary(pre=162000, trig="auto"):
    return json.dumps({"type": "system", "subtype": "compact_boundary",
                       "compactMetadata": {"trigger": trig, "preTokens": pre}})


def test_last_assistant_sums_input_and_cache(tmp_path):
    assert ct.current_context(_w(tmp_path, [_asst(1, 1, 1), _asst(10, 5000, 150000)])) == 155010


def test_no_assistant_is_zero(tmp_path):
    assert ct.current_context(_w(tmp_path, ['{"type": "user"}'])) == 0


def test_missing_file_is_zero():
    assert ct.current_context("/missing.jsonl") == 0


def test_sidechain_ignored(tmp_path):
    assert ct.current_context(_w(tmp_path, [_asst(10, 5000, 150000), _asst(1, 1, 1, side=True)])) == 155010


def test_bad_json_line_skipped(tmp_path):
    assert ct.current_context(_w(tmp_path, [_asst(1, 2, 3), "{not json", _asst(10, 5000, 150000)])) == 155010
    assert ct.current_context(_w(tmp_path, [_asst(1, 2, 3), "{not json"])) == 6
    assert ct.last_compact_boundary(_w(tmp_path, ["{bad", _boundary()]))["line"] == 2


def test_boundary_found(tmp_path):
    lines = ["{}"] * 6 + [_boundary()]
    assert ct.last_compact_boundary(_w(tmp_path, lines)) == {
        "line": 7, "pre_tokens": 162000, "trigger": "auto"}


def test_latest_boundary_wins(tmp_path):
    got = ct.last_compact_boundary(_w(tmp_path, [_boundary(1), "{}", _boundary(2)]))
    assert got["line"] == 3 and got["pre_tokens"] == 2


def test_no_boundary_is_none(tmp_path):
    assert ct.last_compact_boundary(_w(tmp_path, [_asst(1, 1, 1)])) is None
    assert ct.last_compact_boundary("/missing.jsonl") is None


def test_summary_line_without_boundary_gives_pre_tokens_zero(tmp_path):
    lines = ["{}", json.dumps({"type": "user", "isCompactSummary": True})]
    assert ct.last_compact_boundary(_w(tmp_path, lines)) == {"line": 2, "pre_tokens": 0, "trigger": ""}
