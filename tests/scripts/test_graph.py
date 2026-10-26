"""ASCII task graph: width, critical-path star, cache hash, ASCII only."""
from __future__ import annotations

import hashlib
import importlib
import sys
import time
from pathlib import Path

SCRIPTS = (Path(__file__).resolve().parents[2] / "skills"
           / "swarm" / "scripts")
sys.path.insert(0, str(SCRIPTS))
graph = importlib.import_module("graph")


def tiny_plan(tmp_path: Path) -> Path:
    # Longest path T1->T2->T3; T4 is a short branch (deterministic * rule).
    text = (
        "# Plan\n**Goal:** graph fixture.\n\n"
        "```mermaid\n%% taskgraph\nflowchart LR\n"
        "  T1 --> T2\n  T2 --> T3\n  T1 --> T4\n"
        "```\n\n"
        "### T1 a · Complexity 3 · Est. context 20k\n"
        "**Owns:** a.py\n\n"
        "### T2 b · Complexity 3 · Est. context 20k\n"
        "**Owns:** b.py\n\n"
        "### T3 c · Complexity 3 · Est. context 20k\n"
        "**Owns:** c.py\n\n"
        "### T4 d · Complexity 3 · Est. context 20k\n"
        "**Owns:** d.py\n"
    )
    p = tmp_path / "plan.md"
    p.write_text(text, encoding="utf-8")
    return p


def test_width_and_critical_star(tmp_path):
    out = graph.render(str(tiny_plan(tmp_path)))
    assert out.isascii()
    assert "[T" in out
    assert "*" in out
    for ln in out.splitlines():
        assert len(ln) <= 79, repr(ln)


def test_cache_file_hash(tmp_path):
    plan = tiny_plan(tmp_path)
    out1 = graph.render(str(plan))
    cache = tmp_path / ".superlite" / "graph.txt"
    assert cache.is_file()
    raw = cache.read_text(encoding="utf-8")
    assert raw.isascii()
    assert "# hash:" in raw.splitlines()[0]
    digest = hashlib.sha256(plan.read_bytes()).hexdigest()
    assert digest in raw.splitlines()[0]
    time.sleep(0.05)
    mtime1 = cache.stat().st_mtime_ns
    out2 = graph.render(str(plan))
    assert out2 == out1
    assert cache.stat().st_mtime_ns == mtime1
