"""Benchmark harness: run one arm headless, then score it against the hidden suite.

Usage (from the repo root):
    uv run --no-project python bench/harness.py run <arm> <rep> [--smoke]
    uv run --no-project python bench/harness.py score <arm> <rep>
    uv run --no-project python bench/harness.py report

Arms: superpowers (stock plugin as installed) and superlite (this repo via --plugin-dir,
superpowers disabled). Both disable the user-level orchestrator skill.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import taskmetrics as mechanical  # old name `mechanical` is a thin alias module

if os.name == "nt":  # every child (claude, bash, uv, git, pytest) gets a hidden console: no popup windows
    _Popen = subprocess.Popen

    class _NoWindowPopen(_Popen):
        def __init__(self, *args, **kwargs):
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
            super().__init__(*args, **kwargs)

    subprocess.Popen = _NoWindowPopen

ROOT = Path(__file__).resolve().parent.parent
# The superlite arm loads this plugin directory (default: this repo); SUPERLITE_PLUGIN_DIR points it at e.g.
# a git worktree of an older commit, so a frozen baseline can be compared with the working tree.
PLUGIN_DIR = Path(os.environ.get("SUPERLITE_PLUGIN_DIR") or ROOT)
BENCH = ROOT / "bench"
# Outside the repo, so an arm can't wander up into hidden_tests/ or reference/.
RUNS = Path(os.environ.get("BENCH_RUNS", ROOT.parent / "superlite-bench-runs"))
RESULTS = BENCH / "results"
# BENCH_TASK picks the task: "sitegen" (bench/task + bench/hidden_tests) or a dir under bench/tasks/
# holding SPEC.md and hidden/. Non-default tasks prefix run and result names with the task name.
TASK = os.environ.get("BENCH_TASK", "sitegen")
TASK_SPEC = BENCH / "task" / "SPEC.md" if TASK == "sitegen" else BENCH / "tasks" / TASK / "SPEC.md"
TASK_HIDDEN = BENCH / "hidden_tests" if TASK == "sitegen" else BENCH / "tasks" / TASK / "hidden"
# Optional starting repo: bench/tasks/<task>/start/ is copied into the run dir and committed before the arm starts.
TASK_START = None if TASK == "sitegen" else BENCH / "tasks" / TASK / "start"
PREFIX = "" if TASK == "sitegen" else f"{TASK}-"
TASK_DIR = None if TASK == "sitegen" else BENCH / "tasks" / TASK
MARKER = "BENCHMARK-COMPLETE"
MAX_RESUMES = 6
RESUME_PROMPT = (
    "Approved. No human is available - make the decision yourself and continue "
    "until SPEC.md is fully built, tested and committed. End with the line BENCHMARK-COMPLETE."
)
SMOKE_PROMPT = (
    "List the names of the skills and agent types available to you that come from plugins "
    "(one per line, no commentary), then end with the line BENCHMARK-COMPLETE."
)

def load_task_cfg(task_dir: Path | None) -> dict:
    """Language settings from <task>/task.yaml (flat `key: value` lines, lists as `[a, b]`);
    without one a task is Python: .py files under src/, no Node."""
    cfg = {"language": "python", "extensions": [".py"], "src_dirs": ["src"], "needs_node": False}
    path = task_dir / "task.yaml" if task_dir else None
    if path and path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, val = line.split("#")[0].partition(":")
            key, val = key.strip(), val.strip()
            if not sep or key not in cfg:
                continue
            if isinstance(cfg[key], list):
                cfg[key] = [v.strip() for v in val.strip("[]").split(",") if v.strip()]
            elif isinstance(cfg[key], bool):
                cfg[key] = val.lower() in ("true", "yes", "1")
            else:
                cfg[key] = val
    return cfg


TASK_CFG = load_task_cfg(TASK_DIR)


def node_dir() -> Path:
    """User-local Node (BENCH_NODE_DIR, else ~/tools/node), so npm works without touching the system PATH."""
    return Path(os.environ.get("BENCH_NODE_DIR", Path.home() / "tools" / "node"))


def with_node(env: dict[str, str], cfg: dict | None = None) -> dict[str, str]:
    """env with the user-local Node first on PATH when the task needs it; else env unchanged."""
    if not (cfg or TASK_CFG)["needs_node"] or not node_dir().is_dir():
        return env
    key = next((k for k in env if k.upper() == "PATH"), "PATH")
    return {**env, key: str(node_dir()) + os.pathsep + env.get(key, "")}


def bash_exe() -> str:
    """A real bash: Git's on Windows (System32 bash.exe is WSL), else whatever is on PATH."""
    if os.environ.get("BENCH_BASH"):
        return os.environ["BENCH_BASH"]
    git = shutil.which("git")
    if git:
        for sub in ("bin", "usr/bin"):
            cand = Path(git).parent.parent / sub / "bash.exe"
            if cand.is_file():
                return str(cand)
    return shutil.which("bash") or "bash"


def junit_counts(junit: Path) -> tuple[int, list[str], dict]:
    """(total, failed ids, {file: [passed, total]}) from a JUnit XML file."""
    total, failed, by_file = 0, [], {}
    for case in ET.parse(junit).getroot().iter("testcase"):
        total += 1
        # hidden_tests.test_api -> test_api; with suite subdirs (importlib mode) keep "suite.test_x".
        parts = (case.get("classname") or "").split(".")
        f = ".".join(parts[-2:]) if len(parts) >= 2 and parts[-2] not in ("hidden_tests",) else parts[-1]
        bucket = by_file.setdefault(f, [0, 0])
        bucket[1] += 1
        if any(child.tag in ("failure", "error", "skipped") for child in case):
            failed.append(f"{case.get('classname')}::{case.get('name')}")
        else:
            bucket[0] += 1
    return total, failed, by_file


def count_src_loc(run_dir: Path, cfg: dict | None = None) -> int:
    """Lines in the task's source files: its extensions under its src_dirs (default .py under src/)."""
    cfg = cfg or TASK_CFG
    total = 0
    for d in cfg["src_dirs"]:
        root = run_dir / d
        if root.exists():
            total += sum(len(p.read_text(encoding="utf-8", errors="replace").splitlines())
                         for p in root.rglob("*") if p.is_file() and p.suffix in cfg["extensions"]
                         and "node_modules" not in p.parts)
    return total


def with_suffix(prompt: str) -> str:
    """The task prompt plus BENCH_PROMPT_SUFFIX (default empty: prompt unchanged), e.g. to force a workflow."""
    suffix = os.environ.get("BENCH_PROMPT_SUFFIX", "").strip()
    return prompt.rstrip() + "\n\n" + suffix + "\n" if suffix else prompt


def prompt_path(human: bool = False, task: str | None = None) -> Path:
    """The task's own prompt (bench/tasks/<task>/prompt.md, or prompt-human.md in human mode) when it
    exists; else the shared bench/prompt.md (prompt-human.md), which says "Build the project"."""
    name = "prompt-human.md" if human else "prompt.md"
    task = TASK if task is None else task
    own = BENCH / "tasks" / task / name
    return own if task != "sitegen" and own.is_file() else BENCH / name


# Environment guard for arms without superlite's own hook: an arm that kills by name
# (taskkill /IM python.exe) also kills the harness and the other arm. The guard only blocks
# name/pattern kills; it adds no skills or context, so the superpowers arm stays stock.
KILL_GUARD = {"PreToolUse": [{"matcher": "Bash|PowerShell", "hooks": [{
    "type": "command", "command": f'bash "{ROOT.as_posix()}/hooks/block-unsafe-kill"'}]}]}

ARM_ARGS = {
    "superpowers": ["--settings", json.dumps({"skillOverrides": {"orchestrator": "off"},
                                              "hooks": KILL_GUARD})],
    "superlite": [
        "--settings",
        json.dumps({
            "enabledPlugins": {"superpowers@claude-plugins-official": False},
            "skillOverrides": {"orchestrator": "off"},
        }),
        "--plugin-dir", str(PLUGIN_DIR),
    ],
}


def common_args(budget: str) -> list[str]:
    return [
        "--model", "opus",
        "--permission-mode", "auto",
        "--permission-prompts", "none",
        "--output-format", "stream-json", "--verbose",
        "--max-budget-usd", budget,
        "--disallowedTools", "Skill(orchestrator)",
    ]


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def read_events(log: Path) -> list[dict]:
    events = []
    if not log.exists():
        return events
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return events


def results_of(events: list[dict]) -> list[dict]:
    return [e for e in events if e.get("type") == "result"]


def clean_env() -> dict[str, str]:
    """Drop variables inherited from a host Claude session (e.g. CLAUDE_CODE_SIMPLE turns on
    bare mode, which skips plugin hooks), so each arm runs like a fresh terminal session."""
    return {k: v for k, v in os.environ.items()
            if not (k.startswith("CLAUDE") or k.startswith("ANTHROPIC"))}


def arm_env() -> dict[str, str]:
    env = clean_env()
    # Fork subagents are on by default in interactive sessions but off in -p; turn them on so
    # headless arms behave like the interactive sessions a user would actually run.
    env["CLAUDE_CODE_FORK_SUBAGENT"] = "1"
    return with_node(env)  # the agent's Bash tool inherits PATH from here


def invoke(cmd: list[str], cwd: Path, log: Path, err: Path) -> None:
    with log.open("a", encoding="utf-8") as out, err.open("a", encoding="utf-8") as er:
        subprocess.run(cmd, cwd=cwd, stdout=out, stderr=er, stdin=subprocess.DEVNULL, env=arm_env())


DECIDER_TOOLS_OFF = "Bash Edit Write Read Glob Grep Agent WebFetch WebSearch NotebookEdit Skill"


def decide(claude: str, question: str, cwd: Path) -> tuple[str, float, float]:
    """Ask the Opus decision-maker (who plays the human) to answer the arm's last message.
    Returns (answer, seconds, cost_usd). It runs tool-less, skill-less and plugin-free."""
    spec = TASK_SPEC.read_text(encoding="utf-8")
    prompt = (f"<spec>\n{spec}\n</spec>\n\n<agent_message>\n{question[-8000:]}\n</agent_message>\n\n"
              "Reply now as the product owner.")
    # Prompt via stdin and system prompt via file: the SPEC alone is ~29k chars, and Windows
    # caps a command line at 32k.
    cmd = [claude, "-p", "--model", "opus", "--output-format", "json",
           "--system-prompt-file", str(BENCH / "decider.md"),
           "--disable-slash-commands", "--disallowedTools", DECIDER_TOOLS_OFF,
           "--settings", json.dumps({"enabledPlugins": {"superpowers@claude-plugins-official": False}}),
           "--no-session-persistence", "--max-budget-usd", "2"]
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=cwd, input=prompt, capture_output=True, text=True,
                          encoding="utf-8", env=clean_env())
    secs = time.time() - t0
    try:
        out = json.loads(proc.stdout)
        return (out.get("result") or "Continue.").strip(), secs, out.get("total_cost_usd") or 0.0
    except json.JSONDecodeError:
        return "Approved. Continue, and decide anything else yourself.", secs, 0.0


def seed_run_dir(run_dir: Path) -> None:
    """Fresh git repo in run_dir holding SPEC.md and, if the task has one, its start/ tree."""
    has_start = TASK_START is not None and TASK_START.is_dir()
    if has_start:
        shutil.copytree(TASK_START, run_dir, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
    shutil.copy(TASK_SPEC, run_dir / "SPEC.md")
    git(run_dir, "init", "-q", "-b", "main")
    git(run_dir, "add", "-A" if has_start else "SPEC.md")
    git(run_dir, "-c", "user.name=bench", "-c", "user.email=bench@local", "commit", "-qm",
        "Add SPEC.md and starting repo" if has_start else "Add SPEC.md")


def cmd_run(arm: str, rep: str, smoke: bool, human: bool = False) -> None:
    claude = shutil.which("claude") or "claude"
    name = f"{PREFIX}{arm}-{rep}" + ("-smoke" if smoke else "")
    run_dir = RUNS / name
    RESULTS.mkdir(parents=True, exist_ok=True)
    log, err = RESULTS / f"{name}.jsonl", RESULTS / f"{name}.stderr"
    if run_dir.exists():
        sys.exit(f"run dir exists: {run_dir}")
    run_dir.mkdir(parents=True)
    seed_run_dir(run_dir)

    budget = os.environ.get("BENCH_BUDGET", "1" if smoke else "60")
    base = [claude, "-p"]
    extra = common_args(budget) + ARM_ARGS[arm]
    prompt = SMOKE_PROMPT if smoke else with_suffix(prompt_path(human).read_text(encoding="utf-8"))

    t0 = time.time()
    invoke(base + [prompt] + extra, run_dir, log, err)
    resumes, decider_s, decider_cost = 0, 0.0, 0.0
    transcript = RESULTS / f"{name}.decisions.md"
    max_resumes = 12 if human else MAX_RESUMES
    while not smoke and resumes < max_resumes:
        res = results_of(read_events(log))
        if not res or MARKER in (res[-1].get("result") or ""):
            break
        sid = res[-1].get("session_id")
        if not sid:
            break
        resumes += 1
        if human:
            question = res[-1].get("result") or ""
            answer, secs, cost = decide(claude, question, run_dir)
            decider_s, decider_cost = decider_s + secs, decider_cost + cost
            with transcript.open("a", encoding="utf-8") as f:
                f.write(f"## Stop {resumes} (decider {secs:.0f}s)\n\n### Agent asked\n\n{question[-4000:]}\n\n"
                        f"### Decider answered\n\n{answer}\n\n")
            reply = answer
        else:
            reply = RESUME_PROMPT
        invoke(base + [reply, "--resume", sid] + extra, run_dir, log, err)
    wall = time.time() - t0
    meta = {"arm": arm, "rep": rep, "smoke": smoke, "mode": "human" if human else "preapproved",
            "wall_s": round(wall, 1), "arm_wall_s": round(wall - decider_s, 1), "resumes": resumes,
            "decider_s": round(decider_s, 1), "decider_cost_usd": round(decider_cost, 3)}
    (RESULTS / f"{name}.meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(meta))


def stream_stats(events: list[dict]) -> dict:
    agent_calls, models, coord_tools = 0, {}, 0
    for e in events:
        if e.get("type") != "assistant":
            continue
        top = e.get("parent_tool_use_id") is None
        for block in (e.get("message") or {}).get("content") or []:
            if block.get("type") != "tool_use":
                continue
            if top:
                coord_tools += 1
            if block.get("name") in ("Agent", "Task"):
                agent_calls += 1
                m = (block.get("input") or {}).get("model") or "(default)"
                models[m] = models.get(m, 0) + 1
    return {"agent_dispatches": agent_calls, "dispatch_models": models, "coordinator_tool_calls": coord_tools}


def _ts(e: dict) -> float | None:
    s = e.get("timestamp")
    if not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _ctx(u: dict) -> int:
    return ((u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
            + (u.get("cache_creation_input_tokens") or 0))


# ---- 0.3 benchmark metrics (T7) -------------------------------------------------------------
SKILL_DIR = ROOT / "skills" / "swarm"
MODELS_JSON = SKILL_DIR / "config" / "models.json"


def load_prices(path: Path = MODELS_JSON) -> dict[str, tuple]:
    """Per model: (input, output, cache write 5m, cache write 1h, cache read) in USD per token, read from
    config/models.json (the one price table; predict.py reads it too). A cache write price that is
    omitted defaults to input x cache_write_multipliers, as there."""
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    per = float(cfg.get("per_tokens") or 1_000_000)
    mult = cfg.get("cache_write_multipliers") or {}
    out = {}
    for name, m in (cfg.get("models") or {}).items():
        w5 = m.get("cache_write_5m", m["input"] * mult.get("5m", 1.25))
        w1 = m.get("cache_write_1h", m["input"] * mult.get("1h", 2.0))
        out[name] = tuple(float(x) / per for x in (m["input"], m["output"], w5, w1, m["cache_read"]))
    return out


PRICES = load_prices()
ROUTING_JSON = RESULTS / "routing-recommendation.json"  # `rules` there give tier_planned when a dispatch line has no tier=
# Same patterns as hooks/block-unsafe-kill: kill by image name or pattern.
UNSAFE_KILL = re.compile(
    r"(?:^|[\s;&|(`])(?:taskkill\b[^\n]*?\s//?im(?!\w)|(?:pkill|killall)(?![\w-])"
    r"|stop-process\b[^\n]*?\s-name(?!\w)|kill\s+-9\s+-1(?!\d))", re.I)
_PRIME_RE = re.compile(r"do not act yet|\bREADY\b")
_POLL_RE = re.compile(r"relevance\s*(?:score\s*)?0\s*(?:-|–|—|to)\s*10", re.I)
_QUEUE_RE = re.compile(r"\bdo (?:this |it |these )?after\b|\bqueued? (?:behind|after)\b"
                       r"|\bafter you (?:finish|complete|are done)\b", re.I)
_FIX_RE = re.compile(r"\bfix(?:es|ed)?\b", re.I)
_TASK_KEY_RE = re.compile(r"\b(?:task|T)\s*([A-Za-z]?\d+[A-Za-z]?)\b", re.I)
_VERDICT_RE = re.compile(r"^\W*verdict\b\s*[:\-—*]", re.I | re.M)
# Implementer descriptions: "Implement ...", "T3 ...", "Task 3 ...", "Task A1 ..." (letter + digit ids).
_IMPL_DESC_RE = re.compile(r"(implement|t\d|task\s*[a-z]?\d)")
_BATCH_RE = re.compile(r"batch|package|wave|\btasks\s+\w+\s*(?:[,/&+]|and)\s*\w+", re.I)
_PRIME_HASH_RE = re.compile(r"prime[-_ ]hash\W{0,3}([0-9a-f]{8,64})", re.I)
# The scripts run as `sl <tool> ...` (no .py) or `python .../<tool>.py ...`. A seam check is
# `pt_review contract --seam Ta,Tb` (plan_tools dispatches `contract` to it).
_TOOL_SEP = r"(?:\.py)?\s+"
_SEAM_RE = re.compile(r"\b(?:plan_tools|pt_review)" + _TOOL_SEP + r"contract\b[^\n]*--seam"
                      r"|\bseam[-_ ]?(?:check|smoke)\b", re.I)
_SEAM_FAIL_RE = re.compile(r"^FAIL:|MISMATCH", re.M)
_INCIDENT_ADD_RE = re.compile(r"\bincident" + _TOOL_SEP + r"add\b")
_BUILD_PRIME_RE = re.compile(r"\bbuild_prime\b")
_SCHEDULE_RE = re.compile(r"\bplan_tools" + _TOOL_SEP + r"schedule\b")
# First line of build_prime.PREAMBLE: the shared prime text starts here in a dispatched prompt.
PRIME_MARKER = "# superlite prime"
# build_prime.py prints "<sha256> <size in bytes>".
_PRIME_OUT_RE = re.compile(r"\b([0-9a-f]{64})\s+(\d+)\b")
# pt_review contract --seam logs "verified: seam Ta↔Tb" or "seam Ta↔Tb: <mismatch>" (agent pt_review).
_SEAM_OK_ENTRY = re.compile(r"^verified:\s*seam\s+(\S+)")
_SEAM_BAD_ENTRY = re.compile(r"^seam\s+([^\s:]+):")


def _text_of(x) -> str:
    if isinstance(x, str):
        return x
    if isinstance(x, list):
        return "\n".join(_text_of(i.get("text") if isinstance(i, dict) and "text" in i else i) for i in x)
    return json.dumps(x) if x is not None else ""


def _task_key(text: str) -> str | None:
    m = _TASK_KEY_RE.search(text or "")
    return m.group(1).lower() if m else None


def _call_kind(inp: dict) -> str:
    """Classify an Agent dispatch: fix | final_review | review | test | build | other."""
    desc = inp.get("description") or ""
    words = ((inp.get("subagent_type") or "") + " " + desc).lower()
    if _FIX_RE.search(desc):
        return "fix"
    if "review" in words:
        return "final_review" if "final" in words and "re-review" not in words else "review"
    if re.search(r"test[-_ ]?runner|\brun (?:the )?(?:full )?(?:suite|tests?)\b|\bfull suite\b", words):
        return "test"
    if (inp.get("subagent_type") or "").endswith("implementer") or _IMPL_DESC_RE.match(desc.lower()):
        return "build"
    return "other"


def _plan_numbers(plan_text: str, results_text: str) -> tuple[dict, float | None, float | None]:
    """Predicted per-task Est. time (s), predicted total (s) and predicted critical path (s).

    Sources, best first: `plan_tools.py schedule` output (the predictor's per-task times: a
    `--json` object, or the plain `  T1: lane 1, 0.0 to 2.5 min (features)` lines), then the plan's
    stated `Est. time` lines (for tasks the schedule did not list: the predictor wins over a stated
    value). The totals come from schedule's JSON (`total_min`, `critical_path_min`) or its summary
    line `critical path: T1 -> T7 (5.0 min) ... predicted total ~ 8.5 min`."""
    est: dict[str, float] = {}
    cur = None
    for line in plan_text.splitlines():
        h = re.match(r"#{2,4}\s*(?:Task|T)\s*([A-Za-z]?\d+[A-Za-z]?)\b", line, re.I)
        if h:
            cur = h.group(1).lower()
        m = re.search(r"Est\. time:?\*{0,2}\s*([\d.]+)\s*(min|s)\b", line)
        if m and cur:
            est[cur] = float(m.group(1)) * (60 if m.group(2) == "min" else 1)
    corpus = plan_text + "\n" + results_text
    tot = [float(x) * 60 for x in re.findall(r"predicted total\s*[≈~=:]*\s*([\d.]+)\s*min", corpus, re.I)]
    cp = [float(x) * 60 for x in re.findall(r"critical path:[^\n(]*\(([\d.]+)\s*min\)", corpus, re.I)]
    sched: dict[str, float] = {}
    for m in re.finditer(r"^\s*(?:T|Task\s*)?([A-Za-z]?\d+[A-Za-z]?):\s*lane\s+\d+,\s*([\d.]+)\s+to\s+([\d.]+)\s+min",
                         results_text, re.M | re.I):
        sched[m.group(1).lower()] = (float(m.group(3)) - float(m.group(2))) * 60
    for m in re.finditer(r"^\s*\{.*?^\}", results_text, re.M | re.S):      # `schedule --json`
        try:
            d = json.loads(m.group(0))
        except ValueError:
            continue
        if not isinstance(d, dict) or "critical_path_min" not in d:
            continue
        for tid, t in (d.get("tasks") or {}).items():
            if isinstance(t, dict) and isinstance(t.get("min"), (int, float)):
                sched[re.sub(r"^(?:task|t)\s*", "", str(tid), flags=re.I).lower()] = float(t["min"]) * 60
        if isinstance(d.get("total_min"), (int, float)):
            tot.append(float(d["total_min"]) * 60)
        if isinstance(d.get("critical_path_min"), (int, float)):
            cp.append(float(d["critical_path_min"]) * 60)
    est.update({k: v for k, v in sched.items() if v > 0})
    return est, (tot[-1] if tot else None), (cp[-1] if cp else None)


def flow_metrics(events: list[dict], t0: float, last_t: float | None, coordinator_turns: int,
                 incident_log: list[dict] | None = None, prime_bytes: bytes | None = None) -> dict:
    """Agent-flow metrics from one pass over the stream: assignments to agents (idle reuse, queued
    work, polls), per-assignment segments (system task_started .. task_notification, the only
    reliable busy/idle markers), the build/review/fix tail, and 0.3 script and safety signals.
    System events carry no timestamp, so their time is the latest timestamped event before them.
    Signals whose source events don't exist in older logs (polls, seams, prime hashes, plan
    predictions) come back as None or 0 and never raise.

    `incident_log` is the run's .superlite/incidents.jsonl (what incident.py, pt_review contract,
    probe_run and checkpoint wrote); `prime_bytes` is .superlite/prime.md (what build_prime.py
    wrote). When given they are the source for incident counts, seam checks and the prime hash;
    otherwise those come from the commands and prompts in the stream."""
    stamped = any(_ts(e) is not None for e in events)
    tf: list[float] = []
    cur = 0.0
    for i, e in enumerate(events):
        t = _ts(e)
        cur = (t - t0) if t is not None else (cur if stamped else float(i))
        tf.append(cur)

    agents: dict[str, dict] = {}
    agent_of_call: dict[str, str] = {}
    name_of_agent: dict[str, str] = {}
    calls: dict[str, dict] = {}           # coordinator Agent tool_use id -> {idx, t, inp}
    msgs: dict[str, dict] = {}            # coordinator SendMessage tool_use id -> message record
    segs: list[dict] = []
    assigns: list[dict] = []
    polls: dict[str, dict] = {}
    seen_tu: set[str] = set()
    usage: dict[str, dict] = {}
    watch: dict[str, str] = {}            # tool_use id of a command whose output we read
    results: dict[str, str] = {}
    plan_writes: list[str] = []
    incidents: dict[str, int] = {}
    unsafe = graphify = 0
    seam_ids: list[str] = []
    top_assist: list[int] = []
    notifs: list[tuple[int, dict]] = []

    def new_agent(aid: str, call: str, pseudo: bool = False) -> dict:
        ag = {"id": aid, "call": call, "tasks": 0, "ctx": 0, "open": None, "pseudo": pseudo, "n": 0}
        agents[aid] = ag
        agent_of_call[call] = aid
        return ag

    def open_seg(ag: dict, idx: int, kind: str, text: str, call: str) -> None:
        s = {"agent": ag["id"], "call": call, "kind": kind, "start": tf[idx], "end": None, "first": None,
             "last": None, "key": _task_key(text[:300]) if kind == "build" else None,
             "first_of_agent": ag["n"] == 0, "report": ""}
        ag["n"] += 1
        ag["open"] = s
        segs.append(s)

    def touch(ag: dict | None, idx: int) -> None:
        s = ag["open"] if ag else None
        if s:
            s["first"] = tf[idx] if s["first"] is None else s["first"]
            s["last"] = tf[idx]

    for idx, e in enumerate(events):
        ty, parent, sub = e.get("type"), e.get("parent_tool_use_id"), e.get("subtype")
        if ty == "system" and sub == "task_started" and e.get("task_type") == "local_agent":
            aid, cid = e.get("task_id"), e.get("tool_use_id")
            ag = agents.get(aid)
            if ag is None and cid in agent_of_call and agents[agent_of_call[cid]]["pseudo"]:
                ag = agents.pop(agent_of_call[cid])      # sub events arrived first: adopt that agent
                ag.update(id=aid, pseudo=False)
                agents[aid] = ag
                agent_of_call[cid] = aid
                if ag["open"]:
                    ag["open"]["agent"] = aid
            if ag is None:
                ag = new_agent(aid, cid)
                inp = calls.get(cid, {}).get("inp") or {}
                prompt = inp.get("prompt") or e.get("prompt") or ""
                prime = bool(_PRIME_RE.search(prompt))
                kind = "prime" if prime else _call_kind(
                    inp or {"description": e.get("description"), "subagent_type": e.get("subagent_type")})
                ag["tasks"] = 0 if prime or kind != "build" else 1
                name_of_agent[aid] = inp.get("name") or ""
                open_seg(ag, idx, kind, (inp.get("description") or e.get("description") or "") + " "
                         + prompt[:200], cid)
            elif ag["open"] is None:
                m = msgs.get(cid)
                open_seg(ag, idx, "fix" if m and m["fix"] else "build",
                         (m["summary"] + " " + m["message"]) if m else "", cid)
            continue
        if ty == "system" and sub == "task_notification":
            ag = agents.get(e.get("task_id"))
            if ag and ag["open"]:
                s = ag["open"]
                s["end"] = s["last"] if s["last"] is not None else tf[idx]
                s["report"] = _text_of((e.get("handback_report") or {}).get("text"))
                # A fresh agent's first report is READY, not DONE: that segment was the prime.
                if (s["first_of_agent"] and s["kind"] == "build" and len(s["report"]) < 800
                        and re.search(r"\bREADY\b", s["report"]) and not re.search(r"\bDONE", s["report"])):
                    s["kind"], s["key"] = "prime", None
                    if ag["tasks"] == 1:
                        ag["tasks"] = 0
                ag["open"] = None
                notifs.append((idx, s))
            continue
        if ty == "user":
            c = (e.get("message") or {}).get("content")
            for b in c if isinstance(c, list) else []:
                if b.get("type") == "tool_result" and b.get("tool_use_id") in watch:
                    results[b["tool_use_id"]] = _text_of(b.get("content"))
            if parent:
                touch(agents.get(agent_of_call.get(parent, "")), idx)
            continue
        if ty != "assistant":
            continue
        msg = e.get("message") or {}
        mid = msg.get("id")
        if parent:
            if parent not in agent_of_call:      # no task_started seen: one segment per dispatch
                inp = calls.get(parent, {}).get("inp") or {}
                open_seg(new_agent(parent, parent, pseudo=True), idx, _call_kind(inp) if inp else "other",
                         inp.get("description") or "", parent)
            ag = agents[agent_of_call[parent]]
            touch(ag, idx)
            if isinstance(msg.get("usage"), dict) and mid and mid not in usage:
                ag["ctx"] = _ctx(msg["usage"])
        else:
            top_assist.append(idx)
        if isinstance(msg.get("usage"), dict) and mid:
            rec = usage.setdefault(mid, {"parent": parent, "model": msg.get("model") or "", "u": dict(msg["usage"])})
            rec["u"]["output_tokens"] = max(rec["u"].get("output_tokens") or 0, msg["usage"].get("output_tokens") or 0)
        for b in msg.get("content") or []:
            if b.get("type") == "text" and parent and _VERDICT_RE.search(b.get("text") or ""):
                ag = agents.get(agent_of_call.get(parent, ""))
                if ag and ag["open"] and "verdict" not in ag["open"]:
                    ag["open"]["verdict"] = tf[idx]
            if b.get("type") != "tool_use" or b.get("id") in seen_tu:
                continue
            seen_tu.add(b["id"])
            inp, name = b.get("input") or {}, b.get("name")
            if name in ("Bash", "PowerShell"):
                cmd = str(inp.get("command") or "")
                unsafe += bool(UNSAFE_KILL.search(cmd))
                graphify += bool(re.search(r"\bgraphify\b", cmd, re.I))
                if _INCIDENT_ADD_RE.search(cmd):
                    k = re.search(r"--kind[ =]+['\"]?([\w-]+)", cmd)
                    kind = k.group(1) if k else "unspecified"
                    incidents[kind] = incidents.get(kind, 0) + 1
                if _SEAM_RE.search(cmd):
                    watch[b["id"]] = "seam"
                    seam_ids.append(b["id"])
                elif _BUILD_PRIME_RE.search(cmd):
                    watch[b["id"]] = "prime"
                elif _SCHEDULE_RE.search(cmd):
                    watch[b["id"]] = "schedule"
            elif name == "Write" and re.search(r"plan.*\.md$", str(inp.get("file_path", "")).replace("\\", "/"), re.I):
                plan_writes.append(str(inp.get("content") or ""))
            elif not parent and name in ("Agent", "Task"):
                calls[b["id"]] = {"idx": idx, "t": tf[idx], "inp": inp}
            elif not parent and name == "SendMessage":
                message, summary = _text_of(inp.get("message")), str(inp.get("summary") or "")
                to = str(inp.get("to") or "")
                text = summary + " " + message
                rec = {"id": b["id"], "summary": summary, "message": message,
                       "fix": bool(_FIX_RE.search(summary + " " + message[:120]))}
                msgs[b["id"]] = rec
                if _POLL_RE.search(text):
                    polls.setdefault(mid or b["id"], {"t": tf[idx], "ids": []})["ids"].append(b["id"])
                    continue
                aid = to if to in agents else next((a for a, n in name_of_agent.items() if n and n == to), None)
                if aid is None:
                    continue
                ag = agents[aid]
                busy = ag["open"] is not None
                queued = busy or bool(_QUEUE_RE.search(text))
                assigns.append({
                    "to": aid, "t": round(tf[idx], 1), "kind": "fix" if rec["fix"] else "task",
                    "busy": busy, "queued": queued, "reuse_idle": not queued and ag["tasks"] >= 1,
                    # Past 3 tasks, or an agent already beyond 80k context, is outside the reuse limits.
                    "limit_breach": ag["tasks"] >= 1 and (ag["tasks"] >= 3 or ag["ctx"] > 80000)})
                ag["tasks"] += 1

    for ag in agents.values():                   # segments still open when the log ends
        s = ag["open"]
        if s:
            s["end"] = s["last"] if s["last"] is not None else (tf[-1] if tf else 0.0)
    for s in segs:
        s["start"] = s["first"] if s["first"] is not None else s["start"]
        s["end"] = max(s["start"], s["end"] if s["end"] is not None else s["start"])
        s["dur"] = s["end"] - s["start"]

    # Schedule shape: gaps with no agent running, the backward critical chain, the build tail.
    live = sorted((s for s in segs if s["dur"] > 0), key=lambda s: s["start"])
    idle_gaps = 0.0
    crit = None
    if live:
        reach = live[0]["end"]
        for s in live[1:]:
            idle_gaps += max(0.0, s["start"] - reach)
            reach = max(reach, s["end"])
        c = max(live, key=lambda s: s["end"])
        crit = c["dur"]
        while True:
            prev = [s for s in live if s is not c and s["end"] <= c["start"] + 1.0]
            if not prev:
                break
            c = max(prev, key=lambda s: s["end"])
            crit += c["dur"]
    builds = [s for s in segs if s["kind"] == "build" and s["dur"] > 0]
    last_build_end = max((s["end"] for s in builds), default=None)

    # Final review (dispatch to verdict) and the fixes after the verdict.
    finals = sorted((s for s in segs if s["kind"] == "final_review" and s["call"] in calls),
                    key=lambda s: s["start"])
    final_review_s = final_fix_s = None
    if finals:
        final = finals[0]
        verdict = final.get("verdict", final["end"])
        final_review_s = round(verdict - calls[final["call"]]["t"], 1)
        fix_ends = [s["end"] for s in segs if s["kind"] == "fix" and s["start"] >= verdict - 1.0]
        final_fix_s = round(max(0.0, max([final["end"]] + fix_ends) - verdict), 1)

    # Predicted against actual.
    est, pred_total, pred_cp = _plan_numbers(
        plan_writes[-1] if plan_writes else "", "\n".join(results.get(i, "") for i in watch))
    errs, seen_key = [], set()
    for s in builds:
        if s["key"] in est and s["key"] not in seen_key:
            seen_key.add(s["key"])
            errs.append(abs(s["dur"] - est[s["key"]]) / est[s["key"]])

    # Reviews: count, batching and estimated cost.
    reviews = [s for s in segs if s["kind"] in ("review", "final_review") and s["call"] in calls]
    plain = [s for s in reviews if s["kind"] == "review"]
    task_count = len({s["key"] for s in builds if s["key"]}) + sum(1 for s in builds if not s["key"])
    review_calls = {s["call"] for s in reviews}
    cost = 0.0
    for rec in usage.values():
        price = next((p for k, p in PRICES.items() if k in rec["model"]), None)
        if rec["parent"] in review_calls and price:
            u = rec["u"]
            cc = u.get("cache_creation") if isinstance(u.get("cache_creation"), dict) else {}
            w1 = cc.get("ephemeral_1h_input_tokens") or 0
            w5 = (u.get("cache_creation_input_tokens") or 0) - w1
            cost += ((u.get("input_tokens") or 0) * price[0] + (u.get("output_tokens") or 0) * price[1]
                     + w5 * price[2] + w1 * price[3] + (u.get("cache_read_input_tokens") or 0) * price[4])

    if incident_log is not None:        # the log counts every writer, not only `incident add` commands
        incidents = {}
        for e in incident_log:
            if e.get("kind") != "verified":
                k = str(e.get("kind") or "unspecified")
                incidents[k] = incidents.get(k, 0) + 1

    # Relevance polls: a round is one coordinator message; its latency runs to the last answer.
    poll_s = None
    if polls:
        poll_s = 0.0
        for p in polls.values():
            ends = [s["end"] for s in segs if s["call"] in p["ids"]]
            poll_s += max(0.0, max(ends) - p["t"]) if ends else 0.0

    # Seam checks: the log is the record (the scripts run in the background, so the stream often has
    # no command for them); else the stream's `contract --seam` commands and their output.
    seam_ok, seam_bad_labels = 0, set()
    for e in incident_log or []:
        if e.get("agent") != "pt_review":
            continue
        ctx = str(e.get("context") or "")
        if e.get("kind") == "verified" and _SEAM_OK_ENTRY.match(ctx):
            seam_ok += 1
        elif e.get("kind") != "verified" and _SEAM_BAD_ENTRY.match(ctx):
            seam_bad_labels.add(_SEAM_BAD_ENTRY.match(ctx).group(1))
    if seam_ok or seam_bad_labels:
        seam_n, seam_bad = seam_ok + len(seam_bad_labels), len(seam_bad_labels)
    else:
        seam_n = len(seam_ids)
        seam_bad = sum(1 for i in seam_ids if _SEAM_FAIL_RE.search(results.get(i, "")))

    # Prime hash: the expected hash is the one build_prime.py wrote (prime.md, else its printed
    # `<sha256> <size>`); each agent's prime is the first `size` bytes of its prompt from the
    # prime's first line on. Older logs state `prime-hash: <hex>` in the prompt instead.
    exp_hash = exp_size = None
    if prime_bytes:
        exp_hash, exp_size = hashlib.sha256(prime_bytes).hexdigest(), len(prime_bytes)
    else:
        for i, k in watch.items():
            m = _PRIME_OUT_RE.search(results.get(i, "")) if k == "prime" else None
            if m:
                exp_hash, exp_size = m.group(1), int(m.group(2))
                break
    legacy_expected = next((m.group(1) for i, k in watch.items() if k == "prime"
                            for m in [re.search(r"\b([0-9a-f]{12,64})\b", results.get(i, ""))] if m), None)
    hashes: dict[str, str | None] = {}
    for cid, c in calls.items():
        prompt = str(c["inp"].get("prompt") or "")
        at = prompt.find(PRIME_MARKER)
        if at >= 0 and exp_size:
            b = prompt[at:].encode("utf-8")
            if hashlib.sha256(b[:exp_size]).hexdigest() == exp_hash:
                hashes[cid] = exp_hash
            elif hashlib.sha256(b[:exp_size - 1] + b"\n").hexdigest() == exp_hash:   # trailing newline lost
                hashes[cid] = exp_hash
            else:
                hashes[cid] = "differs:" + hashlib.sha256(b[:exp_size]).hexdigest()
        elif _call_kind(c["inp"]) == "build":
            m = _PRIME_HASH_RE.search(prompt)
            hashes[cid] = m.group(1) if m else None
    prime_bad = None
    expected = exp_hash or legacy_expected
    if expected or any(hashes.values()):
        found = [h for h in hashes.values() if h and not h.startswith("differs:")]
        expected = expected or (max(set(found), key=found.count) if found else None)
        prime_bad = sum(1 for h in hashes.values() if h != expected)

    # Report lag: the agent's last event until the coordinator's next output.
    lags = []
    for nidx, s in notifs:
        nxt = next((i for i in top_assist if i > nidx), None)
        if nxt is not None:
            lags.append(max(0.0, tf[nxt] - s["end"]))

    def r1(x):
        return round(x, 1) if x is not None else None

    return {
        "queued_assignments": sum(a["queued"] for a in assigns),
        "agent_reuse_idle": sum(a["reuse_idle"] for a in assigns),
        "relevance_polls": len(polls),
        "relevance_poll_s": r1(poll_s),
        "reuse_limit_breaches": sum(a["limit_breach"] for a in assigns),
        "idle_gaps_s": r1(idle_gaps) if live else None,
        "critical_path_s": r1(crit),
        "critical_path_actual": r1(crit),
        "critical_path_predicted": r1(pred_cp),
        "tail_after_build_s": r1(last_t - last_build_end) if None not in (last_build_end, last_t) else None,
        "last_build_end_s": r1(last_build_end),
        "incidents": {"count": sum(incidents.values()), "by_kind": incidents},
        "graphify_used": bool(graphify),
        "final_review_s": final_review_s,
        "final_fix_s": final_fix_s,
        "longest_task_s": r1(max((s["dur"] for s in builds), default=None)),
        "est_time_error": round(statistics.mean(errs), 3) if errs else None,
        "predicted_total_s": r1(pred_total),
        "actual_total_s": r1(last_t),
        "seam_checks": seam_n or None,
        "seam_mismatches": seam_bad if seam_n else None,
        "reviews_per_task": round(len(plain) / task_count, 2) if task_count else None,
        "reviews_batched": sum(1 for s in plain if _BATCH_RE.search(calls[s["call"]]["inp"].get("description") or "")),
        "review_cost_usd": round(cost, 3),
        "prime_hash_mismatches": prime_bad,
        "coordinator_turns": coordinator_turns,
        "coordinator_report_lag_s": r1(statistics.mean(lags)) if lags else None,
        "coordinator_report_lag_max_s": r1(max(lags)) if lags else None,
        "unsafe_kill_commands": unsafe,
        "assignments": assigns,
    }


def read_run_artifacts(run_dir: Path | None) -> tuple[list[dict] | None, bytes | None]:
    """(incident entries, prime bytes) from a run dir's .superlite/, None where the file is absent."""
    if run_dir is None:
        return None, None
    sl = Path(run_dir) / ".superlite"
    log = sl / "incidents.jsonl"
    entries = None
    if log.is_file():
        entries = []
        for ln in log.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                e = json.loads(ln)
            except ValueError:
                continue
            if isinstance(e, dict):
                entries.append(e)
    prime = sl / "prime.md"
    return entries, (prime.read_bytes() if prime.is_file() else None)


def dispatch_stats(events: list[dict], run_dir: Path | None = None) -> dict:
    """Per-dispatch cache/context metrics, coordinator context, concurrency and a phase timeline.
    Assistant events are deduped by message.id; output_tokens is deliberately not used (it is an
    early-stream snapshot). Subagent events carry parent_tool_use_id == the coordinator's Agent
    tool_use id (also for SendMessage-resumed agents); an unknown parent becomes its own dispatch."""
    stamps = [t for t in map(_ts, events) if t is not None]
    t0 = min(stamps) if stamps else 0.0
    seen: set[str] = set()
    coord: list[int] = []
    agent_inputs: dict[str, dict] = {}   # Agent tool_use id -> input
    calls: dict[str, list[dict]] = {}    # dispatch id -> its API calls, in order
    span: dict[str, list[float]] = {}
    evtype: dict[str, str] = {}
    plan_write: float | None = None
    for idx, e in enumerate(events):
        if e.get("type") not in ("assistant", "user"):
            continue
        t = _ts(e)
        t = (t - t0) if t is not None else float(idx)
        parent = e.get("parent_tool_use_id")
        if parent:
            sp = span.setdefault(parent, [t, t])
            sp[1] = max(sp[1], t)
            if e.get("subagent_type"):
                evtype.setdefault(parent, e["subagent_type"])
        if e.get("type") != "assistant":
            continue
        msg = e.get("message") or {}
        # Each content block arrives as its own event sharing one message.id: scan blocks on every
        # event, but count a message's usage once.
        for b in msg.get("content") or []:
            if b.get("type") != "tool_use":
                continue
            inp = b.get("input") or {}
            if not parent and b.get("name") in ("Agent", "Task"):
                agent_inputs[b["id"]] = inp
            if (b.get("name") == "Write" and plan_write is None
                    and re.search(r"(^|/)docs/.*(plan|spec)", str(inp.get("file_path", "")).replace("\\", "/"), re.I)):
                plan_write = t
        mid = msg.get("id")
        if mid in seen or not isinstance(msg.get("usage"), dict):
            continue
        seen.add(mid)
        u = msg["usage"]
        if parent:
            calls.setdefault(parent, []).append({"model": msg.get("model") or "", "u": u})
        else:
            coord.append(_ctx(u))
    order = list(agent_inputs) + [p for p in span if p not in agent_inputs]
    order.sort(key=lambda p: span[p][0] if p in span else float("inf"))
    dispatches = []
    for did in order:
        inp = agent_inputs.get(did, {})
        cs = calls.get(did, [])
        sp = span.get(did)
        dispatches.append({
            "subagent_type": inp.get("subagent_type") or evtype.get(did) or "general-purpose",
            "model": (cs[0]["model"] if cs else inp.get("model") or "").replace("claude-", "") or None,
            "effort": inp.get("effort"),
            "description": inp.get("description"),
            "api_calls": len(cs),
            "first_call_cache_read": (cs[0]["u"].get("cache_read_input_tokens") or 0) if cs else None,
            "first_call_cache_write": (cs[0]["u"].get("cache_creation_input_tokens") or 0) if cs else None,
            "peak_context": max((_ctx(c["u"]) for c in cs), default=0),
            "total_cache_read": sum(c["u"].get("cache_read_input_tokens") or 0 for c in cs),
            "total_cache_write": sum(c["u"].get("cache_creation_input_tokens") or 0 for c in cs),
            "start_s": round(sp[0], 1) if sp else None,
            "end_s": round(sp[1], 1) if sp else None,
        })

    # Cache sharing: a sibling's first call that reads well beyond its type's bare system prefix.
    by_type: dict[str, list[dict]] = {}
    for d in dispatches:
        if d["api_calls"]:
            by_type.setdefault(d["subagent_type"], []).append(d)
    hits = total = 0
    for ds in by_type.values():
        floor = min(d["first_call_cache_read"] for d in ds)
        for d in ds[1:]:
            total += 1
            hits += d["first_call_cache_read"] > floor + 1000
    # Concurrency: sweep the dispatch intervals (ends sort before starts at the same instant).
    marks = sorted([(d["start_s"], 1) for d in dispatches if d["start_s"] is not None]
                   + [(d["end_s"], -1) for d in dispatches if d["end_s"] is not None], key=lambda m: (m[0], m[1]))
    cur = peak = 0
    for _, delta in marks:
        cur += delta
        peak = max(peak, cur)

    def kind(d: dict, word: str) -> bool:
        return word in ((d["subagent_type"] or "") + " " + (d["description"] or "")).lower()

    impls = [d for d in dispatches if d["start_s"] is not None and
             (d["subagent_type"].endswith("implementer") or _IMPL_DESC_RE.match((d["description"] or "").lower()))]
    finals = [d for d in dispatches if d["start_s"] is not None and kind(d, "review") and kind(d, "final")
              and not kind(d, "fix") and not kind(d, "re-review")]
    final = min(finals, key=lambda d: d["start_s"]) if finals else None
    last_t = max((t - t0 for t in stamps), default=None)
    flow = flow_metrics(events, t0, last_t, len(coord), *read_run_artifacts(run_dir))
    timeline = {
        "plan_write_s": round(plan_write, 1) if plan_write is not None else None,
        "first_implementer_s": min(d["start_s"] for d in impls) if impls else None,
        "last_implementer_end_s": max(d["end_s"] for d in impls) if impls else None,
        # Build tasks only: a resumed agent's span also covers its idle time and fix rounds.
        "last_build_end_s": flow["last_build_end_s"],
        "final_review_start_s": final["start_s"] if final else None,
        "final_review_end_s": final["end_s"] if final else None,
        "last_event_s": round(last_t, 1) if last_t is not None else None,
    }
    return {
        "dispatches": dispatches,
        "sibling_first_call_hit_rate": round(hits / total, 3) if total else None,
        "fork_dispatches": sum(1 for d in dispatches if d["subagent_type"] == "fork"),
        "reviewer_dispatches": sum(1 for d in dispatches if kind(d, "review")),
        "implementer_dispatches": len(impls),
        "orchestrated": len(impls) >= 2,
        "coordinator_calls": len(coord),
        "coordinator_context_median": int(statistics.median(coord)) if coord else None,
        "coordinator_context_max": max(coord, default=None),
        "max_concurrent_agents": peak,
        "timeline": timeline,
        **flow,
    }


def _score_from_junit(junit: Path, key: str) -> dict:
    if not junit.is_file() or junit.stat().st_size == 0:
        return {f"{key}_passed": 0, f"{key}_total": None, f"{key}_failed_ids": ["(no junit)"]}
    total, failed, by_file = junit_counts(junit)
    return {f"{key}_passed": total - len(failed), f"{key}_total": total, f"{key}_failed_ids": failed,
            f"{key}_by_file": {k: f"{p}/{t}" for k, (p, t) in sorted(by_file.items())}}


def hidden_score(run_dir: Path, suite: Path | None = None, key: str = "hidden") -> dict:
    suite = suite or TASK_HIDDEN
    with tempfile.TemporaryDirectory() as tmp:
        ignore = [".git", ".venv", "__pycache__"]
        if (suite / "run.sh").is_file():
            ignore += ["node_modules", "dist"]
        work = Path(tmp) / "w"
        shutil.copytree(run_dir, work, ignore=shutil.ignore_patterns(*ignore))
        shutil.copytree(suite, work / "hidden_tests")
        junit = Path(tmp) / "junit.xml"
        if (suite / "run.sh").is_file():
            # The suite brings its own runner, so any language scores the same way: `bash hidden_tests/run.sh`
            # in the temp copy, writing JUnit XML to $JUNIT; $HIDDEN_DIR names the suite directory.
            env = with_node(dict(os.environ, JUNIT=str(junit), HIDDEN_DIR="hidden_tests"))
            subprocess.run([bash_exe(), "hidden_tests/run.sh"], cwd=work, env=env, capture_output=True, timeout=900)
            return _score_from_junit(junit, key)
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(work / "src"), str(work)]))
        # A task may list extra test-only packages (e.g. playwright) in hidden/requirements.txt.
        req = TASK_HIDDEN / "requirements.txt"  # the task's extras serve every suite
        pkgs = [ln.strip() for ln in (req.read_text(encoding="utf-8").splitlines() if req.exists() else [])]
        extras = [w for pkg in pkgs if pkg and not pkg.startswith("#") for w in ("--with", pkg)]
        subprocess.run(
            ["uv", "run", "--no-project", "--with", "pytest", *extras, "python", "-m", "pytest",
             "hidden_tests", "-q", "-p", "no:cacheprovider", f"--junitxml={junit}"],
            cwd=work, env=env, capture_output=True, timeout=900,
        )
        return _score_from_junit(junit, key)


def own_tests(run_dir: Path) -> dict:
    script = TASK_HIDDEN / "own_tests.sh"
    if script.is_file():  # task-supplied runner (cwd = the run dir), JUnit XML via $JUNIT
        with tempfile.TemporaryDirectory() as tmp:
            junit = Path(tmp) / "junit.xml"
            env = with_node(dict(os.environ, JUNIT=str(junit)))
            subprocess.run([bash_exe(), str(script)], cwd=run_dir, env=env, capture_output=True, timeout=600)
            if not junit.is_file() or junit.stat().st_size == 0:
                return {"own_tests_summary": "(no junit)"}
            total, failed, _ = junit_counts(junit)
            return {"own_tests_summary": f"{total - len(failed)} passed, {len(failed)} failed"}
    proc = subprocess.run(
        ["uv", "run", "--no-project", "--with", "pytest", "python", "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "--ignore=hidden_tests"],
        cwd=run_dir, env=dict(os.environ, PYTHONPATH=os.pathsep.join([str(run_dir / "src"), str(run_dir)])), capture_output=True, text=True, timeout=600,
    )
    tail = proc.stdout.strip().splitlines()[-1:] or [""]
    return {"own_tests_summary": tail[0]}


def _load_plan_tools():
    sdir = str(ROOT / "skills" / "swarm" / "scripts")
    if sdir not in sys.path:
        sys.path.insert(0, sdir)
    import plan_tools
    return plan_tools


def _run_plan(run_dir: Path):
    """The run's plan (the newest plan with a `%% taskgraph` block, else the newest plan) or None."""
    plans = sorted((run_dir / "docs" / "plans").glob("*.md"), key=lambda p: (p.stat().st_mtime, p.name))
    texts = {p: p.read_text(encoding="utf-8-sig", errors="replace") for p in plans}
    with_graph = [p for p in plans if re.search(r"^\s*%%\s*taskgraph\b", texts[p], re.M)]
    chosen = (with_graph or plans)[-1:]
    if not chosen:
        return None
    return _load_plan_tools().Plan(texts[chosen[0]], chosen[0])


def task_records(run_dir: Path, dispatches: list[dict], events: list[dict] | None = None) -> list[dict]:
    """Per-task observations for `predict.py --refit`: each implementer dispatch (task id from its
    description, e.g. "T4 sitegen search/links" or "Implementer G (T8)") joined to that task's plan
    features. Re-dispatches of one task sum their durations. Tasks with no plan match are skipped.
    With the stream `events`, each record also carries the mechanical-task measurements
    (bench/mechanical.py: tokens, cost, tool calls, first_time_pass, rework, critical path, ...)."""
    plan = _run_plan(run_dir)
    if plan is None:
        return []
    pt = _load_plan_tools()
    secs: dict[str, float] = {}
    models: dict[str, str] = {}
    for d in dispatches:
        m = re.search(r"\b(T\d+)\b", d.get("description") or "")
        if (not m or "implementer" not in (d.get("subagent_type") or "")
                or d.get("start_s") is None or d.get("end_s") is None):
            continue
        tid = m.group(1)
        secs[tid] = secs.get(tid, 0.0) + (d["end_s"] - d["start_s"])
        models.setdefault(tid, d.get("model"))
    out = []
    for tid, s in secs.items():
        task = plan.by_id.get(tid)
        feats = task.explicit_features if task else {}
        if not task or "loc" not in feats:
            continue
        out.append({"task": tid, "components": feats.get("components", 1), "loc": feats["loc"],
                    "tests": feats.get("tests", "fast"), "kind": feats.get("kind", "design"), "model": feats.get("model") or models.get(tid),
                    "actual_s": round(s, 1)})
    out.sort(key=lambda r: pt.nkey(r["task"]))
    if events is not None:
        try:
            graph = pt.parse_taskgraph(plan)
        except pt.PlanError:
            graph = {}
        out = mechanical.task_metrics(events, out, plan, graph, run_dir, PRICES, mechanical.load_rules(ROUTING_JSON))
    return out


def load_speeds(path: Path = MODELS_JSON) -> dict[str, float]:
    return {k: float(m["output_tps"]) for k, m in json.loads(Path(path).read_text(encoding="utf-8")).get("models", {}).items()
            if m.get("output_tps")}


def mechanical_score(run_dir: Path, events: list[dict], dispatches: list[dict], baseline: str = "sonnet",
                     select: str | None = None) -> tuple[list[dict], dict]:
    """(`tasks` records, `mechanical` block) for one run, from its stream, plan and .superlite/ notes."""
    tasks = task_records(run_dir, dispatches, events)
    plan = _run_plan(run_dir)
    try:
        graph = _load_plan_tools().parse_taskgraph(plan) if plan is not None else {}
    except Exception:
        graph = {}
    tids = set(plan.by_id) if plan is not None else set()
    block = mechanical.mechanical_block(tasks, graph, PRICES, load_speeds(), plan,
                                        mechanical.other_haiku(events, PRICES, tids), baseline, select)
    return tasks, block


def cmd_rescore_tasks(arm: str, rep: str) -> None:
    """Add/refresh the `tasks` and `mechanical` keys of an existing score JSON without rerunning the
    hidden suites. A run that was killed before scoring has no score JSON: its metrics go to
    `<name>.mechanical.json` instead (same two keys)."""
    name = f"{PREFIX}{arm}-{rep}"
    path = RESULTS / f"{name}.json"
    events = read_events(RESULTS / f"{name}.jsonl")
    if path.is_file():
        score = json.loads(path.read_text(encoding="utf-8"))
        dispatches = score.get("dispatches") or []
    else:
        score, path = {"run": name}, RESULTS / f"{name}.mechanical.json"
        dispatches = dispatch_stats(events, RUNS / name)["dispatches"]
    score["tasks"], score["mechanical"] = mechanical_score(RUNS / name, events, dispatches)
    path.write_text(json.dumps(score, indent=2), encoding="utf-8")
    print(f"{path.name}: {len(score['tasks'])} task records")


def cmd_score(arm: str, rep: str) -> dict:
    name = f"{PREFIX}{arm}-{rep}"
    run_dir = RUNS / name
    meta_path = RESULTS / f"{name}.meta.json"
    deadline = time.time() + 8 * 3600
    while not meta_path.exists() and time.time() < deadline:
        time.sleep(60)  # the run is still going (e.g. launched in parallel); meta is written at its end
    events = read_events(RESULTS / f"{name}.jsonl")
    res = results_of(events)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    # A session emits a result event each time the coordinator idles while background agents
    # run; cost and modelUsage in each are cumulative for the session, so keep only the last
    # result per session (resumes start new sessions). num_turns is per segment, so it sums.
    last_per_session: dict[str, dict] = {}
    for r in res:
        last_per_session[r.get("session_id") or ""] = r
    turns = sum(r.get("num_turns") or 0 for r in res)
    res_all, res = res, list(last_per_session.values())
    model_usage: dict[str, dict] = {}
    for r in res:
        for model, u in (r.get("modelUsage") or {}).items():
            agg = model_usage.setdefault(model, {})
            for k, v in u.items():
                if isinstance(v, (int, float)):
                    agg[k] = agg.get(k, 0) + v
    src_loc = count_src_loc(run_dir)
    commits = subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=run_dir,
                             capture_output=True, text=True).stdout.strip()
    score = {
        **meta,
        "cost_usd": round(sum(r.get("total_cost_usd") or 0 for r in res), 2),
        "turns": turns,
        "api_s": round(sum(r.get("duration_api_ms") or 0 for r in res) / 1000, 1),
        "result_events": len(res_all),
        "completed_marker": bool(res_all) and MARKER in (res_all[-1].get("result") or ""),
        "last_subtype": res_all[-1].get("subtype") if res_all else None,
        **stream_stats(events),
        **dispatch_stats(events, run_dir),
        # The stream's last event excludes decider time spent between resumes; arm_wall_s is the real clock.
        **({"actual_total_s": meta["arm_wall_s"]} if "arm_wall_s" in meta else {}),
        "model_usage": model_usage,
        "tasks": [],  # filled below from the dispatches and the run's plan
        "src_loc": src_loc,
        "commits": int(commits or 0),
        **own_tests(run_dir),
        **hidden_score(run_dir),
        # Optional beyond-SPEC robustness probes (bench/tasks/<task>/robustness/), scored separately.
        **(hidden_score(run_dir, TASK_HIDDEN.parent / "robustness", "robust")
           if (TASK_HIDDEN.parent / "robustness").is_dir() else {}),
    }
    score["tasks"], score["mechanical"] = mechanical_score(run_dir, events, score.get("dispatches") or [])
    (RESULTS / f"{name}.json").write_text(json.dumps(score, indent=2), encoding="utf-8")
    brief = {k: v for k, v in score.items() if k not in ("model_usage", "hidden_failed_ids", "dispatches", "assignments")}
    print(json.dumps(brief, indent=2))
    return score


def cmd_report() -> None:
    paths = [p for p in sorted(RESULTS.glob("*-*.json"))
             if re.fullmatch(rf"{re.escape(PREFIX)}(superpowers|superlite)-[a-z]?\d+\.json", p.name)]
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    for r in rows:
        r["decisions"] = r.get("resumes") if r.get("mode") == "human" else ""
        r["arm_wall_s"] = r.get("arm_wall_s", r.get("wall_s"))
        r["sibling_hit_rate"] = r.get("sibling_first_call_hit_rate", "")
        r["coord_ctx_median"] = r.get("coordinator_context_median", "")
    cols = ["arm", "rep", "wall_s", "arm_wall_s", "cost_usd", "turns", "resumes", "decisions", "agent_dispatches",
            "coordinator_tool_calls", "sibling_hit_rate", "coord_ctx_median", "hidden_passed", "hidden_total",
            "own_tests_summary", "src_loc", "commits"]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        lines.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
    print("\n".join(lines))


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    if args[0] == "run":
        cmd_run(args[1], args[2], "--smoke" in args, "--human" in args)
    elif args[0] == "score":
        cmd_score(args[1], args[2])
    elif args[0] == "rescore-tasks":
        cmd_rescore_tasks(args[1], args[2])
    elif args[0] == "report":
        cmd_report()
    else:
        sys.exit(__doc__)
