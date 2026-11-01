#!/usr/bin/env python3
"""Per-task records from a run's `.superlite/metrics.jsonl` (written by the capture hooks).

    metrics.py [--dir .superlite] [--plan P] [--json] [--task Tn]

Hook lines (one JSON object per line, `t` in epoch ms):
    {"ev":"launch","t":ms,"agent_id","type","model","desc"}
    {"ev":"stop","t":ms,"agent_id","type","transcript","last"}
Launch and stop are joined by agent_id. The agent's own transcript (JSONL, `agent_transcript_path`) gives
usage per assistant message (deduplicated by message id), tool calls and timestamps; prices come from
config/models.json. An agent is mapped to its task through `.superlite/pool.json` (agent id -> tasks) or the
`Tn` in its launch description; an agent reused for several tasks is split at the user messages that name a
`Tn`. Output fields reuse the names of bench/mechanical.py: task, kind, model, tier_planned, agent,
agent_model_used, dispatches, start_s, end_s, duration_s, tokens {in,out,cache_read,cache_write,...},
cost_usd, tool_calls, status, complete, rework_dispatches, rework_s, rework_cost, kind_miss_incidents,
fix_notes, first_time_pass, done_s, start_lag_s, slack_s, on_critical_path. Offline, stdlib only; one parse
of each file, well under a second for a normal run.
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import state  # noqa: E402

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
FAMILIES = ("haiku", "sonnet", "opus")
TID = re.compile(r"\bT(\d+[a-z]?)\b")
STATUS = re.compile(r"\bStatus:\s*\**\s*(DONE_WITH_CONCERNS|DONE|NEEDS_CONTEXT|BLOCKED)\b", re.I)
RED = re.compile(r"\b(?:tests?|suite)\b[^\n]{0,40}\b(?:red|failing|failed)\b|\b\d+\s+failed\b", re.I)
CHARS_PER_TOKEN = 3.5  # output_tokens in a transcript is an early snapshot of a streamed message; text length bounds it below


def msg_chars(msg) -> int:  # O(content)
    """Characters the message produced: text, thinking and tool-call input (one message id spans several entries)."""
    n = 0
    for b in msg.get("content") or []:
        if not isinstance(b, dict):
            continue
        n += len(b.get("text") or "") + len(b.get("thinking") or "")
        if b.get("type") == "tool_use":
            n += len(json.dumps(b.get("input") or {}))
    return n


FIXER = re.compile(r"fixer|\bfix(?:es|ed)?\b|F-T\d+-\d+|re-?dispatch|rework", re.I)


def family(model) -> str | None:
    m = str(model or "").lower()
    return next((f for f in FAMILIES if f in m), None)


def load_prices(path=None) -> dict:  # O(file)
    cfg = json.loads(Path(path or CONFIG_DIR / "models.json").read_text(encoding="utf-8"))
    if path is None:  # an explicit file wins over superlite.json
        scripts = str(Path(__file__).resolve().parent)
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import project_config
        cfg = project_config.apply_project_prices(cfg, Path(".").resolve())
    per = float(cfg.get("per_tokens") or 1_000_000)
    mult = cfg.get("cache_write_multipliers") or {}
    out = {}
    for name, m in (cfg.get("models") or {}).items():
        w5 = m.get("cache_write_5m", m["input"] * mult.get("5m", 1.25))
        w1 = m.get("cache_write_1h", m["input"] * mult.get("1h", 2.0))
        out[name] = tuple(float(x) / per for x in (m["input"], m["output"], w5, w1, m["cache_read"]))
    return out


def cost_of(tok: dict, price: tuple) -> float:
    pi, po, w5, w1, pr = price
    return (tok["input"] * pi + tok["output"] * po + tok["cache_write_5m"] * w5 + tok["cache_write_1h"] * w1
            + tok["cache_read"] * pr)


def _ts(e) -> float | None:
    s = e.get("timestamp")
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() if isinstance(s, str) else None
    except ValueError:
        return None


def _empty() -> dict:
    return {"input": 0, "output": 0, "cache_read": 0, "cache_write_5m": 0, "cache_write_1h": 0}


def _text_of(content) -> str:  # O(content)
    if isinstance(content, str):
        return content
    return " ".join(b.get("text") or "" for b in content or [] if isinstance(b, dict) and b.get("type") == "text")


def read_jsonl(path) -> list:  # O(bytes)
    out = []
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for ln in lines:
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        if isinstance(e, dict):
            out.append(e)
    return out


def _count_usage(seen: dict, mid: str, cur: dict, msg: dict) -> None:  # O(usage fields)
    """Add one assistant message's token counts to its segment; a repeated id keeps the largest count seen."""
    u = msg.get("usage") or {}
    cc = u.get("cache_creation") or {}
    w1, w5 = cc.get("ephemeral_1h_input_tokens"), cc.get("ephemeral_5m_input_tokens")
    if w1 is None and w5 is None:
        w5, w1 = u.get("cache_creation_input_tokens") or 0, 0
    rec = {"input": u.get("input_tokens") or 0, "output": u.get("output_tokens") or 0,
           "cache_read": u.get("cache_read_input_tokens") or 0, "cache_write_5m": w5 or 0, "cache_write_1h": w1 or 0}
    prev = seen.get(mid)
    if prev is None:
        seen[mid] = {"rec": dict(rec), "seg": cur, "chars": msg_chars(msg)}
        for k, v in rec.items():
            cur["tok"][k] += v
        return
    prev["chars"] += msg_chars(msg)  # a repeated message id (streamed snapshots): keep the largest
    for k, v in rec.items():
        if v > prev["rec"][k]:
            prev["seg"]["tok"][k] += v - prev["rec"][k]
            prev["rec"][k] = v


def transcript_segments(path, tasks_hint: list) -> list:  # O(bytes of one transcript)
    """[{tid, tokens, tool_calls, first, last, model, text}] split at user messages that name a Tn.
    Output tokens are the message's own count (the maximum seen per message id)."""
    segs: list[dict] = []
    cur = None
    seen: dict[str, dict] = {}  # message id -> its segment record
    for e in read_jsonl(path):
        msg = e.get("message") or {}
        t = _ts(e)
        kind = e.get("type") or e.get("role")  # Claude lines use type; Cursor transcripts use role
        if kind == "user" and not any(isinstance(b, dict) and b.get("type") == "tool_result"
                                                for b in (msg.get("content") if isinstance(msg.get("content"), list) else [])):
            m = TID.search(_text_of(msg.get("content"))[:400])
            if m and (cur is None or cur["tid"] != "T" + m.group(1)):
                cur = {"tid": "T" + m.group(1), "tok": _empty(), "calls": set(), "first": t, "last": t, "model": "", "text": ""}
                segs.append(cur)
        if kind != "assistant":
            continue
        if cur is None:  # work before any task message (the prime): belongs to the first task
            cur = {"tid": tasks_hint[0] if tasks_hint else None, "tok": _empty(), "calls": set(), "first": t, "last": t, "model": "", "text": ""}
            segs.append(cur)
        mid = msg.get("id") or f"anon{len(seen)}"
        _count_usage(seen, mid, cur, msg)
        for b in msg.get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                cur["calls"].add(b.get("id") or f"{mid}:{len(cur['calls'])}")
        tx = _text_of(msg.get("content"))
        if tx:
            cur["text"] = tx
        cur["model"] = msg.get("model") or cur["model"]
        if t is not None:
            cur["first"] = cur["first"] if cur["first"] is not None else t
            cur["last"] = t
    for v in seen.values():  # top up each message's output tokens from its text length
        est = int(v["chars"] / CHARS_PER_TOKEN)
        if est > v["rec"]["output"]:
            v["seg"]["tok"]["output"] += est - v["rec"]["output"]
    return segs


def agent_records(sl_dir: Path) -> list:  # O(M + B)
    """One record per agent: launch/stop joined, its transcript segments."""
    launches: dict[str, dict] = {}
    stops: dict[str, list] = {}
    for e in read_jsonl(sl_dir / "metrics.jsonl"):
        aid = e.get("agent_id")
        if not aid:
            continue
        if e.get("ev") == "launch":
            launches.setdefault(aid, e)
        elif e.get("ev") == "stop":
            stops.setdefault(aid, []).append(e)
    pool: dict[str, list] = {}
    try:
        for ag in json.loads((sl_dir / "pool.json").read_text(encoding="utf-8")).get("agents", []):
            pool[str(ag.get("id"))] = [t for t in ag.get("tasks") or [] if isinstance(t, str)]
    except (OSError, ValueError, AttributeError):
        pass
    out = []
    for aid in sorted(set(launches) | set(stops)):
        la, sts = launches.get(aid, {}), stops.get(aid, [])
        desc = la.get("desc") or ""
        hint = pool.get(aid) or ([ "T" + TID.search(desc).group(1)] if TID.search(desc) else [])
        segs = []
        for s in sts:
            if s.get("transcript"):
                segs = transcript_segments(s["transcript"], hint)
                break
        out.append({"agent": aid, "type": la.get("type") or (sts[0].get("type") if sts else ""), "model": la.get("model") or "",
                    "desc": desc, "start_ms": la.get("t"), "stop_ms": max((s.get("t") or 0 for s in sts), default=None) if sts else None,
                    "last": report_of(sts[-1]) if sts else "", "segments": segs, "hint": hint, "complete": bool(sts),
                    "first_abs": min((x["first"] for x in segs if x["first"] is not None), default=None)})
    return out


def report_of(stop_event, limit=400) -> str:  # O(transcript)
    """What an agent last told its caller. A subagent reports through the `SubagentHandback` tool, so the stop event's `last` is often
    empty: then read the end of the agent's transcript for the handback message (else its last text). At most `limit` characters."""
    t = ((stop_event or {}).get("last") or "").strip()
    if t:
        return t[:limit]
    try:
        lines = Path((stop_event or {}).get("transcript") or "").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for ln in reversed(lines):
        if '"assistant"' not in ln:
            continue
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        content = (e.get("message") or {}).get("content") if isinstance(e, dict) and e.get("type") == "assistant" else None
        for b in (content if isinstance(content, list) else [])[::-1]:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and b.get("name") == "SubagentHandback":
                return str((b.get("input") or {}).get("message") or "")[:limit]
            if b.get("type") == "text" and (b.get("text") or "").strip():
                return b["text"].strip()[:limit]
    return ""


def session_transcript(sl_dir: Path) -> str | None:  # O(M)
    """The coordinator's (main session) transcript: a `session_transcript`/`transcript_path` key on any metrics line, else
    derived from an agent transcript path `<dir>/<session>/subagents/agent-X.jsonl` -> `<dir>/<session>.jsonl`."""
    lines = read_jsonl(sl_dir / "metrics.jsonl")
    for e in lines:
        for k in ("session_transcript", "transcript_path"):
            if e.get(k) and Path(e[k]).is_file():
                return e[k]
    for e in lines:
        pp = Path(e.get("transcript") or "")
        if pp.parent.name == "subagents":
            main = pp.parent.parent.with_suffix(".jsonl")
            if main.is_file():
                return str(main)
    return None


def coordinator_record(sl_dir, prices=None) -> dict | None:  # O(bytes of the main transcript)
    """The coordinator as one line: usage per assistant message (deduplicated by message id), turns, context peak and
    median (input + cache read + cache write per message), cost by model, and its first-to-last message time."""
    sl_dir = Path(sl_dir)
    path = session_transcript(sl_dir)
    if not path:
        return None
    prices = prices or load_prices()
    seen: dict[str, dict] = {}
    first = last = None
    for e in read_jsonl(path):
        if e.get("isSidechain") or e.get("type") != "assistant":  # subagent entries are counted per agent
            continue
        msg = e.get("message") or {}
        u = msg.get("usage") or {}
        t = _ts(e)
        if t is not None:
            first = t if first is None else first
            last = t
        mid = msg.get("id") or f"anon{len(seen)}"
        cc = u.get("cache_creation") or {}
        w1, w5 = cc.get("ephemeral_1h_input_tokens"), cc.get("ephemeral_5m_input_tokens")
        if w1 is None and w5 is None:
            w5, w1 = u.get("cache_creation_input_tokens") or 0, 0
        rec = {"input": u.get("input_tokens") or 0, "output": u.get("output_tokens") or 0,
               "cache_read": u.get("cache_read_input_tokens") or 0, "cache_write_5m": w5 or 0, "cache_write_1h": w1 or 0,
               "model": msg.get("model") or ""}
        old = seen.get(mid)
        if old is None:
            rec["chars"] = msg_chars(msg)
            seen[mid] = rec
        else:
            old["chars"] += msg_chars(msg)  # a repeated snapshot: keep the largest of each counter
            for k in ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h"):
                old[k] = max(old[k], rec[k])
            old["model"] = rec["model"] or old["model"]
    tok, by_model, ctx = _empty(), {}, []
    for rec in seen.values():
        rec["output"] = max(rec["output"], int(rec["chars"] / CHARS_PER_TOKEN))
        fam = family(rec["model"]) or "sonnet"
        t5 = {k: rec[k] for k in tok}
        for k in tok:
            tok[k] += t5[k]
        by_model[fam] = by_model.get(fam, 0.0) + cost_of(t5, prices.get(fam) or prices["sonnet"])
        ctx.append(rec["input"] + rec["cache_read"] + rec["cache_write_5m"] + rec["cache_write_1h"])
    ctx.sort()
    return {"turns": len(seen), "tokens": {"in": tok["input"], "out": tok["output"], "cache_read": tok["cache_read"],
                                           "cache_write": tok["cache_write_5m"] + tok["cache_write_1h"]},
            "cost_usd": round(sum(by_model.values()), 6), "cost_by_model": {k: round(v, 6) for k, v in sorted(by_model.items())},
            "context_peak": ctx[-1] if ctx else 0, "context_median": ctx[len(ctx) // 2] if ctx else 0,
            "duration_s": round(last - first, 1) if first is not None and last is not None else None, "transcript": path}


def agents_cost(sl_dir, prices=None) -> float:  # O(M + B)
    """Cost of every agent segment in metrics.jsonl, including ones not mapped to a plan task."""
    prices = prices or load_prices()
    return sum(cost_of(s["tok"], prices.get(family(s["model"]) or family(a["type"]) or "sonnet") or prices["sonnet"])
               for a in agent_records(Path(sl_dir)) for s in a["segments"])


def reconcile(sl_dir, stream_log, prices=None) -> dict:  # O(M + B + log)
    """Coordinator + agents from the transcripts against the stream-json log's cumulative total_cost_usd."""
    truth = None
    for e in read_jsonl(stream_log):
        if isinstance(e.get("total_cost_usd"), (int, float)):
            truth = float(e["total_cost_usd"])  # the last (cumulative) value wins
    co = coordinator_record(sl_dir, prices)
    ag = round(agents_cost(sl_dir, prices), 6)
    mine = round((co["cost_usd"] if co else 0.0) + ag, 6)
    diff = None if truth is None else round(mine - truth, 6)
    return {"metrics_usd": mine, "coordinator_usd": co["cost_usd"] if co else None, "agents_usd": ag,
            "stream_usd": truth, "diff_usd": diff, "diff_pct": None if not truth else round(100.0 * diff / truth, 2),
            "higher": None if diff is None else ("metrics" if diff > 0 else "stream" if diff < 0 else "equal")}


def task_records(sl_dir, plan_path=None, prices=None) -> list:  # O(M + B + n*(n + e))
    sl_dir = Path(sl_dir)
    prices = prices or load_prices()
    agents = agent_records(sl_dir)
    plan = None
    if plan_path:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import plan_tools as pt
        plan = pt.Plan.load(str(plan_path))
    recs: dict[str, dict] = {}

    def rec(tid):
        return recs.setdefault(tid, {"task": tid, "builds": [], "fixes": []})

    for a in agents:
        fixer = bool(FIXER.search(a["type"] + " " + a["desc"]))
        segs = a["segments"]
        if not segs:  # no transcript: one segment from launch and stop times
            tid = a["hint"][0] if a["hint"] else None
            if tid:
                st = (a["start_ms"] or 0) / 1000.0
                en = (a["stop_ms"] or a["start_ms"] or 0) / 1000.0
                segs = [{"tid": tid, "tok": _empty(), "calls": set(), "first": st, "last": en, "model": a["model"], "text": a["last"]}]
        for i, s in enumerate(segs):
            if not s["tid"]:
                continue
            fam = family(s["model"]) or family(a["model"]) or family(a["type"]) or "sonnet"
            # the transcript's own first and last message times; the hook times (launch can be logged after the
            # agent started, even after its stop) only when the transcript has none
            st = s["first"] if s["first"] is not None else (a["start_ms"] or 0) / 1000.0
            en = s["last"] if s["last"] is not None else (a["stop_ms"] or a["start_ms"] or 0) / 1000.0
            en = max(en, st)  # never a negative duration
            text = s["text"] or (a["last"] if i == len(segs) - 1 else "")
            (rec(s["tid"])["fixes" if fixer else "builds"]).append(
                {"agent": a["agent"], "type": a["type"], "fam": fam, "tok": s["tok"], "calls": len(s["calls"]),
                 "start": st, "end": en, "text": text, "complete": a["complete"]})
    starts = [b["start"] for x in recs.values() for b in x["builds"] + x["fixes"]]
    t0 = min(starts) if starts else 0.0  # run time zero = the earliest agent start
    incidents = read_jsonl(sl_dir / "incidents.jsonl")
    fix_notes = [p.read_text(encoding="utf-8", errors="replace") for p in sorted((sl_dir / "fixes").glob("*.md"))] if (sl_dir / "fixes").is_dir() else []
    from_plan = {t.id: t for t in plan.tasks} if plan else {}
    out = []
    for tid in sorted(recs, key=lambda x: [int(n) for n in re.findall(r"\d+", x)]):
        r = recs[tid]
        builds, fixes = r["builds"], r["fixes"]
        tok = _empty()
        cost = 0.0
        for b in builds:
            for k in tok:
                tok[k] += b["tok"][k]
            cost += cost_of(b["tok"], prices.get(b["fam"]) or prices["sonnet"])
        last = builds[-1] if builds else None
        sm = STATUS.search(last["text"]) if last else None
        status = sm.group(1).upper() if sm else None
        rw_s = sum(f["end"] - f["start"] for f in fixes)
        rw_c = sum(cost_of(f["tok"], prices.get(f["fam"]) or prices["sonnet"]) for f in fixes)
        misses = [i for i in incidents if re.search(rf"kind-miss\s+{tid}\b", str(i.get("context", "")))]
        notes = [n for n in fix_notes if re.search(rf"\bF-{tid}-\d+|\b{tid}\b", n)]
        reworked = bool(fixes or misses or notes)
        task = from_plan.get(tid)
        feats = task.explicit_features if task else {}
        row = {"task": tid, "kind": feats.get("kind", "design"), "components": feats.get("components"), "loc": feats.get("loc"),
               "tests": feats.get("tests"), "model": (last["fam"] if last else None), "agent": last["agent"] if last else None,
               "agent_type": last["type"] if last else None, "agent_model_used": last["fam"] if last else None,
               "dispatches": len(builds), "start_s": round(min(b["start"] for b in builds) - t0, 1) if builds else None,
               "end_s": round(max(b["end"] for b in builds) - t0, 1) if builds else None,
               "duration_s": round(sum(b["end"] - b["start"] for b in builds), 1) if builds else None,
               "tokens": {"in": tok["input"], "out": tok["output"], "cache_read": tok["cache_read"],
                          "cache_write": tok["cache_write_5m"] + tok["cache_write_1h"], "cache_write_5m": tok["cache_write_5m"],
                          "cache_write_1h": tok["cache_write_1h"]},
               "cost_usd": round(cost, 6), "tool_calls": sum(b["calls"] for b in builds), "status": status,
               "complete": bool(last and last["complete"]), "rework_dispatches": len(fixes), "kind_miss_incidents": len(misses),
               "fix_notes": len(notes), "rework_s": round(rw_s, 1), "rework_cost": round(rw_c, 6)}
        if last is None or status is None:
            row["first_time_pass"] = None
        else:
            row["first_time_pass"] = status in ("DONE", "DONE_WITH_CONCERNS") and not (RED.search(last["text"]) and status != "DONE") and not reworked
        row["done_s"] = round(max([row["end_s"]] + [f["end"] - t0 for f in fixes]), 1) if builds else None
        out.append(row)
    timing(out, plan)
    return out


def timing(rows: list, plan) -> None:  # O(n*(n + e))
    """slack_s, start_lag_s and on_critical_path from the actual start and finish times (as bench/mechanical.py)."""
    graph = {}
    if plan is not None:
        import plan_tools as pt
        import planfile
        try:
            graph = pt.parse_taskgraph(plan)
        except (planfile.PlanError, ValueError, KeyError):
            graph = {}
    known = {r["task"]: r for r in rows if r["start_s"] is not None and r["done_s"] is not None}
    deps = {t: [d for d in graph.get(t, []) if d in known] for t in known}
    succ: dict[str, list] = {t: [] for t in known}
    for t, ds in deps.items():
        for d in ds:
            succ[d].append(t)
    end = max((r["done_s"] for r in known.values()), default=0.0)
    for t, r in known.items():
        r["start_lag_s"] = round(max(0.0, r["start_s"] - max(known[d]["done_s"] for d in deps[t])), 1) if deps[t] else None
    for t, r in known.items():
        gaps = [known[s]["start_s"] - r["done_s"] - (known[s]["start_lag_s"] or 0.0) for s in succ[t]]
        r["slack_s"] = round(max(0.0, min(gaps) if gaps else end - r["done_s"]), 1)
        r["on_critical_path"] = False
    todo = [t for t in known if known[t]["done_s"] >= end - 1.0]
    while todo:
        cur = todo.pop()
        if known[cur]["on_critical_path"]:
            continue
        known[cur]["on_critical_path"] = True
        if deps[cur]:
            top = max(known[d]["done_s"] for d in deps[cur])
            todo += [d for d in deps[cur] if known[d]["done_s"] >= top - 1.0]
    for r in rows:
        if r["task"] not in known:
            r.update({"start_lag_s": None, "slack_s": None, "on_critical_path": None})


def main(argv=None):  # O(M + B)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dir", default=state.DIR_NAME, help=f"the {state.DIR_NAME} folder itself (not the project root)")
    ap.add_argument("--plan")
    ap.add_argument("--task")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--all", action="store_true", help="with --json: {coordinator, tasks} instead of the task list")
    ap.add_argument("--reconcile", metavar="STREAM_LOG", help="compare coordinator + agents against the stream log total_cost_usd")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    if a.reconcile:
        print(json.dumps(reconcile(a.dir, a.reconcile), indent=1))
        return 0
    if not (Path(a.dir) / "metrics.jsonl").is_file():
        print(f"metrics: no metrics.jsonl in {a.dir} (--dir is the {state.DIR_NAME} folder itself)", file=sys.stderr)
    rows = task_records(a.dir, a.plan)
    if a.task:
        rows = [r for r in rows if r["task"] == a.task]
    co = coordinator_record(a.dir)
    if a.json:
        print(json.dumps({"coordinator": co, "tasks": rows} if a.all else rows, indent=1))
    else:
        if co:
            print(f"coordinator {co['turns']} turns ${co['cost_usd']:.3f} context median {co['context_median']} peak {co['context_peak']}")
        for r in rows:
            print(f"{r['task']} {r['model'] or '?'} {r['duration_s']}s ${r['cost_usd']:.3f} tools={r['tool_calls']} "
                  f"status={r['status']} first_time_pass={r['first_time_pass']}")
        if not rows:
            print(f"metrics: no task records (no {state.DIR_NAME}/metrics.jsonl or no agent mapped to a task)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
