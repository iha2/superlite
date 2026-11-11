"""predict.load_coef refits on unique, non-prototype history rows."""
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import predict  # noqa: E402


def row(i, run="r1", **extra):
    return {"run": run, "date": f"2026-01-{1 + i % 28:02d}", "task": f"T{i}", "loc": 20 + 7 * i, "components": 1 + i % 3,
            "tests": "slow" if i % 5 == 0 else "fast", "model": "sonnet", "duration_s": 60 + 25 * i,
            "tokens": {"in": 100 + i, "out": 50 + i}, **extra}


@pytest.fixture
def hist(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERLITE_HOME", str(tmp_path))
    monkeypatch.delenv("SUPERLITE_HISTORY_MIN", raising=False)
    predict._HIST_CACHE.clear()

    def write(rows):
        (tmp_path / "history.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        predict._HIST_CACHE.clear()
        return predict.load_coef()
    return write


def test_duplicates_same_coef(hist):
    single = [row(i) for i in range(35)]
    a = hist(single)
    assert a["fit"]["source"] == "history"
    b = hist(single + [row(i, run="r2") for i in range(35)])
    assert b["time"] == a["time"] and b["fit"]["n"] == a["fit"]["n"] == 35


def test_proto_rows_ignored(hist):
    build = [row(i) for i in range(35)]
    a = hist(build)
    b = hist(build + [row(100 + i, phase="proto") for i in range(40)])
    assert b["time"] == a["time"]
    shipped = predict.load_coef(SCRIPTS.parent / "config" / "predictor.json")
    c = hist([row(i) for i in range(10)] + [row(100 + i, phase="proto") for i in range(40)])
    assert c["time"] == shipped["time"] and "source" not in c.get("fit", {})


def test_threshold_counts_unique_build_rows(hist):
    shipped = predict.load_coef(SCRIPTS.parent / "config" / "predictor.json")
    uniq = [row(i) for i in range(29)]
    c = hist(uniq + [row(i, run="r2") for i in range(29)])
    assert c["time"] == shipped["time"]
    d = hist(uniq + [row(29)])
    assert d["fit"]["source"] == "history"


def test_proto_twin_does_not_shadow_build_row(hist):
    build = [row(i) for i in range(35)]
    a = hist(build)
    b = hist([row(i, run="r0", phase="proto") for i in range(35)] + build)
    assert b["fit"]["n"] == 35 and b["time"] == a["time"]
