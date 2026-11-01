#!/usr/bin/env python3
"""Resolve a host profile (e.g. Cursor) once per run into `.superlite/host-models.json`.

    host_models.py resolve [--host cursor] [--plan P] [--force]
    host_models.py show
    host_models.py job-for --tier haiku|--agent TYPE

Resolve picks the first candidate per job from config/hosts/<host>.json and writes
`.superlite/host-models.json`. Without --force, an existing file is left unchanged.
Host is taken from --host, else plan `**Execution:** host=...`, else $SUPERLITE_HOST.
"""
# Complexity: H = hosts file size, J = jobs
import argparse
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import state  # noqa: E402

CFG = HERE.parent / "config" / "hosts"
OUT_NAME = "host-models.json"


def superlite_dir(plan=None) -> Path:
    if plan:
        p = Path(plan).resolve().parent
        for _ in range(8):
            if (p / state.DIR_NAME).is_dir() or (p / ".git").exists():
                d = p / state.DIR_NAME
                d.mkdir(parents=True, exist_ok=True)
                return d
            if p.parent == p:
                break
            p = p.parent
    d = Path(state.DIR_NAME)
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_host_cfg(host: str, root=None) -> dict:  # O(H)
    """Shipped host profile. Overlay `hosts.<host>.<job>.candidates` replaces that job only."""
    path = CFG / f"{host}.json"
    if not path.is_file():
        raise FileNotFoundError(f"host profile not found: {path}")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    root = Path(root).resolve() if root is not None else Path(".").resolve()
    scripts = str(Path(__file__).resolve().parent)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import project_config
    over = project_config.load(root).get("hosts")
    host_over = over.get(host) if isinstance(over, dict) else None
    if not isinstance(host_over, dict):
        return cfg
    jobs = cfg.setdefault("jobs", {})
    for job, spec in host_over.items():
        if not isinstance(spec, dict) or not isinstance(spec.get("candidates"), list):
            continue
        current = dict(jobs.get(job) or {})
        current["candidates"] = [str(c) for c in spec["candidates"]]
        jobs[job] = current
    return cfg


def parse_execution(plan_text: str) -> dict:  # O(L)
    """Parse `**Execution:** host=cursor, parallel=multitask, swarm_by=tier` into a dict."""
    out = {}
    for ln in plan_text.splitlines():
        if ln.strip().startswith("```"):
            continue
        m = re.match(r"^\*\*Execution:\*\*\s*(.*)$", ln)
        if not m:
            continue
        for part in m.group(1).split(","):
            part = part.strip()
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            out[k.strip().lower()] = v.strip().lower()
        break
    return out


def detect_host(plan=None, explicit=None) -> str | None:  # O(L)
    if explicit:
        return explicit
    env = (os.environ.get("SUPERLITE_HOST") or "").strip().lower()
    if env:
        return env
    if plan and Path(plan).is_file():
        ex = parse_execution(Path(plan).read_text(encoding="utf-8"))
        if ex.get("host"):
            return ex["host"]
    return None


def resolve(host: str, plan=None, force=False) -> dict:  # O(H + J)
    sl = superlite_dir(plan)
    out_path = sl / OUT_NAME
    if out_path.is_file() and not force:
        return json.loads(out_path.read_text(encoding="utf-8"))
    cfg = load_host_cfg(host)
    jobs = {}
    for name, spec in (cfg.get("jobs") or {}).items():
        cands = list(spec.get("candidates") or ["inherit"])
        jobs[name] = cands[0] if cands else "inherit"
    data = {
        "host": host,
        "price_unit": cfg.get("price_unit", "est"),
        "dispatch": cfg.get("dispatch", "Task"),
        "parallel": cfg.get("parallel", "multitask"),
        "swarm_by": cfg.get("swarm_by", "tier"),
        "tier_to_job": dict(cfg.get("tier_to_job") or {}),
        "agent_to_job": dict(cfg.get("agent_to_job") or {}),
        "jobs": jobs,
    }
    if plan and Path(plan).is_file():
        ex = parse_execution(Path(plan).read_text(encoding="utf-8"))
        if ex:
            data["execution"] = ex
    out_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return data


def load_resolved(plan=None) -> dict | None:  # O(file)
    p = superlite_dir(plan) / OUT_NAME
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def job_for(resolved: dict, *, tier=None, agent=None) -> str | None:
    if agent:
        m = resolved.get("agent_to_job") or {}
        if agent in m:
            return m[agent]
        short = agent.split(":")[-1] if ":" in agent else agent
        if short in m:
            return m[short]
    if tier:
        return (resolved.get("tier_to_job") or {}).get(tier)
    return None


def slug_for(resolved: dict, job: str | None) -> str:
    if not job:
        return "inherit"
    return (resolved.get("jobs") or {}).get(job, "inherit")


def parse_dispatch_line(line: str) -> dict | None:  # O(w)
    """Parse one `superlite-cli dispatch` stdout line into tasks/decision/tier/agent."""
    line = line.strip()
    if not line or line.startswith("evict=") or line.startswith("budget:") or line.startswith("SWARM"):
        return None
    parts = line.split()
    if len(parts) < 2:
        return None
    tasks = parts[0]
    rest = parts[1:]
    decision = "fresh"
    if rest[0].startswith("agent="):
        decision = "resume"
    elif rest[0] == "fresh" or rest[0] == "wave0":
        decision = "fresh"
    elif rest[0] == "poll" or rest[0].startswith("poll"):
        decision = "poll"
    tier_m = re.search(r"\btier=(\S+)", line)
    agent_ms = re.findall(r"\bagent=(\S+)", line)
    tier = tier_m.group(1) if tier_m else "sonnet"
    # resume: first agent= is the pool id; fresh: last agent= is the type
    agent = (agent_ms[0] if decision == "resume" else (agent_ms[-1] if agent_ms else None))
    return {"tasks": tasks, "decision": decision, "tier": tier, "agent": agent}


def swarm_lines(dispatch_lines, resolved: dict) -> list[str]:  # O(R)
    """Group dispatch stdout lines into a SWARM block by tier. Multitask when agents>1."""
    by_tier: dict[str, list[dict]] = {}
    for raw in dispatch_lines:
        parsed = parse_dispatch_line(raw)
        if not parsed:
            continue
        job = job_for(resolved, tier=parsed["tier"], agent=parsed["agent"] if parsed["decision"] == "fresh" else None)
        if parsed["decision"] == "fresh" and parsed["agent"]:
            job = job_for(resolved, agent=parsed["agent"]) or job
        job = job or "build"
        parsed["job"] = job
        parsed["slug"] = slug_for(resolved, job)
        by_tier.setdefault(parsed["tier"], []).append(parsed)
    if not by_tier:
        return []
    out = [
        "SWARM host=%s parallel=%s swarm_by=%s"
        % (resolved.get("host", "?"), resolved.get("parallel", "multitask"), resolved.get("swarm_by", "tier"))
    ]
    for tier, items in by_tier.items():
        n = len(items)
        mode = "multitask" if n > 1 else "task"
        out.append(
            f"  tier={tier} job={items[0]['job']} slug={items[0]['slug']} mode={mode} agents={n}"
        )
        for i in items:
            out.append(f"    {i['tasks']} {i['decision']} agent={i['agent'] or '-'}")
    return out


def main(argv=None):  # O(H + J)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("resolve")
    r.add_argument("--host")
    r.add_argument("--plan")
    r.add_argument("--force", action="store_true")
    sub.add_parser("show")
    j = sub.add_parser("job-for")
    j.add_argument("--tier")
    j.add_argument("--agent")
    j.add_argument("--plan")
    a = ap.parse_args(argv)
    if a.cmd == "resolve":
        host = detect_host(a.plan, a.host)
        if not host:
            print("host_models: no host (pass --host, set SUPERLITE_HOST, or **Execution:** host=...)", file=sys.stderr)
            return 2
        data = resolve(host, a.plan, force=a.force)
        print(f"host-models: {data['host']} jobs={','.join(f'{k}={v}' for k, v in data['jobs'].items())}")
        return 0
    if a.cmd == "show":
        data = load_resolved()
        if not data:
            print("host-models: none")
            return 0
        print(json.dumps(data, indent=2))
        return 0
    if a.cmd == "job-for":
        data = load_resolved(getattr(a, "plan", None))
        if not data:
            print("job: none")
            return 0
        job = job_for(data, tier=a.tier, agent=a.agent)
        print(f"job={job or 'none'} slug={slug_for(data, job)}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
