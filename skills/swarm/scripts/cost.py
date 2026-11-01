#!/usr/bin/env python3
"""Run cost: the live host spend line, the per-agent cost table, and the host-truth overlay.

    cost.py line [RUN_DIR]     host-reported spend block; prints nothing before the host reports usage
    cost.py table [RUN_DIR]    markdown table: agent, model, wall_min, tokens, usd, with a totals row

Library: line(run_dir) -> str | None, table(run_dir) -> str, summary(run_dir) -> dict | None (host rows),
est_summary(run_dir) -> dict (estimate; source=est), apply_host(summary, kind, path) -> dict.

Host rows are `host_usage` lines in metrics.jsonl (written by capture-host-usage). USD is the host's `usd`
when present, else the models.json rate card on the host token counts (labelled host+rates). The estimate
comes from `cost-rows.json` when present, else from metrics.agent_records. Both write cost-summary.json.
RUN_DIR defaults to the current checkout's run-state folder. Stdlib only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402
import state  # noqa: E402

USD_PLACES = 4


def _round_usd(v) -> float:
    return round(float(v), USD_PLACES)


def _rows_of_event(run_dir: Path, ev: str) -> list[dict]:  # O(L)
    p = Path(run_dir) / "metrics.jsonl"
    if not p.is_file():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        if isinstance(e, dict) and e.get("ev") == ev:
            out.append(e)
    return out


def host_rows(run_dir) -> list[dict]:  # O(L)
    """Latest host_usage per agent+generation (coordinator rows have empty agent_id)."""
    by: dict[str, dict] = {}
    for i, e in enumerate(_rows_of_event(Path(run_dir), "host_usage")):
        agent = str(e.get("agent_id") or "")
        gen = str(e.get("generation_id") or e.get("conversation_id") or f"row-{i}")
        by[f"{agent}|{gen}"] = e  # later line wins
    return list(by.values())


def _row_usd(e: dict, prices: dict) -> tuple[float, str]:
    """USD for one host_usage row: host usd when set, else rate card on its tokens."""
    import metrics
    if isinstance(e.get("usd"), (int, float)):
        return float(e["usd"]), "host"
    tok = metrics._empty()
    tok["input"] = int(e.get("input") or 0)
    tok["output"] = int(e.get("output") or 0)
    tok["cache_read"] = int(e.get("cache_read") or 0)
    tok["cache_write_5m"] = int(e.get("cache_write") or 0)
    fam = metrics.family(e.get("model")) or metrics.family(e.get("agent_type")) or "sonnet"
    price = prices.get(fam) or prices["sonnet"]
    return metrics.cost_of(tok, price), "host+rates"


def summary(run_dir) -> dict | None:  # O(L + rates)
    """Aggregated host usage (also written to cost-summary.json), or None when the host has not reported."""
    import metrics
    rows = host_rows(run_dir)
    if not rows:
        return None
    prices = metrics.load_prices()
    tok = metrics._empty()
    hosts: set[str] = set()
    sources: set[str] = set()
    usd_total = 0.0
    agents: list[dict] = []
    for e in rows:
        hosts.add(str(e.get("host") or "host"))
        tok["input"] += int(e.get("input") or 0)
        tok["output"] += int(e.get("output") or 0)
        tok["cache_read"] += int(e.get("cache_read") or 0)
        tok["cache_write_5m"] += int(e.get("cache_write") or 0)
        row_usd, src = _row_usd(e, prices)
        sources.add(src)
        usd_total += row_usd
        if e.get("agent_id") or e.get("agent_type"):
            atok = int((e.get("input") or 0) + (e.get("output") or 0)
                       + (e.get("cache_read") or 0) + (e.get("cache_write") or 0))
            agents.append({"agent": str(e.get("agent_type") or e.get("agent_id") or "agent"),
                           "agent_id": e.get("agent_id") or "", "usd": round(row_usd, 4), "tokens": atok, "source": src})
    src = "host" if sources == {"host"} else ("host+rates" if "host+rates" in sources else "host")
    tokens = int(tok["input"] + tok["output"] + tok["cache_read"] + tok["cache_write_5m"] + tok["cache_write_1h"])
    out = {"source": src, "usd": round(usd_total, 4), "tokens": tokens, "turns": len(rows),
           "input": tok["input"], "output": tok["output"], "cache_read": tok["cache_read"],
           "cache_write": tok["cache_write_5m"], "host": ",".join(sorted(hosts)), "agents": agents}
    base.write_json(Path(run_dir) / "cost-summary.json", out)
    return out


def line(run_dir) -> str | None:  # O(L + rates)
    """Host-sourced spend block (multi-line; per-agent when present), or None before the host reports."""
    s = summary(run_dir)
    if not s:
        return None
    turns = f"{s['turns']} turn" + ("" if s["turns"] == 1 else "s")
    lines = [
        f"superlite cost ({s['source']}, {s['host']}): ${s['usd']:.4f}",
        f"  tokens {s['tokens']} | {turns}",
        f"  in {s['input']} out {s['output']} cache_r {s['cache_read']}",
    ]
    if s["agents"]:
        lines.append("  agents:")
        lines.extend(f"    {a['agent']} ${a['usd']:.4f} ({a['tokens']} tok)" for a in s["agents"])
    return "\n".join(lines)


def _norm_row(r: dict) -> dict:
    return {"agent": str(r["agent"]), "model": str(r.get("model") or ""),
            "wall_min": float(r.get("wall_min") or 0.0), "tokens": int(r.get("tokens") or 0),
            "usd": _round_usd(r.get("usd") or 0.0)}


def _rows_from_metrics(run_dir: Path) -> list:  # O(A + B)
    import metrics
    prices = metrics.load_prices()
    rows = []
    for a in metrics.agent_records(run_dir):
        tok = metrics._empty()
        for s in a.get("segments") or []:
            for k in tok:
                tok[k] += (s.get("tok") or {}).get(k) or 0
        fam = metrics.family(a.get("model")) or metrics.family(a.get("type")) or "sonnet"
        price = prices.get(fam) or prices["sonnet"]
        start = (a.get("start_ms") or 0) / 1000.0
        stop = (a.get("stop_ms") or a.get("start_ms") or 0) / 1000.0
        wall_min = max(0.0, (stop - start) / 60.0)
        tokens = int(tok["input"] + tok["output"] + tok["cache_read"] + tok["cache_write_5m"] + tok["cache_write_1h"])
        rows.append(_norm_row({"agent": a.get("agent") or "", "model": fam, "wall_min": round(wall_min, 4),
                               "tokens": tokens, "usd": metrics.cost_of(tok, price)}))
    return rows


def est_summary(run_dir) -> dict:  # O(R) or O(A + B)
    """Estimated run cost (source=est until apply_host overlays host truth); written to cost-summary.json."""
    run_dir = Path(run_dir)
    fixture = base.read_json(run_dir / "cost-rows.json", None)
    rows = [_norm_row(r) for r in fixture] if isinstance(fixture, list) else _rows_from_metrics(run_dir)
    out = {"source": "est", "usd": _round_usd(sum(r["usd"] for r in rows)),
           "tokens": sum(r["tokens"] for r in rows), "wall_min": round(sum(r["wall_min"] for r in rows), 4),
           "rows": rows}
    base.write_json(run_dir / "cost-summary.json", out)
    return out


def table(run_dir) -> str:  # O(R)
    """Markdown table: agent | model | wall_min | tokens | usd, plus a totals row."""
    s = est_summary(run_dir)
    lines = ["| agent | model | wall_min | tokens | usd |",
             "| --- | --- | --- | --- | --- |"]
    for r in s["rows"]:
        lines.append(f"| {r['agent']} | {r['model']} | {r['wall_min']} | {r['tokens']} | {r['usd']} |")
    lines.append(f"| totals |  | {s['wall_min']} | {s['tokens']} | {s['usd']} |")
    return "\n".join(lines) + "\n"


def _claude_total(path: Path) -> float | None:  # O(L)
    """Last cumulative total_cost_usd in a Claude stream-json log."""
    truth = None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for ln in text.splitlines():
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        if isinstance(e, dict) and isinstance(e.get("total_cost_usd"), (int, float)):
            truth = float(e["total_cost_usd"])
    return truth


def _json_usd(path: Path) -> float | None:
    data = base.read_json(path, None)
    if isinstance(data, dict) and isinstance(data.get("usd"), (int, float)):
        return float(data["usd"])
    return None


def apply_host(summary_: dict, kind: str, path: str) -> dict:  # O(L)
    """The summary with source=host and usd from path, or unchanged (est) when the path is empty or unusable."""
    if not path or not Path(path).is_file():
        return summary_
    usd = _claude_total(Path(path)) if kind == "claude" else _json_usd(Path(path))
    if usd is None:
        return summary_
    return {**summary_, "source": "host", "usd": usd}


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in ("line", "table") or len(argv) > 2:
        print("usage: cost.py line|table [RUN_DIR]", file=sys.stderr)
        return 2
    run_dir = Path(argv[1]) if len(argv) == 2 else state.run_dir(Path.cwd())
    if argv[0] == "table":
        sys.stdout.write(table(run_dir))
        return 0
    text = line(run_dir)
    if text:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
