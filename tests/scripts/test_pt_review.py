"""Tests for scripts/pt_review.py: adapter, packet, contract."""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent.parent / "skills" / "swarm" / "scripts" / "pt_review.py"
FIXTURE = HERE / "fixtures" / "review_basic"

_spec = importlib.util.spec_from_file_location("pt_review", SCRIPT)
pt_review = importlib.util.module_from_spec(_spec)
sys.modules["pt_review"] = pt_review
_spec.loader.exec_module(pt_review)

CONSUMER_TEST = textwrap.dedent('''\
    import os


    def test_runs_against_real_producer():
        assert os.environ.get("SUPERLITE_REAL") == "1"
''')


@pytest.fixture()
def proj(tmp_path: Path) -> Path:
    dest = tmp_path / "proj"
    shutil.copytree(FIXTURE, dest)
    return dest


def run(*argv: str) -> int:
    return pt_review.main(list(argv))


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{old!r} not in {path}"
    path.write_text(text.replace(old, new), encoding="utf-8")


def packet(proj: Path, log: Path | None = None, **extra: str) -> tuple[int, str]:
    out = proj / "packet.md"
    argv = ["packet", "--plan", str(proj / "plan.md"), "--root", str(proj),
            "--log", str(log or proj / "incidents.jsonl"), "--out", str(out)]
    for k, v in extra.items():
        argv += [f"--{k.replace('_', '-')}", v]
    rc = run(*argv)
    return rc, out.read_text(encoding="utf-8")


def write_log(path: Path, entries: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


def verified(file: str, context: str) -> dict:
    return {"file": file, "line": 0, "context": context, "agent": "probe_run", "kind": "verified"}


# ----------------------------------------------------------------------------- adapter


def make_pkg(root: Path) -> None:
    pkg = root / "src" / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "mod.py").write_text("def fn(text):\n    return text.upper()\n", encoding="utf-8")
    (pkg / "__main__.py").write_text(
        "import sys\nprint('hello', ' '.join(sys.argv[1:]), sys.stdin.read().strip())\n", encoding="utf-8")


def test_adapter_from_probe_entry(tmp_path: Path):
    make_pkg(tmp_path)
    plan = tmp_path / "plan.md"
    plan.write_text("**Probe entry:** call=pkg.mod.fn · run=python -m pkg\n", encoding="utf-8")
    out = tmp_path / ".superlite" / "probes" / "adapter.py"
    assert run("adapter", "--plan", str(plan), "--root", str(tmp_path), "--out", str(out)) == 0
    src = out.read_text(encoding="utf-8")
    assert "pkg.mod.fn" in src and "python -m pkg" in src
    spec = importlib.util.spec_from_file_location("probe_adapter", out)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    assert adapter.call("abc") == "ABC"
    rc, so, _err = adapter.run(["x", "y"], stdin="in")
    assert rc == 0 and so.strip() == "hello x y in"
    assert not hasattr(adapter, "request")  # no serve= entry, no server kind


def test_adapter_serve_request(proj: Path):
    out = proj / "adapter.py"
    assert run("adapter", "--plan", str(proj / "plan.md"), "--root", str(proj), "--out", str(out)) == 0
    spec = importlib.util.spec_from_file_location("probe_adapter_srv", out)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    try:
        assert adapter.request("POST", "/api/cells", {"ref": "A1", "raw": "x"}) == (200, {"cells": {"A1": "x"}})
        status, body = adapter.request("GET", "/nope")
        assert status == 404 and body == {"error": "not found"}
    finally:
        adapter.close()
        for name in [n for n in sys.modules if n.split(".")[0] in ("minisheet",)]:
            del sys.modules[name]
        sys.path[:] = [p for p in sys.path if not p.startswith(str(proj))]


def test_adapter_without_entries_exits_2(tmp_path: Path):
    plan = tmp_path / "plan.md"
    plan.write_text("# nothing here\n", encoding="utf-8")
    assert run("adapter", "--plan", str(plan), "--out", str(tmp_path / "a.py")) == 2
    assert not (tmp_path / "a.py").exists()


# ----------------------------------------------------------------------------- packet


def test_packet_lists_open_and_uncovered(proj: Path):
    rc, text = packet(proj)
    assert rc == 0
    assert "REVIEW REQUIRED" in text and "FAST PATH" not in text.splitlines()[0]
    # the open incident, with a +-12 line excerpt around workbook.py:8
    assert "src/minisheet/workbook.py:8" in text
    assert "def set(self, ref: str, raw: str)" in text
    # minisheet has a verified `state` entry but no `deep`; sitegen has no `encoding`
    assert "package minisheet: no `verified` entry for category `deep`" in text
    assert "package minisheet: no `verified` entry for category `state`" not in text
    assert "package sitegen: no `verified` entry for category `encoding`" in text
    # the fixed, high-confidence incident is not open
    assert "off-by-one" not in text


def test_packet_repeated_location_ranks_first(proj: Path):
    log = proj / "log.jsonl"
    write_log(log, [
        {"file": "src/minisheet/workbook.py", "line": 8, "context": "unresolved guess", "kind": "assumption"},
        {"file": "src/minisheet/evaluator.py", "line": 8, "context": "first", "kind": "exception",
         "resolution": "fixed", "confidence": "high"},
        {"file": "src/minisheet/evaluator.py", "line": 8, "context": "second", "kind": "exception",
         "resolution": "fixed", "confidence": "high"},
    ])
    _rc, text = packet(proj, log)
    assert text.index("src/minisheet/evaluator.py:8") < text.index("src/minisheet/workbook.py:8")
    assert "repeated" in text


def test_packet_contract_mismatch(proj: Path):
    edit(proj / "src/minisheet/evaluator.py", "def evaluate(formula: str, get_cell)", "def evaluate(formula: str)")
    _rc, text = packet(proj)
    assert "missing parameter 'get_cell'" in text
    assert "src/minisheet/evaluator.py:" in text
    assert "1 mismatch(es)" in text
    # excerpt of the real definition
    assert "def evaluate(formula: str)" in text


def test_packet_probe_failures_and_tests(proj: Path):
    probes = proj / "probes"
    probes.mkdir()
    (probes / "probe-results.json").write_text(json.dumps({"results": [
        {"category": "deep", "file": "src/minisheet/evaluator.py", "line": 8, "status": "crash",
         "detail": "RecursionError at depth 2000"},
        {"category": "numeric", "file": "src/minisheet/evaluator.py", "line": 8, "status": "pass"},
    ]}), encoding="utf-8")
    tests = proj / "tests.json"
    tests.write_text(json.dumps({"passed": 9, "failed": 1, "skipped": 1, "failures": ["tests/test_x.py::test_a"],
                                 "skipped_tests": ["tests/test_x.py::test_slow"]}), encoding="utf-8")
    _rc, text = packet(proj, probes=str(probes), tests=str(tests))
    assert "probe deep crash" in text and "RecursionError at depth 2000" in text
    assert "probe numeric" not in text
    assert "passed 9" in text and "FAILING: tests/test_x.py::test_a" in text
    assert "SKIPPED: tests/test_x.py::test_slow" in text


def test_packet_lists_cross_package_seams(proj: Path):
    _rc, text = packet(proj)
    assert "src/sitegen/render.py:7  sitegen -> minisheet: minisheet.evaluator.evaluate()" in text


def test_packet_size_cap(proj: Path):
    log = proj / "big.jsonl"
    entries = [{"file": "src/minisheet/evaluator.py", "line": 8, "context": "dup " + "x" * 150,
                "kind": "exception", "agent": "A"}] * 2  # repeated -> top rank
    for n in range(500):
        entries.append({"file": "src/minisheet/workbook.py", "line": 1 + n, "context": f"issue {n:03d} " + "y" * 150,
                        "kind": "perf", "agent": "A"})
    write_log(log, entries)
    rc, text = packet(proj, log)
    assert rc == 0
    assert len(text) <= int(20000 * 1.9)
    assert "src/minisheet/evaluator.py:8" in text  # top-ranked kept
    assert "issue 499" not in text  # lowest-ranked dropped
    assert "lower-ranked item(s) omitted" in text


def test_packet_max_tokens_flag(proj: Path):
    log = proj / "big.jsonl"
    write_log(log, [{"file": "src/minisheet/workbook.py", "line": 1 + n, "context": f"issue {n}", "kind": "perf"}
                    for n in range(200)])
    _rc, text = packet(proj, log, max_tokens="3000")
    assert len(text) <= int(3000 * 1.9)


def test_packet_empty_is_fast_path(proj: Path):
    log = proj / "clean.jsonl"
    write_log(log, [verified("src/minisheet/evaluator.py", "probe deep: nesting returns #VALUE!"),
                    verified("src/minisheet/evaluator.py", "probe state: consistent after error"),
                    verified("src/sitegen/render.py", "probe encoding: BOM and surrogates ok")])
    rc, text = packet(proj, log)
    assert rc == 0
    assert text.splitlines()[0] == "# Review packet: FAST PATH"
    assert "APPROVED" in text
    assert "REVIEW REQUIRED" not in text


def test_packet_missing_log_is_not_an_error(proj: Path):
    rc, text = packet(proj, proj / "does-not-exist.jsonl")
    assert rc == 0 and "Review packet" in text


# ----------------------------------------------------------------------------- contract


def contract(proj: Path, *extra: str) -> int:
    return run("contract", "--plan", str(proj / "plan.md"), "--root", str(proj), *extra)


def test_contract_seam_ok_and_mismatch(proj: Path, capsys):
    (proj / "tests").mkdir()
    (proj / "tests" / "test_ui.py").write_text(CONSUMER_TEST, encoding="utf-8")
    assert contract(proj, "--seam", "T1,T2") == 0
    assert "PASS: verified: seam T1↔T2" in capsys.readouterr().out
    edit(proj / "src/minisheet/workbook.py", "def set(self, ref: str, raw: str)", "def set(self, ref: str)")
    assert contract(proj, "--seam", "T1,T2") == 1  # T1 is the producer; T2 the consumer
    out = capsys.readouterr().out
    assert "missing parameter 'raw'" in out
    assert "src/minisheet/workbook.py:" in out


def test_contract_without_seam_checks_all_produces(proj: Path, capsys):
    assert contract(proj) == 0
    assert "4 Produces signature(s) checked" in capsys.readouterr().out
    edit(proj / "src/minisheet/server.py", "port: int = 0", "port: int = 8080")
    assert contract(proj) == 1
    assert "default is 8080, planned 0" in capsys.readouterr().out


def test_contract_param_order_and_return(proj: Path, capsys):
    edit(proj / "src/minisheet/workbook.py", "def set(self, ref: str, raw: str) -> list[str]",
         "def set(self, raw: str, ref: str) -> set[str]")
    assert contract(proj) == 1
    out = capsys.readouterr().out
    assert "parameter order differs" in out


def test_contract_missing_method(proj: Path, capsys):
    edit(proj / "src/minisheet/workbook.py", "def get(self, ref: str)", "def fetch(self, ref: str)")
    assert contract(proj) == 1
    assert "Workbook.get not found" in capsys.readouterr().out


def test_contract_data_contract_shape_mismatch(proj: Path, capsys):
    edit(proj / "src/minisheet/server.py", '{"cells":', '{"cell":')
    assert contract(proj, "--seam", "T2,T3") == 1
    out = capsys.readouterr().out
    assert "part2" in out and "lacks required key 'cells'" in out
    assert "src/minisheet/server.py:" in out


def test_contract_data_contract_wrong_status(proj: Path, capsys):
    edit(proj / "src/minisheet/server.py", 'self._send(404, {"error": "not found"})\n\n        def do_GET',
         'self._send(400, {"error": "not found"})\n\n        def do_GET')
    edit(proj / "plan.md", "POST /api/cells {", "POST /nowhere {")
    assert contract(proj, "--seam", "T2,T3") == 1
    out = capsys.readouterr().out
    assert "POST /nowhere" in out and "returned status 400, contract says 200" in out


def test_contract_consumer_call_not_in_contract(proj: Path, capsys):
    edit(proj / "src/minisheet/static/index.html", "fetch('/api/cells', {method: 'POST'",
         "fetch('/api/cellz', {method: 'POST'")
    assert contract(proj, "--seam", "T2,T3") == 1
    out = capsys.readouterr().out
    assert "src/minisheet/static/index.html:4" in out
    assert "POST /api/cellz" in out and "not in the data contract" in out


def test_contract_template_literal_call_is_in_contract(proj: Path, capsys):
    assert contract(proj, "--seam", "T2,T3") == 0
    out = capsys.readouterr().out
    assert "not in the data contract" not in out


def test_contract_consumer_tests_run_with_superlite_real(proj: Path, capsys):
    (proj / "tests").mkdir()
    (proj / "tests" / "test_ui.py").write_text(CONSUMER_TEST, encoding="utf-8")
    assert contract(proj, "--seam", "T2,T3") == 0  # passes only if SUPERLITE_REAL=1 reached the tests
    (proj / "tests" / "test_ui.py").write_text(
        "def test_fake_producer_only():\n    raise AssertionError('real producer broke the consumer')\n",
        encoding="utf-8")
    assert contract(proj, "--seam", "T2,T3") == 1
    out = capsys.readouterr().out
    assert "part3" in out and "SUPERLITE_REAL=1" in out


def test_contract_logs_verified_entry(proj: Path):
    (proj / "tests").mkdir()
    (proj / "tests" / "test_ui.py").write_text(CONSUMER_TEST, encoding="utf-8")
    log = proj / "seam.jsonl"
    assert contract(proj, "--seam", "T2,T3", "--log", str(log)) == 0
    entry = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["kind"] == "verified" and "seam T2↔T3" in entry["context"]


def test_contract_unknown_seam_exits_2(proj: Path):
    assert contract(proj, "--seam", "T1,T3") == 2  # no edge between them
    assert contract(proj, "--seam", "T1,T9") == 2


def test_shape_checker():
    shape = pt_review.parse_shape("{a: int, b?: list[str], c: str|null, d: {e: bool}}")
    assert pt_review.check_shape({"a": 1, "c": None, "d": {"e": True}}, shape) == []
    probs = pt_review.check_shape({"a": "1", "b": [1], "c": 3, "d": {}}, shape)
    assert len(probs) == 4


# ----------------------------------------------------------------------------- standalone


def test_standalone_cli(proj: Path):
    out = proj / "packet.md"
    p = subprocess.run([sys.executable, str(SCRIPT), "packet", "--plan", str(proj / "plan.md"),
                        "--root", str(proj), "--log", str(proj / "incidents.jsonl"), "--out", str(out)],
                       capture_output=True, text=True, encoding="utf-8")
    assert p.returncode == 0, p.stderr
    assert out.is_file() and "Review packet" in out.read_text(encoding="utf-8")


def test_bad_usage_returns_2():
    assert run("packet") == 2
    assert run("nonsense") == 2
    assert run("packet", "--plan", "no-such-plan.md") == 2


def test_packet_reads_default_probe_and_test_summary_paths(proj: Path):
    """Packet defaults: .superlite/probes/probe-results.json and .superlite/test-summary.json."""
    sl = proj / ".superlite"
    (sl / "probes").mkdir(parents=True)
    (sl / "probes" / "probe-results.json").write_text(json.dumps({"results": [
        {"category": "state", "file": "src/minisheet/workbook.py", "line": 3, "status": "crash",
         "detail": "default-path probe failure"}]}), encoding="utf-8")
    (sl / "test-summary.json").write_text(json.dumps({
        "passed": 4, "failed": 1, "skipped": 0, "failures": ["tests/test_d.py::test_default"],
        "skipped_tests": []}), encoding="utf-8")
    assert run("packet", "--plan", str(proj / "plan.md"), "--root", str(proj)) == 0
    text = (sl / "review-packet.md").read_text(encoding="utf-8")
    assert "default-path probe failure" in text
    assert "FAILING: tests/test_d.py::test_default" in text


def test_contract_seam_logs_incident_by_default_and_verified_on_pass(proj: Path, capsys):
    (proj / "tests").mkdir()
    (proj / "tests" / "test_ui.py").write_text(CONSUMER_TEST, encoding="utf-8")
    log = proj / ".superlite" / "incidents.jsonl"
    assert contract(proj, "--seam", "T1,T2") == 0          # no --log: default incident log
    entries = [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines()]
    assert [(e["kind"], e["agent"]) for e in entries] == [("verified", "pt_review")]
    edit(proj / "src/minisheet/workbook.py", "def set(self, ref: str, raw: str)", "def set(self, ref: str)")
    assert contract(proj, "--seam", "T1,T2") == 1
    entries = [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines()]
    bad = [e for e in entries if e["kind"] == "exception"]
    assert bad and all(e["agent"] == "pt_review" and e["resolution"] == "open" for e in bad)
    assert any("missing parameter 'raw'" in e["context"] for e in bad)
    assert bad[0]["file"] == "src/minisheet/workbook.py"
    capsys.readouterr()
    # the packet then lists it as an open incident
    assert run("packet", "--plan", str(proj / "plan.md"), "--root", str(proj)) == 0
    assert "missing parameter 'raw'" in (proj / ".superlite" / "review-packet.md").read_text(encoding="utf-8")


def test_contract_without_seam_does_not_write_a_log(proj: Path):
    assert contract(proj) == 0
    assert not (proj / ".superlite" / "incidents.jsonl").exists()
