#!/usr/bin/env python3
"""Live spend line from host/model usage only (Cursor stop tokens, Claude cost fields).

    cost_live.line(sl_dir) -> str | None

Reads `host_usage` rows in `.superlite/metrics.jsonl` (written by capture-host-usage).
Silent when none exist — never invents $0.00 before the host reports usage.
USD: host `usd` when present; else models.json rate card on the host token counts
(labelled host+rates). When rows name an agent_id / agent_type, includes a per-agent
breakdown. Stdlib only.
"""
# Complexity notation:
#   L = metrics lines, R = host_usage rows after dedupe, A = agents with ids
from __future__ import annotations

import json  # O(1)
import sys  # O(1)
from pathlib import Path  # O(1)

HERE = Path(__file__).resolve().parent  # O(1)
if str(HERE) not in sys.path:  # O(1)
    sys.path.insert(0, str(HERE))  # O(1)

__all__ = ["host_rows", "line", "summary"]


def _load_rows(sl: Path) -> list[dict]:  # O(L)
    p = sl / "metrics.jsonl"  # O(1)
    if not p.is_file():  # O(1)
        return []  # O(1)
    out = []  # O(1)
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():  # O(L)
        ln = ln.strip()  # O(1)
        if not ln:  # O(1)
            continue  # O(1)
        try:  # O(1)
            e = json.loads(ln)  # O(w)
        except ValueError:  # O(1)
            continue  # O(1)
        if isinstance(e, dict) and e.get("ev") == "host_usage":  # O(1)
            out.append(e)  # O(1)
    return out  # O(1)


def host_rows(sl_dir: str) -> list[dict]:  # O(L)
    """Latest host_usage per agent+generation (coordinator rows have empty agent_id)."""
    rows = _load_rows(Path(sl_dir))  # O(L)
    by: dict[str, dict] = {}  # O(1)
    for i, e in enumerate(rows):  # O(L)
        agent = str(e.get("agent_id") or "")  # O(1)
        gen = str(e.get("generation_id") or e.get("conversation_id") or f"row-{i}")  # O(1)
        by[f"{agent}|{gen}"] = e  # later line wins | O(1)
    return list(by.values())  # O(R)


def _row_usd(e: dict, prices: dict) -> tuple[float, str]:  # O(1)
    """USD for one host_usage row: host usd when set, else rate card on its tokens."""
    import metrics  # noqa: E402  | O(1)
    if isinstance(e.get("usd"), (int, float)):  # O(1)
        return float(e["usd"]), "host"  # O(1)
    tok = metrics._empty()  # O(1)
    tok["input"] = int(e.get("input") or 0)  # O(1)
    tok["output"] = int(e.get("output") or 0)  # O(1)
    tok["cache_read"] = int(e.get("cache_read") or 0)  # O(1)
    tok["cache_write_5m"] = int(e.get("cache_write") or 0)  # O(1)
    fam = metrics.family(e.get("model")) or metrics.family(e.get("agent_type")) or "sonnet"  # O(1)
    price = prices.get(fam) or prices["sonnet"]  # O(1)
    return metrics.cost_of(tok, price), "host+rates"  # O(1)


def _agent_label(e: dict) -> str:  # O(1)
    return str(e.get("agent_type") or e.get("agent_id") or "agent")  # O(1)


def summary(sl_dir: str) -> dict | None:  # O(L + rates)
    """Aggregated host usage, or None when the host has not reported yet."""
    rows = host_rows(sl_dir)  # O(L)
    if not rows:  # O(1)
        return None  # O(1)
    import metrics  # noqa: E402  | O(1)
    prices = metrics.load_prices()  # O(file)
    tok = metrics._empty()  # O(1)
    hosts: set[str] = set()  # O(1)
    sources: set[str] = set()  # O(1)
    usd_total = 0.0  # O(1)
    agents: list[dict] = []  # O(1)
    for e in rows:  # O(R)
        hosts.add(str(e.get("host") or "host"))  # O(1)
        tok["input"] += int(e.get("input") or 0)  # O(1)
        tok["output"] += int(e.get("output") or 0)  # O(1)
        tok["cache_read"] += int(e.get("cache_read") or 0)  # O(1)
        tok["cache_write_5m"] += int(e.get("cache_write") or 0)  # O(1)
        row_usd, src = _row_usd(e, prices)  # O(1)
        sources.add(src)  # O(1)
        usd_total += row_usd  # O(1)
        if e.get("agent_id") or e.get("agent_type"):  # O(1)
            atok = int((e.get("input") or 0) + (e.get("output") or 0)  # O(1)
                       + (e.get("cache_read") or 0) + (e.get("cache_write") or 0))
            agents.append({"agent": _agent_label(e), "agent_id": e.get("agent_id") or "",  # O(1)
                           "usd": round(row_usd, 4), "tokens": atok, "source": src})
    # Prefer "host" if any row had host usd; else host+rates. Mixed → host+rates unless all host. | O(1)
    src = "host" if sources == {"host"} else ("host+rates" if "host+rates" in sources else "host")  # O(1)
    tokens = int(tok["input"] + tok["output"] + tok["cache_read"] + tok["cache_write_5m"] + tok["cache_write_1h"])  # O(1)
    out = {"source": src, "usd": round(usd_total, 4), "tokens": tokens, "turns": len(rows),  # O(1)
           "input": tok["input"], "output": tok["output"], "cache_read": tok["cache_read"],
           "cache_write": tok["cache_write_5m"], "host": ",".join(sorted(hosts)),
           "agents": agents}
    sl = Path(sl_dir)  # O(1)
    sl.mkdir(parents=True, exist_ok=True)  # O(1)
    (sl / "cost-summary.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")  # O(1)
    return out  # O(1)


def line(sl_dir: str) -> str | None:  # O(L + rates)
    """Host-sourced spend block (multi-line; per-agent when present), or None before reports."""
    s = summary(sl_dir)  # O(L)
    if not s:  # O(1)
        return None  # O(1)
    turns = f"{s['turns']} turn" + ("" if s["turns"] == 1 else "s")  # O(1)
    lines = [  # O(1)
        f"superlite cost ({s['source']}, {s['host']}): ${s['usd']:.4f}",
        f"  tokens {s['tokens']} | {turns}",
        f"  in {s['input']} out {s['output']} cache_r {s['cache_read']}",
    ]
    agents = s.get("agents") or []  # O(1)
    if agents:  # O(A)
        lines.append("  agents:")  # O(1)
        for a in agents:  # O(A)
            lines.append(f"    {a['agent']} ${a['usd']:.4f} ({a['tokens']} tok)")  # O(1)
    return "\n".join(lines)  # O(A)


def main(argv=None) -> int:  # O(L)
    import argparse  # O(1)
    ap = argparse.ArgumentParser(description=__doc__.split(chr(10))[0])  # O(1)
    ap.add_argument("sl_dir", nargs="?", default=".superlite")  # O(1)
    a = ap.parse_args(argv)  # O(1)
    text = line(a.sl_dir)  # O(L)
    if text:  # O(1)
        print(text)  # O(1)
    return 0  # O(1)


if __name__ == "__main__":  # O(1)
    raise SystemExit(main())  # O(1)
