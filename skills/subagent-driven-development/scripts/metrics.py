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
# Complexity notation:
#   M = metrics lines, A = agents, B = bytes of agent transcripts, n = tasks, e = dependency edges
import argparse  # O(1)
import json  # O(1)
import re  # O(1)
import sys  # O(1)
from datetime import datetime  # O(1)
from pathlib import Path  # O(1)

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"  # O(1)
FAMILIES = ("haiku", "sonnet", "opus")  # O(1)
TID = re.compile(r"\bT(\d+[a-z]?)\b")  # O(1)
STATUS = re.compile(r"\bStatus:\s*\**\s*(DONE_WITH_CONCERNS|DONE|NEEDS_CONTEXT|BLOCKED)\b", re.I)  # O(1)
RED = re.compile(r"\b(?:tests?|suite)\b[^\n]{0,40}\b(?:red|failing|failed)\b|\b\d+\s+failed\b", re.I)  # O(1)
CHARS_PER_TOKEN = 3.5  # output_tokens in a transcript is an early snapshot of a streamed message; text length bounds it below


def msg_chars(msg) -> int:  # O(content)
    """Characters the message produced: text, thinking and tool-call input (one message id spans several entries)."""
    n = 0  # O(1)
    for b in msg.get("content") or []:  # O(content)
        if not isinstance(b, dict):  # O(1)
            continue  # O(1)
        n += len(b.get("text") or "") + len(b.get("thinking") or "")  # O(1)
        if b.get("type") == "tool_use":  # O(1)
            n += len(json.dumps(b.get("input") or {}))  # O(1)
    return n  # O(1)


FIXER = re.compile(r"fixer|\bfix(?:es|ed)?\b|F-T\d+-\d+|re-?dispatch|rework", re.I)  # O(1)


def family(model) -> str | None:  # O(1)
    m = str(model or "").lower()  # O(1)
    return next((f for f in FAMILIES if f in m), None)  # O(1)


def load_prices(path=None) -> dict:  # O(file)
    cfg = json.loads(Path(path or CONFIG_DIR / "models.json").read_text(encoding="utf-8"))  # O(file)
    if path is None:  # an explicit file wins over superlite.json | O(1)
        scripts = str(Path(__file__).resolve().parent)  # O(1)
        if scripts not in sys.path:  # O(1)
            sys.path.insert(0, scripts)  # O(1)
        import project_config  # O(1)
        cfg = project_config.apply_project_prices(cfg, Path(".").resolve())  # O(M)
    per = float(cfg.get("per_tokens") or 1_000_000)  # O(1)
    mult = cfg.get("cache_write_multipliers") or {}  # O(1)
    out = {}  # O(1)
    for name, m in (cfg.get("models") or {}).items():  # O(models)
        w5 = m.get("cache_write_5m", m["input"] * mult.get("5m", 1.25))  # O(1)
        w1 = m.get("cache_write_1h", m["input"] * mult.get("1h", 2.0))  # O(1)
        out[name] = tuple(float(x) / per for x in (m["input"], m["output"], w5, w1, m["cache_read"]))  # O(1)
    return out  # O(1)


def cost_of(tok: dict, price: tuple) -> float:  # O(1)
    pi, po, w5, w1, pr = price  # O(1)
    return (tok["input"] * pi + tok["output"] * po + tok["cache_write_5m"] * w5 + tok["cache_write_1h"] * w1  # O(1)
            + tok["cache_read"] * pr)


def _ts(e) -> float | None:  # O(1)
    s = e.get("timestamp")  # O(1)
    try:  # O(1)
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() if isinstance(s, str) else None  # O(1)
    except ValueError:  # O(1)
        return None  # O(1)


def _empty() -> dict:  # O(1)
    return {"input": 0, "output": 0, "cache_read": 0, "cache_write_5m": 0, "cache_write_1h": 0}  # O(1)


def _text_of(content) -> str:  # O(content)
    if isinstance(content, str):  # O(1)
        return content  # O(1)
    return " ".join(b.get("text") or "" for b in content or [] if isinstance(b, dict) and b.get("type") == "text")  # O(content)


def read_jsonl(path) -> list:  # O(bytes)
    out = []  # O(1)
    try:  # O(1)
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()  # O(bytes)
    except OSError:  # O(1)
        return out  # O(1)
    for ln in lines:  # O(lines)
        try:  # O(1)
            e = json.loads(ln)  # O(w)
        except ValueError:  # O(1)
            continue  # O(1)
        if isinstance(e, dict):  # O(1)
            out.append(e)  # O(1)
    return out  # O(1)


def transcript_segments(path, tasks_hint: list) -> list:  # O(bytes of one transcript)
    """[{tid, tokens, tool_calls, first, last, model, text}] split at user messages that name a Tn.
    Output tokens are the message's own count (the maximum seen per message id)."""
    segs: list[dict] = []  # O(1)
    cur = None  # O(1)
    seen: dict[str, dict] = {}  # message id -> its segment record | O(1)
    for e in read_jsonl(path):  # O(entries)
        msg = e.get("message") or {}  # O(1)
        t = _ts(e)  # O(1)
        if e.get("type") == "user" and not any(isinstance(b, dict) and b.get("type") == "tool_result"  # O(content)
                                                for b in (msg.get("content") if isinstance(msg.get("content"), list) else [])):
            m = TID.search(_text_of(msg.get("content"))[:400])  # O(400)
            if m and (cur is None or cur["tid"] != "T" + m.group(1)):  # O(1)
                cur = {"tid": "T" + m.group(1), "tok": _empty(), "calls": set(), "first": t, "last": t, "model": "", "text": ""}  # O(1)
                segs.append(cur)  # O(1)
        if e.get("type") != "assistant":  # O(1)
            continue  # O(1)
        if cur is None:  # work before any task message (the prime): belongs to the first task | O(1)
            cur = {"tid": tasks_hint[0] if tasks_hint else None, "tok": _empty(), "calls": set(), "first": t, "last": t, "model": "", "text": ""}  # O(1)
            segs.append(cur)  # O(1)
        mid = msg.get("id") or f"anon{len(seen)}"  # O(1)
        u = msg.get("usage") or {}  # O(1)
        cc = u.get("cache_creation") or {}  # O(1)
        w1, w5 = cc.get("ephemeral_1h_input_tokens"), cc.get("ephemeral_5m_input_tokens")  # O(1)
        if w1 is None and w5 is None:  # O(1)
            w5, w1 = u.get("cache_creation_input_tokens") or 0, 0  # O(1)
        rec = {"input": u.get("input_tokens") or 0, "output": u.get("output_tokens") or 0,  # O(1)
               "cache_read": u.get("cache_read_input_tokens") or 0, "cache_write_5m": w5 or 0, "cache_write_1h": w1 or 0}
        prev = seen.get(mid)  # O(1)
        if prev is not None:
            prev["chars"] += msg_chars(msg)  # O(content)  # a repeated message id (streamed snapshots): keep the largest | O(1)
            for k, v in rec.items():  # O(5)
                if v > prev["rec"][k]:  # O(1)
                    prev["seg"]["tok"][k] += v - prev["rec"][k]  # O(1)
                    prev["rec"][k] = v  # O(1)
        else:  # O(1)
            seen[mid] = {"rec": dict(rec), "seg": cur, "chars": msg_chars(msg)}  # O(content)
            for k, v in rec.items():  # O(5)
                cur["tok"][k] += v  # O(1)
        for b in msg.get("content") or []:  # O(content)
            if isinstance(b, dict) and b.get("type") == "tool_use":  # O(1)
                cur["calls"].add(b.get("id") or f"{mid}:{len(cur['calls'])}")  # O(1)
        tx = _text_of(msg.get("content"))  # O(content)
        if tx:  # O(1)
            cur["text"] = tx  # O(1)
        cur["model"] = msg.get("model") or cur["model"]  # O(1)
        if t is not None:  # O(1)
            cur["first"] = cur["first"] if cur["first"] is not None else t  # O(1)
            cur["last"] = t  # O(1)
    for v in seen.values():  # top up each message's output tokens from its text length | O(messages)
        est = int(v["chars"] / CHARS_PER_TOKEN)  # O(1)
        if est > v["rec"]["output"]:  # O(1)
            v["seg"]["tok"]["output"] += est - v["rec"]["output"]  # O(1)
    return segs  # O(1)


def agent_records(sl_dir: Path) -> list:  # O(M + B)
    """One record per agent: launch/stop joined, its transcript segments."""
    launches: dict[str, dict] = {}  # O(1)
    stops: dict[str, list] = {}  # O(1)
    for e in read_jsonl(sl_dir / "metrics.jsonl"):  # O(M)
        aid = e.get("agent_id")  # O(1)
        if not aid:  # O(1)
            continue  # O(1)
        if e.get("ev") == "launch":  # O(1)
            launches.setdefault(aid, e)  # O(1)
        elif e.get("ev") == "stop":  # O(1)
            stops.setdefault(aid, []).append(e)  # O(1)
    pool: dict[str, list] = {}  # O(1)
    try:  # O(1)
        for ag in json.loads((sl_dir / "pool.json").read_text(encoding="utf-8")).get("agents", []):  # O(a)
            pool[str(ag.get("id"))] = [t for t in ag.get("tasks") or [] if isinstance(t, str)]  # O(tasks)
    except (OSError, ValueError, AttributeError):  # O(1)
        pass  # O(1)
    out = []  # O(1)
    for aid in sorted(set(launches) | set(stops)):  # O(A)
        la, sts = launches.get(aid, {}), stops.get(aid, [])  # O(1)
        desc = la.get("desc") or ""  # O(1)
        hint = pool.get(aid) or ([ "T" + TID.search(desc).group(1)] if TID.search(desc) else [])  # O(1)
        segs = []  # O(1)
        for s in sts:  # O(stops)
            if s.get("transcript"):  # O(1)
                segs = transcript_segments(s["transcript"], hint)  # O(B)
                break  # O(1)
        out.append({"agent": aid, "type": la.get("type") or (sts[0].get("type") if sts else ""), "model": la.get("model") or "",
                    "desc": desc, "start_ms": la.get("t"), "stop_ms": max((s.get("t") or 0 for s in sts), default=None) if sts else None,
                    "last": report_of(sts[-1]) if sts else "", "segments": segs, "hint": hint, "complete": bool(sts),
                    "first_abs": min((x["first"] for x in segs if x["first"] is not None), default=None)})  # O(1)
    return out  # O(A)


def report_of(stop_event, limit=400) -> str:  # O(transcript)
    """What an agent last told its caller. A subagent reports through the `SubagentHandback` tool, so the stop event's `last` is often
    empty: then read the end of the agent's transcript for the handback message (else its last text). At most `limit` characters."""
    t = ((stop_event or {}).get("last") or "").strip()  # O(1)
    if t:  # O(1)
        return t[:limit]  # O(1)
    try:  # O(1)
        lines = Path((stop_event or {}).get("transcript") or "").read_text(encoding="utf-8", errors="replace").splitlines()  # O(transcript)
    except OSError:  # O(1)
        return ""  # O(1)
    for ln in reversed(lines):  # O(lines)
        if '"assistant"' not in ln:  # O(len)
            continue  # O(1)
        try:  # O(1)
            e = json.loads(ln)  # O(len)
        except ValueError:  # O(1)
            continue  # O(1)
        content = (e.get("message") or {}).get("content") if isinstance(e, dict) and e.get("type") == "assistant" else None  # O(1)
        for b in (content if isinstance(content, list) else [])[::-1]:  # O(blocks)
            if not isinstance(b, dict):  # O(1)
                continue  # O(1)
            if b.get("type") == "tool_use" and b.get("name") == "SubagentHandback":  # O(1)
                return str((b.get("input") or {}).get("message") or "")[:limit]  # O(1)
            if b.get("type") == "text" and (b.get("text") or "").strip():  # O(1)
                return b["text"].strip()[:limit]  # O(1)
    return ""  # O(1)


def session_transcript(sl_dir: Path) -> str | None:  # O(M)
    """The coordinator's (main session) transcript: a `session_transcript`/`transcript_path` key on any metrics line, else
    derived from an agent transcript path `<dir>/<session>/subagents/agent-X.jsonl` -> `<dir>/<session>.jsonl`."""
    lines = read_jsonl(sl_dir / "metrics.jsonl")  # O(M)
    for e in lines:  # O(M)
        for k in ("session_transcript", "transcript_path"):  # O(1)
            if e.get(k) and Path(e[k]).is_file():  # O(1)
                return e[k]  # O(1)
    for e in lines:  # O(M)
        pp = Path(e.get("transcript") or "")  # O(1)
        if pp.parent.name == "subagents":  # O(1)
            main = pp.parent.parent.with_suffix(".jsonl")  # O(1)
            if main.is_file():  # O(1)
                return str(main)  # O(1)
    return None  # O(1)


def coordinator_record(sl_dir, prices=None) -> dict | None:  # O(bytes of the main transcript)
    """The coordinator as one line: usage per assistant message (deduplicated by message id), turns, context peak and
    median (input + cache read + cache write per message), cost by model, and its first-to-last message time."""
    sl_dir = Path(sl_dir)  # O(1)
    path = session_transcript(sl_dir)  # O(M)
    if not path:  # O(1)
        return None  # O(1)
    prices = prices or load_prices()  # O(file)
    seen: dict[str, dict] = {}  # O(1)
    first = last = None  # O(1)
    for e in read_jsonl(path):  # O(entries)
        if e.get("isSidechain") or e.get("type") != "assistant":  # subagent entries are counted per agent | O(1)
            continue  # O(1)
        msg = e.get("message") or {}  # O(1)
        u = msg.get("usage") or {}  # O(1)
        t = _ts(e)  # O(1)
        if t is not None:  # O(1)
            first = t if first is None else first  # O(1)
            last = t  # O(1)
        mid = msg.get("id") or f"anon{len(seen)}"  # O(1)
        cc = u.get("cache_creation") or {}  # O(1)
        w1, w5 = cc.get("ephemeral_1h_input_tokens"), cc.get("ephemeral_5m_input_tokens")  # O(1)
        if w1 is None and w5 is None:  # O(1)
            w5, w1 = u.get("cache_creation_input_tokens") or 0, 0  # O(1)
        rec = {"input": u.get("input_tokens") or 0, "output": u.get("output_tokens") or 0,  # O(1)
               "cache_read": u.get("cache_read_input_tokens") or 0, "cache_write_5m": w5 or 0, "cache_write_1h": w1 or 0,
               "model": msg.get("model") or ""}
        old = seen.get(mid)  # O(1)
        if old is None:  # O(1)
            rec["chars"] = msg_chars(msg)  # O(content)
            seen[mid] = rec  # O(1)
        else:
            old["chars"] += msg_chars(msg)  # O(content)  # a repeated snapshot: keep the largest of each counter | O(1)
            for k in ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h"):  # O(5)
                old[k] = max(old[k], rec[k])  # O(1)
            old["model"] = rec["model"] or old["model"]  # O(1)
    tok, by_model, ctx = _empty(), {}, []  # O(1)
    for rec in seen.values():  # O(turns)
        rec["output"] = max(rec["output"], int(rec["chars"] / CHARS_PER_TOKEN))  # O(1)
        fam = family(rec["model"]) or "sonnet"  # O(1)
        t5 = {k: rec[k] for k in tok}  # O(5)
        for k in tok:  # O(5)
            tok[k] += t5[k]  # O(1)
        by_model[fam] = by_model.get(fam, 0.0) + cost_of(t5, prices.get(fam) or prices["sonnet"])  # O(1)
        ctx.append(rec["input"] + rec["cache_read"] + rec["cache_write_5m"] + rec["cache_write_1h"])  # O(1)
    ctx.sort()  # O(turns log turns)
    return {"turns": len(seen), "tokens": {"in": tok["input"], "out": tok["output"], "cache_read": tok["cache_read"],  # O(1)
                                           "cache_write": tok["cache_write_5m"] + tok["cache_write_1h"]},
            "cost_usd": round(sum(by_model.values()), 6), "cost_by_model": {k: round(v, 6) for k, v in sorted(by_model.items())},
            "context_peak": ctx[-1] if ctx else 0, "context_median": ctx[len(ctx) // 2] if ctx else 0,
            "duration_s": round(last - first, 1) if first is not None and last is not None else None, "transcript": path}


def agents_cost(sl_dir, prices=None) -> float:  # O(M + B)
    """Cost of every agent segment in metrics.jsonl, including ones not mapped to a plan task."""
    prices = prices or load_prices()  # O(file)
    return sum(cost_of(s["tok"], prices.get(family(s["model"]) or family(a["type"]) or "sonnet") or prices["sonnet"])  # O(segments)
               for a in agent_records(Path(sl_dir)) for s in a["segments"])  # O(M + B)


def reconcile(sl_dir, stream_log, prices=None) -> dict:  # O(M + B + log)
    """Coordinator + agents from the transcripts against the stream-json log's cumulative total_cost_usd."""
    truth = None  # O(1)
    for e in read_jsonl(stream_log):  # O(log)
        if isinstance(e.get("total_cost_usd"), (int, float)):  # O(1)
            truth = float(e["total_cost_usd"])  # the last (cumulative) value wins | O(1)
    co = coordinator_record(sl_dir, prices)  # O(B)
    ag = round(agents_cost(sl_dir, prices), 6)  # O(M + B)
    mine = round((co["cost_usd"] if co else 0.0) + ag, 6)  # O(1)
    diff = None if truth is None else round(mine - truth, 6)  # O(1)
    return {"metrics_usd": mine, "coordinator_usd": co["cost_usd"] if co else None, "agents_usd": ag,  # O(1)
            "stream_usd": truth, "diff_usd": diff, "diff_pct": None if not truth else round(100.0 * diff / truth, 2),
            "higher": None if diff is None else ("metrics" if diff > 0 else "stream" if diff < 0 else "equal")}


def task_records(sl_dir, plan_path=None, prices=None) -> list:  # O(M + B + n*(n + e))
    sl_dir = Path(sl_dir)  # O(1)
    prices = prices or load_prices()  # O(file)
    agents = agent_records(sl_dir)  # O(M + B)
    plan = None  # O(1)
    if plan_path:  # O(1)
        sys.path.insert(0, str(Path(__file__).resolve().parent))  # O(1)
        import plan_tools as pt  # O(1)
        plan = pt.Plan.load(str(plan_path))  # O(L)
    recs: dict[str, dict] = {}  # O(1)

    def rec(tid):  # O(1)
        return recs.setdefault(tid, {"task": tid, "builds": [], "fixes": []})  # O(1)

    for a in agents:  # O(A)
        fixer = bool(FIXER.search(a["type"] + " " + a["desc"]))  # O(1)
        segs = a["segments"]  # O(1)
        if not segs:  # no transcript: one segment from launch and stop times | O(1)
            tid = a["hint"][0] if a["hint"] else None  # O(1)
            if tid:  # O(1)
                st = (a["start_ms"] or 0) / 1000.0  # O(1)
                en = (a["stop_ms"] or a["start_ms"] or 0) / 1000.0  # O(1)
                segs = [{"tid": tid, "tok": _empty(), "calls": set(), "first": st, "last": en, "model": a["model"], "text": a["last"]}]  # O(1)
        for i, s in enumerate(segs):  # O(segments)
            if not s["tid"]:  # O(1)
                continue  # O(1)
            fam = family(s["model"]) or family(a["model"]) or family(a["type"]) or "sonnet"  # O(1)
            # the transcript's own first and last message times; the hook times (launch can be logged after the
            # agent started, even after its stop) only when the transcript has none | O(1)
            st = s["first"] if s["first"] is not None else (a["start_ms"] or 0) / 1000.0  # O(1)
            en = s["last"] if s["last"] is not None else (a["stop_ms"] or a["start_ms"] or 0) / 1000.0  # O(1)
            en = max(en, st)  # never a negative duration | O(1)
            text = s["text"] or (a["last"] if i == len(segs) - 1 else "")  # O(1)
            (rec(s["tid"])["fixes" if fixer else "builds"]).append(
                {"agent": a["agent"], "type": a["type"], "fam": fam, "tok": s["tok"], "calls": len(s["calls"]),
                 "start": st, "end": en, "text": text, "complete": a["complete"]})  # O(1)
    starts = [b["start"] for x in recs.values() for b in x["builds"] + x["fixes"]]  # O(n)
    t0 = min(starts) if starts else 0.0  # run time zero = the earliest agent start | O(n)
    incidents = read_jsonl(sl_dir / "incidents.jsonl")  # O(I)
    fix_notes = [p.read_text(encoding="utf-8", errors="replace") for p in sorted((sl_dir / "fixes").glob("*.md"))] if (sl_dir / "fixes").is_dir() else []  # O(notes)
    from_plan = {t.id: t for t in plan.tasks} if plan else {}  # O(n)
    out = []  # O(1)
    for tid in sorted(recs, key=lambda x: [int(n) for n in re.findall(r"\d+", x)]):  # O(n log n)
        r = recs[tid]  # O(1)
        builds, fixes = r["builds"], r["fixes"]  # O(1)
        tok = _empty()  # O(1)
        cost = 0.0  # O(1)
        for b in builds:  # O(builds)
            for k in tok:  # O(5)
                tok[k] += b["tok"][k]  # O(1)
            cost += cost_of(b["tok"], prices.get(b["fam"]) or prices["sonnet"])  # O(1)
        last = builds[-1] if builds else None  # O(1)
        sm = STATUS.search(last["text"]) if last else None  # O(1)
        status = sm.group(1).upper() if sm else None  # O(1)
        rw_s = sum(f["end"] - f["start"] for f in fixes)  # O(fixes)
        rw_c = sum(cost_of(f["tok"], prices.get(f["fam"]) or prices["sonnet"]) for f in fixes)  # O(fixes)
        misses = [i for i in incidents if re.search(rf"kind-miss\s+{tid}\b", str(i.get("context", "")))]  # O(I)
        notes = [n for n in fix_notes if re.search(rf"\bF-{tid}-\d+|\b{tid}\b", n)]  # O(notes)
        reworked = bool(fixes or misses or notes)  # O(1)
        task = from_plan.get(tid)  # O(1)
        feats = task.explicit_features if task else {}  # O(k*s)
        row = {"task": tid, "kind": feats.get("kind", "design"), "components": feats.get("components"), "loc": feats.get("loc"),  # O(1)
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
        if last is None or status is None:  # O(1)
            row["first_time_pass"] = None  # O(1)
        else:  # O(1)
            row["first_time_pass"] = status in ("DONE", "DONE_WITH_CONCERNS") and not (RED.search(last["text"]) and status != "DONE") and not reworked  # O(1)
        row["done_s"] = round(max([row["end_s"]] + [f["end"] - t0 for f in fixes]), 1) if builds else None  # O(fixes)
        out.append(row)  # O(1)
    timing(out, plan)  # O(n*(n + e))
    return out  # O(1)


def timing(rows: list, plan) -> None:  # O(n*(n + e))
    """slack_s, start_lag_s and on_critical_path from the actual start and finish times (as bench/mechanical.py)."""
    graph = {}  # O(1)
    if plan is not None:  # O(1)
        import plan_tools as pt  # O(1)
        try:  # O(1)
            graph = pt.parse_taskgraph(plan)  # O(L)
        except Exception:  # noqa: BLE001 | O(1)
            graph = {}  # O(1)
    known = {r["task"]: r for r in rows if r["start_s"] is not None and r["done_s"] is not None}  # O(n)
    deps = {t: [d for d in graph.get(t, []) if d in known] for t in known}  # O(n + e)
    succ: dict[str, list] = {t: [] for t in known}  # O(n)
    for t, ds in deps.items():  # O(n)
        for d in ds:  # O(deg)
            succ[d].append(t)  # O(1)
    end = max((r["done_s"] for r in known.values()), default=0.0)  # O(n)
    for t, r in known.items():  # O(n)
        r["start_lag_s"] = round(max(0.0, r["start_s"] - max(known[d]["done_s"] for d in deps[t])), 1) if deps[t] else None  # O(deg)
    for t, r in known.items():  # O(n)
        gaps = [known[s]["start_s"] - r["done_s"] - (known[s]["start_lag_s"] or 0.0) for s in succ[t]]  # O(deg)
        r["slack_s"] = round(max(0.0, min(gaps) if gaps else end - r["done_s"]), 1)  # O(deg)
        r["on_critical_path"] = False  # O(1)
    todo = [t for t in known if known[t]["done_s"] >= end - 1.0]  # O(n)
    while todo:  # O(n)
        cur = todo.pop()  # O(1)
        if known[cur]["on_critical_path"]:  # O(1)
            continue  # O(1)
        known[cur]["on_critical_path"] = True  # O(1)
        if deps[cur]:  # O(1)
            top = max(known[d]["done_s"] for d in deps[cur])  # O(deg)
            todo += [d for d in deps[cur] if known[d]["done_s"] >= top - 1.0]  # O(deg)
    for r in rows:  # O(n)
        if r["task"] not in known:  # O(1)
            r.update({"start_lag_s": None, "slack_s": None, "on_critical_path": None})  # O(1)


def main(argv=None):  # O(M + B)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])  # O(1)
    ap.add_argument("--dir", default=".superlite", help="the .superlite folder itself (not the project root)")  # O(1)
    ap.add_argument("--plan")  # O(1)
    ap.add_argument("--task")  # O(1)
    ap.add_argument("--json", action="store_true")  # O(1)
    ap.add_argument("--all", action="store_true", help="with --json: {coordinator, tasks} instead of the task list")  # O(1)
    ap.add_argument("--reconcile", metavar="STREAM_LOG", help="compare coordinator + agents against the stream log total_cost_usd")  # O(1)
    a = ap.parse_args(argv)  # O(1)
    sys.stdout.reconfigure(encoding="utf-8")  # O(1)
    if a.reconcile:  # O(1)
        print(json.dumps(reconcile(a.dir, a.reconcile), indent=1))  # O(1)
        return 0  # O(1)
    if not (Path(a.dir) / "metrics.jsonl").is_file():  # O(1)
        print(f"metrics: no metrics.jsonl in {a.dir} (--dir is the .superlite folder itself)", file=sys.stderr)  # O(1)
    rows = task_records(a.dir, a.plan)  # O(M + B)
    if a.task:  # O(1)
        rows = [r for r in rows if r["task"] == a.task]  # O(n)
    co = coordinator_record(a.dir)  # O(B)
    if a.json:  # O(1)
        print(json.dumps({"coordinator": co, "tasks": rows} if a.all else rows, indent=1))  # O(n)
    else:  # O(1)
        if co:  # O(1)
            print(f"coordinator {co['turns']} turns ${co['cost_usd']:.3f} context median {co['context_median']} peak {co['context_peak']}")  # O(1)
        for r in rows:  # O(n)
            print(f"{r['task']} {r['model'] or '?'} {r['duration_s']}s ${r['cost_usd']:.3f} tools={r['tool_calls']} "
                  f"status={r['status']} first_time_pass={r['first_time_pass']}")  # O(1)
        if not rows:  # O(1)
            print("metrics: no task records (no .superlite/metrics.jsonl or no agent mapped to a task)")  # O(1)
    return 0  # O(1)


if __name__ == "__main__":  # O(1)
    sys.exit(main())  # O(1)
