import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "skills" / "swarm" / "scripts" / "research.py"
FIXTURE = ROOT / "tests" / "fixtures" / "research" / "ledger.json"

sys.path.insert(0, str(SCRIPT.parent))
import research  # noqa: E402
import research_render  # noqa: E402


def ledger_at(tmp_path, data=None):
    p = tmp_path / "ledger.json"
    if data is None:
        shutil.copy(FIXTURE, p)
    else:
        p.write_text(json.dumps(data))
    return p


def load(p):
    return json.loads(Path(p).read_text())


def lint(p):
    return research.main(["lint", "--ledger", str(p)])


def test_lint_fails_on_unknown_source_id_naming_claim(tmp_path, capsys):
    data = load(FIXTURE)
    data["claims"][0]["sources"] = ["S99"]
    assert lint(ledger_at(tmp_path, data)) == 1
    out = capsys.readouterr().out
    assert "C1" in out and "S99" in out


def test_lint_fails_on_claim_with_no_source(tmp_path, capsys):
    data = load(FIXTURE)
    data["claims"][1]["sources"] = []
    assert lint(ledger_at(tmp_path, data)) == 1
    assert "C2" in capsys.readouterr().out


def test_lint_fails_on_contradiction_with_no_cause(tmp_path, capsys):
    data = load(FIXTURE)
    data["contradictions"][0]["cause"] = ""
    assert lint(ledger_at(tmp_path, data)) == 1
    assert "cause" in capsys.readouterr().out


def test_lint_fails_on_bad_status_and_unknown_claim(tmp_path, capsys):
    data = load(FIXTURE)
    data["contradictions"][1]["status"] = "maybe"
    data["contradictions"][0]["claims"] = ["C1", "C77"]
    assert lint(ledger_at(tmp_path, data)) == 1
    out = capsys.readouterr().out
    assert "maybe" in out and "C77" in out


def test_lint_passes_on_fixture():
    assert lint(FIXTURE) == 0


def test_fixture_shape():
    data = load(FIXTURE)
    assert len(data["sources"]) == 3
    assert any(s["grade"] == "A" and s["kind"] == "paper"
               and s["date"] >= "2026-01-01" for s in data["sources"])
    assert any(s.get("file_url") and s.get("size") for s in data["sources"])
    assert data["tiers"]
    assert len(data["claims"]) == 5
    assert all(any(ch.isdigit() for ch in c["text"]) for c in data["claims"])
    st = sorted(c["status"] for c in data["contradictions"])
    assert st == ["open", "resolved"]
    assert all(c["cause"] for c in data["contradictions"])


def test_fixture_is_level_free():
    data = load(FIXTURE)
    assert "level" not in json.dumps(data).lower()


def test_cli_roundtrip(tmp_path):
    base = ["--root", str(tmp_path), "--slug", "demo"]
    assert research.main(["init", *base, "--topic", "t"]) == 0
    led = tmp_path / ".superlite" / "research" / "demo" / "ledger.json"
    assert led.is_file()
    assert research.main(["source", *base, "--id", "S1", "--url", "http://x",
                          "--date", "2026-01-01", "--grade", "A"]) == 0
    assert research.main(["claim", *base, "--id", "C1", "--text", "n is 5",
                          "--source", "S1"]) == 0
    assert research.main(["claim", *base, "--id", "C2", "--text", "n is 6",
                          "--source", "S1"]) == 0
    assert lint(led) == 0
    assert research.main(["contradiction", *base, "--claim", "C1",
                          "--claim", "C2", "--cause", "units",
                          "--status", "open"]) == 0
    assert load(led)["contradictions"][0]["cause"] == "units"
    assert lint(led) == 0


def test_cli_claim_unknown_source_rejected_by_lint(tmp_path):
    base = ["--root", str(tmp_path), "--slug", "d"]
    research.main(["init", *base])
    research.main(["claim", *base, "--id", "C1", "--text", "x 1",
                   "--source", "S9"])
    led = tmp_path / ".superlite" / "research" / "d" / "ledger.json"
    assert lint(led) == 1


def test_duplicate_id_rejected(tmp_path):
    base = ["--root", str(tmp_path), "--slug", "d"]
    research.main(["init", *base])
    args = ["source", *base, "--id", "S1", "--url", "u", "--date",
            "2026-01-01", "--grade", "A"]
    assert research.main(args) == 0
    assert research.main(args) == 1


def test_script_runs_as_subprocess():
    r = subprocess.run([sys.executable, str(SCRIPT), "lint", "--ledger",
                        str(FIXTURE)], capture_output=True, text=True)
    assert r.returncode == 0


def test_init_tiers_and_grade_check(tmp_path):
    base = ["--root", str(tmp_path), "--slug", "t"]
    assert research.main(["init", *base, "--tiers", "gold,silver"]) == 0
    led = tmp_path / ".superlite" / "research" / "t" / "ledger.json"
    assert load(led)["tiers"] == ["gold", "silver"]
    src = ["source", *base, "--id", "S1", "--url", "u", "--date", "2026-01-01"]
    assert research.main([*src, "--grade", "E"]) == 1
    assert research.main([*src, "--grade", "gold", "--file-url", "http://f/a.pdf",
                          "--size", "10"]) == 0
    assert load(led)["sources"][0]["file_url"] == "http://f/a.pdf"


def test_no_tiers_allows_any_grade(tmp_path):
    base = ["--root", str(tmp_path), "--slug", "t"]
    research.main(["init", *base])
    assert research.main(["source", *base, "--id", "S1", "--url", "u", "--date",
                          "d", "--grade", "Z9"]) == 0
    assert research.main(["source", *base, "--id", "S2", "--url", "u", "--date",
                          "d", "--grade", " "]) == 1


def test_render_prints_report(capsys):
    assert research.main(["render", "--ledger", str(FIXTURE), "--level", "vp"]) == 0
    out = capsys.readouterr().out
    assert "C1" in out and "120" in out
    assert research.main(["render", "--ledger", str(FIXTURE), "--level", "x"]) == 1


def test_lint_parity(tmp_path, capsys):
    good = tmp_path / "vp.md"
    good.write_text(research_render.render(load(FIXTURE), "vp"))
    bad = tmp_path / "simple.md"
    bad.write_text(research_render.render(load(FIXTURE), "simple").replace("C1", "C-"))
    args = ["lint", "--ledger", str(FIXTURE), "--parity"]
    assert research.main([*args, str(good)]) == 0
    capsys.readouterr()
    assert research.main([*args, str(good), str(bad)]) == 1
    assert "C1" in capsys.readouterr().out


def test_downloads_lists_without_fetch(monkeypatch, capsys):
    monkeypatch.setattr(research, "fetch", lambda *a: pytest.fail("fetched"))
    assert research.main(["downloads", "--ledger", str(FIXTURE)]) == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1 and "S2" in out and "2400000" in out


def test_download_requires_yes(monkeypatch, tmp_path):
    monkeypatch.setattr(research, "fetch", lambda *a: pytest.fail("fetched"))
    assert research.main(["download", "--ledger", str(FIXTURE),
                          "--out", str(tmp_path / "o")]) == 1


def test_download_yes_fetches_only_listed(monkeypatch, tmp_path):
    got = []
    monkeypatch.setattr(research, "fetch", lambda u, d: got.append((u, Path(d).name)))
    assert research.main(["download", "--yes", "--ledger", str(FIXTURE),
                          "--out", str(tmp_path / "o")]) == 0
    assert got == [("https://example.org/reports/grid-outlook.pdf", "grid-outlook.pdf")]


def _dl(tmp_path, file_urls):
    data = load(FIXTURE)
    data["sources"] = [dict(id=f"S{i}", url="u", date="d", grade="A", file_url=u)
                       for i, u in enumerate(file_urls)]
    return ledger_at(tmp_path, data)


def test_download_rejects_non_http_scheme(monkeypatch, tmp_path):
    monkeypatch.setattr(research, "fetch", lambda *a: pytest.fail("fetched"))
    led = _dl(tmp_path, ["file:///etc/passwd"])
    assert research.main(["download", "--yes", "--ledger", str(led),
                          "--out", str(tmp_path / "o")]) == 1


def test_fetch_rejects_scheme(tmp_path):
    with pytest.raises(ValueError):
        research.fetch("file:///etc/passwd", tmp_path / "x")


def test_download_safe_names_and_duplicates(monkeypatch, tmp_path):
    got = []
    monkeypatch.setattr(research, "fetch", lambda u, d: got.append(Path(d)))
    led = _dl(tmp_path, ["http://h/a/..", "http://h/"])
    assert research.main(["download", "--yes", "--ledger", str(led),
                          "--out", str(tmp_path / "o")]) == 0
    assert all(p.parent == tmp_path / "o" and p.name not in ("..", "") for p in got)
    dup = _dl(tmp_path, ["http://h/a.pdf", "http://i/a.pdf"])
    assert research.main(["download", "--yes", "--ledger", str(dup),
                          "--out", str(tmp_path / "o2")]) == 1


def test_fetch_size_limit(monkeypatch, tmp_path):
    import io
    monkeypatch.setattr(research, "MAX_BYTES", 4)
    monkeypatch.setattr(research.urllib.request, "urlopen",
                        lambda url, timeout=0: io.BytesIO(b"123456789"))
    with pytest.raises(ValueError):
        research.fetch("http://h/a", tmp_path / "a")
    assert not (tmp_path / "a").exists()
