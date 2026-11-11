"""Regression tests for past Copilot review findings that had no covering test (docs/plans/2026-11-01-simplify-bloat.findings.md).

One test per finding, named test_pr<NN>_<short>. A strict xfail marks a finding whose bug is back in today's code.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

import fake_agent
import scenario

ROOT = scenario.ROOT
SCRIPTS = scenario.SCRIPTS
PROBE_RUN = ROOT / "skills" / "swarm" / "probes" / "probe_run.py"
FIXTURES = Path(__file__).parent / "fixtures"
FLOW_PLAN = FIXTURES / "flow_plan.md"


@pytest.fixture
def repo(tmp_path):
    return scenario.Repo(tmp_path)


def text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def sessions_json(repo) -> dict:
    return repo.state("sessions.json")


# ---- PR 26: CI -------------------------------------------------------------------------------------------------

def test_pr26_ci_reverse_dns_step_cannot_hang_the_job():
    """Finding 1: the diagnostic step must print the host first, run each lookup in a subprocess with a timeout,
    and catch the timeout, so a hung lookup neither blocks pytest nor stays silent."""
    yml = text(".github/workflows/tests.yml")
    step = yml.split("Time reverse-DNS lookups", 1)[1].split("- name: Run tests under /bin/bash 3.2", 1)[0]
    assert "subprocess.run" in step and "timeout=" in step
    assert "except subprocess.TimeoutExpired" in step
    assert step.index("Looking up") < step.index("getfqdn")  # the host is printed before the lookup starts


# ---- PR 25: sessions -------------------------------------------------------------------------------------------

def test_pr25_close_clean_worktree_keeps_branch_with_unmerged_commits(repo):
    """Finding 36: closing a clean owned worktree must not delete a branch that holds unmerged commits."""
    assert repo.cli("session", "create", "--slug", "p", "--worktree").code == 0
    wt = Path(repo.session_row("p")["worktree"])
    (wt / "work.txt").write_text("committed work\n", encoding="utf-8")
    repo.git("add", "work.txt", cwd=wt)
    repo.git("commit", "-q", "-m", "work", cwd=wt)
    branch = repo.session_row("p")["branch"]
    r = repo.cli("session", "close", "--slug", "p")
    assert r.code == 0, r.err
    assert not wt.exists()
    assert repo.git("rev-parse", "--verify", "-q", branch).strip()  # the branch and its commit survive
    assert repo.session_row("p") is None  # the row leaves the registry...
    manifest = repo.state("session-store/p/session.json")  # ...and the archive manifest lets the session come back
    assert manifest and manifest["slug"] == "p" and manifest["branch"] == branch and manifest["run"] == "finished"


# ---- PR 21: sessions -------------------------------------------------------------------------------------------

def test_pr21_create_refuses_an_existing_directory_that_is_not_a_worktree(repo):
    """Finding 63: a plain directory already sitting at the session path is never adopted, and no branch changes."""
    (repo.root / ".claude" / "worktrees" / "sessions-n").mkdir(parents=True)
    r = repo.cli("session", "create", "--slug", "n", "--worktree")
    assert r.code == 2 and "already exists" in r.err
    assert repo.branch() == "main" and repo.session_row("n") is None


def test_pr21_split_will_not_pair_a_parked_keep_session_with_the_active_branch(repo):
    """Finding 64: a is parked, b active in the same host; split --keep a must not rewrite a's row with b's branch."""
    assert repo.cli("session", "ensure", "--slug", "a").code == 0
    assert repo.cli("session", "create", "--slug", "b", "--untracked", "keep").code == 0
    a_before = dict(repo.session_row("a"))
    repo.write(".superlite/orchestrator.json", json.dumps({"mode": "ai", "approved": False}))
    r = repo.cli("session", "split", "--keep", "a", "--new", "c")
    assert r.code == 2, r.err
    assert repo.session_row("a") == a_before and repo.session_row("c") is None
    assert repo.session_row("a")["branch"] == "superlite/sessions/a"


def test_pr21_discard_stops_when_git_refuses_to_remove_the_worktree(repo):
    """Finding 66: a locked worktree makes discard fail, and the row, worktree and branch all stay."""
    assert repo.cli("session", "create", "--slug", "p", "--worktree").code == 0
    wt = Path(repo.session_row("p")["worktree"])
    repo.git("worktree", "lock", str(wt))
    r = repo.cli("session", "discard", "--slug", "p")
    assert r.code != 0
    assert wt.is_dir() and repo.session_row("p") is not None
    assert repo.git("rev-parse", "--verify", "-q", "superlite/sessions/p").strip()


def test_pr21_split_from_an_owned_worktree_keeps_it_owned(repo):
    """Finding 67: after splitting from a superlite-made worktree, discard still removes that worktree."""
    assert repo.cli("session", "create", "--slug", "p", "--worktree").code == 0
    wt = Path(repo.session_row("p")["worktree"])
    repo.write(".superlite/orchestrator.json", "{}")  # the main checkout has none; the run lives in the worktree
    (wt / ".superlite").mkdir(exist_ok=True)
    (wt / ".superlite" / "orchestrator.json").write_text(json.dumps({"mode": "ai"}), encoding="utf-8")
    r = repo.cli("session", "split", "--new", "q", cwd=wt)
    assert r.code == 0, r.err
    assert repo.session_row("p")["owned"] is True
    assert repo.cli("session", "discard", "--slug", "p").code == 0
    assert not wt.exists()


# ---- PR 18 / PR 15 ---------------------------------------------------------------------------------------------

def _serve_args(tmp_path, monkeypatch, args_src: str):
    """Run the live-server adapter's argument resolver against a throwaway module; returns (positional, keywords)."""
    sys.path.insert(0, str(SCRIPTS))
    import review_adapter
    mod = tmp_path / "pf_serve_mod.py"
    mod.write_text("DEFAULTS = {'a': 1, 'debug': True}\nOPTIONS = {'host': 'h', 'port': 7}\n"
                   "class Settings:\n    def __init__(self, **kw):\n        self.kw = kw\n"
                   "def create(*args, **kwargs):\n    return args, kwargs\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    ns: dict = {}
    exec(review_adapter._SERVE_SNIPPET, ns)
    got, _fn = ns["_make_server"]("pf_serve_mod.create", args_src)
    sys.modules.pop("pf_serve_mod", None)
    return got


def test_pr15_serve_args_resolve_builtins(tmp_path, monkeypatch):
    """Finding 75: serve=pkg.create(dict(debug=True)) still works."""
    args, kwargs = _serve_args(tmp_path, monkeypatch, "dict(debug=True)")
    assert args == ({"debug": True},)


def test_pr15_serve_args_merge_dict_unpack(tmp_path, monkeypatch):
    """Finding 76: {**DEFAULTS, 'debug': False} keeps every setting from DEFAULTS."""
    args, kwargs = _serve_args(tmp_path, monkeypatch, "{**DEFAULTS, 'debug': False}")
    assert args == ({"a": 1, "debug": False},)


def test_pr15_serve_args_expand_keyword_unpacking(tmp_path, monkeypatch):
    """Finding 77: create(**OPTIONS) and nested Settings(**OPTIONS) receive the mapping."""
    args, kwargs = _serve_args(tmp_path, monkeypatch, "**OPTIONS")
    assert kwargs.get("host") == "h" and kwargs.get("port") == 7


# ---- PR 14 -----------------------------------------------------------------------------------------------------

def test_pr14_slug_cannot_walk_out_of_the_session_store(repo):
    """Finding 85 (end() deleted a directory from an unvalidated id): discard builds its deletion path from the slug,
    so a traversal slug is rejected before anything is removed."""
    (repo.root / "src").mkdir()
    (repo.root / "src" / "keep.py").write_text("x = 1\n", encoding="utf-8")
    for bad in ("../../src", "../..", "a/../../.."):
        r = repo.cli("session", "discard", "--slug", bad)
        assert r.code != 0, bad
    assert (repo.root / "src" / "keep.py").is_file()


def test_pr14_probe_run_default_out_follows_root_not_cwd(tmp_path):
    """Finding 111: run from a parent directory with a relative --root, the results and log land in <root>/.superlite,
    not <root>/<root>/.superlite."""
    proj = tmp_path / "proj"
    shutil.copytree(FIXTURES / "probe_lib", proj)
    p = subprocess.run([sys.executable, str(PROBE_RUN), "--root", "proj", "--adapter", "proj/adapter.py",
                        "--categories", "state", "--files", "src/parser.py"], cwd=tmp_path,
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr
    assert (proj / ".superlite" / "probes" / "probe-results.json").is_file()
    assert (proj / ".superlite" / "incidents.jsonl").is_file()
    assert not (proj / "proj").exists() and not (tmp_path / "proj" / "proj").exists()


def test_pr14_probe_run_background_keeps_space_separated_out_and_log(tmp_path):
    """Finding 112: --background with `--out custom.json --log custom.jsonl` hands the child both flags and values."""
    proj = tmp_path / "proj"
    shutil.copytree(FIXTURES / "probe_lib", proj)
    p = subprocess.run([sys.executable, str(PROBE_RUN), "--root", str(proj), "--adapter", str(proj / "adapter.py"),
                        "--categories", "state", "--files", "src/parser.py", "--out", "custom.json", "--log", "custom.jsonl", "--background"],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0 and "started" in json.loads(p.stdout)
    out = proj / "custom.json"
    end = time.time() + 60
    while time.time() < end and not out.exists():
        time.sleep(0.2)
    assert out.is_file(), "the child exited with an argument error: --out custom.json was lost"
    assert not (proj / ".superlite" / "probes" / "probe-results.json").exists()


# ---- PR 7 ------------------------------------------------------------------------------------------------------

def test_pr7_exclusive_settings_write_does_not_block_checkpoints(repo):
    """Finding 119: on a Claude Code project `orchestrator set` writes .claude/settings.local.json; the run's
    checkpoints must still pass with that file present (it is bookkeeping, not an out-of-scope change)."""
    assert repo.cli("session", "ensure", "--slug", "run").code == 0
    (repo.root / ".claude").mkdir()
    assert repo.cli("orchestrator", "set", "ai", env={"CLAUDE_CODE": "1"}).code == 0
    settings = repo.root / ".claude" / "settings.local.json"
    assert settings.is_file()  # the suspension did write the file
    plan = repo.write("docs/plans/2026-11-01-flow.md", FLOW_PLAN.read_text(encoding="utf-8"))
    assert repo.cli("plan_tools", "lint", "--plan", plan, "--stable").code == 0
    assert repo.cli("dispatch", "--plan", plan).code == 0
    for tid in ("T1", "T2", "T3", "T4"):
        fake_agent.do(tid, plan, repo)
    r = repo.cli("checkpoint", "--plan", plan, "T1", "T2", "T3", "T4")
    assert r.code == 0 and "out-of-scope" not in r.out, r.out + r.err


def test_checkpoint_ignores_a_harness_worktree_in_the_unhidden_home(repo):
    """A hub holding a harness-made .claude/worktrees/<x> checkout (home not hidden yet) never blocks a checkpoint."""
    repo.git("worktree", "add", "-q", "-b", "worktree-agent-1", repo.root / ".claude" / "worktrees" / "agent-1")
    plan = repo.write("docs/plans/2026-11-01-flow.md", FLOW_PLAN.read_text(encoding="utf-8"))
    assert repo.cli("plan_tools", "lint", "--plan", plan, "--stable").code == 0
    assert repo.cli("dispatch", "--plan", plan).code == 0
    for tid in ("T1", "T2", "T3", "T4"):
        fake_agent.do(tid, plan, repo)
    r = repo.cli("checkpoint", "--plan", plan, "T1", "T2", "T3", "T4")
    assert r.code == 0 and "out-of-scope" not in r.out, r.out + r.err


def test_pr14_guard_denies_writes_to_protected_state_files(tmp_path):
    """Finding 79: during a run, Write/Edit of orchestrator.json and human-replies.jsonl is denied by the pre-tool hook."""
    sd = tmp_path / ".superlite"
    sd.mkdir()
    (sd / "orchestrator.json").write_text('{"mode":"ai","approved":false}', encoding="utf-8")
    (sd / "pool.json").write_text("{}", encoding="utf-8")  # an active run: the guard enforces
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PROJECT_DIR", "CURSOR_PROJECT_DIR")}
    env["CLAUDE_PROJECT_DIR"] = str(tmp_path)
    bash = scenario.BASH
    for name in ("orchestrator.json", "human-replies.jsonl"):
        for tool in ("Write", "Edit"):
            payload = json.dumps({"tool_name": tool, "tool_input": {"file_path": str(sd / name)}})
            r = subprocess.run([bash, str(ROOT / "hooks" / "pre-tool")], input=payload, capture_output=True, text=True,
                               env=env, timeout=60)
            assert r.returncode == 2, (tool, name, r.stdout, r.stderr)


@pytest.mark.xfail(strict=True, reason="bug is back: .opencode/INSTALL.md says a run suspends the plugin, but "
                                       ".opencode/plugins/superlite.js never reads the peers.json marker")
def test_pr7_opencode_install_claim_matches_the_bootstrap():
    """Finding 122: INSTALL.md advertises suspension only if the native bootstrap reads the suspension marker."""
    install = text(".opencode/INSTALL.md")
    plugin = text(".opencode/plugins/superlite.js")
    if "suspends" in install:
        assert "peers.json" in plugin
