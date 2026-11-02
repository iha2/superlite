"""Mermaid task flowchart: nodes, edges, critical class, labels, cache hash."""
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


def _critical(out: str) -> list[str]:
    line = next(ln for ln in out.splitlines() if ln.strip().startswith("class "))
    return line.split()[1].split(",")


def test_flowchart_nodes_edges_and_longest_path_critical(tmp_path):
    out = graph.render(str(tiny_plan(tmp_path)))
    body = out.split("\n", 1)[1]
    assert body.splitlines() == [
        "flowchart TD",
        '  T1["T1 a"]', '  T2["T2 b"]', '  T3["T3 c"]', '  T4["T4 d"]',
        "  T1 --> T2", "  T2 --> T3", "  T1 --> T4",
        "  classDef critical stroke:#d33,stroke-width:3px",
        "  class T1,T2,T3 critical",
    ]
    assert not any(ln.lstrip().startswith("graph ") for ln in body.splitlines())


def test_label_swaps_quotes_brackets_and_keeps_accents(tmp_path):
    plan = tiny_plan(tmp_path)
    plan.write_text(plan.read_text(encoding="utf-8").replace("### T1 a ·", '### T1 say "hi" [x] <y> `z` #2 café ·'),
                    encoding="utf-8")
    out = graph.render(str(plan))
    assert "  T1[\"T1 say 'hi' (x) (y) 'z' no. 2 café\"]" in out.splitlines()


def test_lone_task_is_a_node(tmp_path):
    plan = tiny_plan(tmp_path)
    plan.write_text(plan.read_text(encoding="utf-8") + "\n### T5 e · Complexity 3 · Est. context 20k\n**Owns:** e.py\n",
                    encoding="utf-8")
    out = graph.render(str(plan))
    assert '  T5["T5 e"]' in out.splitlines()
    assert not any("T5 -->" in ln or "--> T5" in ln for ln in out.splitlines())


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


def test_cache_from_the_ascii_renderer_is_redrawn(tmp_path):
    plan = tiny_plan(tmp_path)
    cache = tmp_path / ".superlite" / "graph.txt"
    cache.parent.mkdir(parents=True)
    digest = hashlib.sha256(plan.read_bytes()).hexdigest()
    cache.write_text(f"# hash: {digest}\n[T1*]\n  v\n[T2*]  [T4]\n* = critical path\n", encoding="utf-8")
    out = graph.render(str(plan))
    assert out.splitlines()[1] == "flowchart TD"
    assert cache.read_text(encoding="utf-8") == out


def test_critical_argument_marks_exactly_those_tasks(tmp_path):
    plan = str(tiny_plan(tmp_path))
    assert _critical(graph.render(plan, ["T1", "T4"])) == ["T1", "T4"]


def test_one_argument_render_ignores_a_critical_cache(tmp_path):
    plan = str(tiny_plan(tmp_path))
    graph.render(plan, ["T1", "T4"])
    assert _critical(graph.render(plan)) == ["T1", "T2", "T3"]


def test_cycle_is_a_value_error(tmp_path):
    plan = tiny_plan(tmp_path)
    text = plan.read_text(encoding="utf-8").replace("T1 --> T4", "T3 --> T1")
    plan.write_text(text, encoding="utf-8")
    try:
        graph.render(str(plan))
    except ValueError as e:
        assert "cycle" in str(e)
    else:
        raise AssertionError("cycle not reported")
