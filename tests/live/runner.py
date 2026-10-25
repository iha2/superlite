"""One-turn instruction-test runner: real agent.md, record-and-deny tools, cost caps."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AGENTS = REPO / "agents"
COST_LOG = REPO / ".superlite" / "instruction-test-costs.jsonl"
PASS_HASH = REPO / ".superlite" / "instruction-test-pass.json"
CLAUDE = shutil.which("claude") or os.environ.get("CLAUDE_BIN", "claude")
MAX_USD = 0.02
MAX_SECS = 30


@dataclass
class Outcome:
    kind: str  # Followed | Guarded | Wrong
    seconds: float
    cost_usd: float
    tokens: int
    detail: str = ""


def instruction_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def should_skip(path: Path, rule: str) -> bool:
    """Skip only when this rule last passed against the same instruction bytes."""
    if not PASS_HASH.exists():
        return False
    data = json.loads(PASS_HASH.read_text(encoding="utf-8"))
    entry = data.get(f"{path.relative_to(REPO)}::{rule}")
    return bool(entry) and entry.get("hash") == instruction_hash(path)


def mark_pass(path: Path, rule: str) -> None:
    PASS_HASH.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(PASS_HASH.read_text(encoding="utf-8")) if PASS_HASH.exists() else {}
    data[f"{path.relative_to(REPO)}::{rule}"] = {"hash": instruction_hash(path)}
    PASS_HASH.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")


def _agent_def(name: str) -> dict:
    text = (AGENTS / f"{name}.md").read_text(encoding="utf-8")
    body = text
    meta: dict = {"description": name, "model": "haiku"}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            for line in text[3:end].splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            body = text[end + 4 :].lstrip("\n")
    # Instruction tests always use haiku so each stay under ~$0.02.
    return {
        "description": meta.get("description", name),
        "prompt": body,
        "model": "haiku",
        "tools": ["Read", "Edit", "Write", "Bash", "Grep", "Glob"],
    }


def _settings(record_hook: Path) -> str:
    # Deny every tool after recording so nothing runs; keep block-coordinator-edits too.
    hooks = {
        "PreToolUse": [
            {
                "matcher": ".*",
                "hooks": [{"type": "command", "command": f'bash "{record_hook.as_posix()}"'}],
            }
        ]
    }
    return json.dumps({
        "enabledPlugins": {"superpowers@claude-plugins-official": False},
        "hooks": hooks,
    })


def _write_record_hook(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env bash
# Record the tool call then deny so nothing runs.
IFS= read -r -d '' input || true
mkdir -p .superlite
printf '%s\\n' "$input" >> .superlite/instruction-test-tools.jsonl
printf '%s\\n' "instruction-test: tool recorded and denied" >&2
printf '%s\\n' '{"permission":"deny","user_message":"recorded"}'
exit 2
""",
        encoding="utf-8",
        newline="\n",
    )
    path.chmod(0o755)


def run_instruction(
    agent: str,
    prompt: str,
    *,
    expect: str,
    rule: str = "",
    budget: float = MAX_USD,
    timeout: int = MAX_SECS,
    cwd: Path | None = None,
) -> Outcome:
    """Run one agent turn. expect is a substring the first move or final text must contain for Followed."""
    agent_path = AGENTS / f"{agent}.md"
    rule = rule or expect
    if should_skip(agent_path, rule):
        return Outcome("Followed", 0.0, 0.0, 0, "skipped unchanged")

    work = cwd or (REPO / ".superlite" / "instruction-test-scratch" / agent)
    work.mkdir(parents=True, exist_ok=True)
    (work / ".superlite").mkdir(exist_ok=True)
    hook = work / ".superlite" / "record-deny.sh"
    _write_record_hook(hook)
    tools_log = work / ".superlite" / "instruction-test-tools.jsonl"
    if tools_log.exists():
        tools_log.unlink()

    agents = {agent: _agent_def(agent)}
    cmd = [
        CLAUDE, "-p", prompt,
        "--agents", json.dumps(agents),
        "--agent", agent,
        "--permission-mode", "auto",
        "--settings", _settings(hook),
        "--output-format", "json",
        "--max-budget-usd", f"{budget:.2f}",
    ]
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd, cwd=work, capture_output=True, text=True, encoding="utf-8",
            timeout=timeout, env={k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")},
        )
    except subprocess.TimeoutExpired:
        return Outcome("Wrong", float(timeout), budget, 0, "timeout")
    secs = time.perf_counter() - t0

    result: dict = {}
    try:
        result = json.loads(proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "{}")
    except json.JSONDecodeError:
        result = {}
    cost = float(result.get("total_cost_usd") or 0.0)
    usage = result.get("usage") or {}
    tokens = int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
    text = str(result.get("result") or "")

    recorded = []
    if tools_log.exists():
        for line in tools_log.read_text(encoding="utf-8").splitlines():
            try:
                recorded.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    kind = "Wrong"
    detail = text
    blob = (text + "\n" + json.dumps(recorded)).upper()
    if expect.upper() in blob:
        kind = "Followed"

    COST_LOG.parent.mkdir(parents=True, exist_ok=True)
    with COST_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "t": int(time.time() * 1000), "agent": agent, "kind": kind,
            "seconds": round(secs, 2), "cost_usd": cost, "tokens": tokens,
            "expect": expect,
        }) + "\n")

    if cost > budget or secs > timeout:
        kind = "Wrong"
        detail = f"over cap cost={cost} secs={secs}"
    if kind == "Followed":
        mark_pass(agent_path, rule)
    return Outcome(kind, secs, cost, tokens, detail)
