"""Red-phase tests for the Copilot MEDIUM findings on PR #25 (M1-M10): exclusion, status, slugs, HUD, portability, lifecycle text."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "swarm" / "scripts"
SESSION = SCRIPTS / "session.py"
HUD = SCRIPTS / "session_hud.py"
HOOKS = ROOT / "hooks"
BASH = shutil.which("bash")

sys.path.insert(0, str(SCRIPTS))

import session  # noqa: E402


def _env(**extra):
    e = dict(os.environ, PYTHONIOENCODING="utf-8")
    for k in ("SUPERLITE_NO_AUTO_SESSION", "CLAUDE_PLUGIN_ROOT", "CLAUDE_CODE", "SUPERLITE_NO_SESSION_HUD"):
        e.pop(k, None)
    e.update(extra)
    return e


def run(*args, cwd=None):
    cp = subprocess.run([sys.executable, str(SESSION), *map(str, args)], capture_output=True, cwd=cwd, env=_env(),
                        timeout=120)
    cp.out, cp.err = cp.stdout.decode("utf-8"), cp.stderr.decode("utf-8")
    cp.both = cp.out + cp.err
    return cp


def git(cwd, *args, check=True):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=check)


def out(cwd, *args):
    return git(cwd, *args).stdout


def make_repo(r: Path, gitignore: str) -> Path:
    r.mkdir()
    out(r, "init", "-q", "-b", "main")
    out(r, "config", "user.email", "t@example.com")
    out(r, "config", "user.name", "t")
    (r / ".gitignore").write_text(gitignore)
    (r / "app.py").write_text("one\ntwo\nthree\n")
    out(r, "add", "-A")
    out(r, "commit", "-q", "-m", "init")
    return r


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path.resolve() / "app", ".superlite/\n")


@pytest.fixture
def repo_noignore(tmp_path):
    return make_repo(tmp_path.resolve() / "app", "*.pyc\n")


def reg_of(r):
    return json.loads((r / ".superlite" / "sessions.json").read_text(encoding="utf-8"))


def row_of(r, slug):
    return next(s for s in reg_of(r)["sessions"] if s["slug"] == slug)


def exclude_lines(r):
    common = out(r, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    f = Path(common) / "info" / "exclude"
    return [ln.strip() for ln in f.read_text(encoding="utf-8").splitlines()] if f.is_file() else []


def status_lines(cwd):
    return [ln for ln in out(cwd, "status", "--porcelain").splitlines() if ".superlite" in ln]


# ---- M1 / M2: .superlite/ exclusion -----------------------------------------------------------

def test_m1_ensure_in_unregistered_linked_worktree_hides_superlite(repo_noignore):
    """Copilot 4432902460, 4432405057: adopting an unregistered linked worktree hides .superlite/ in every checkout."""
    repo = repo_noignore
    x = repo.parent / "x"
    out(repo, "worktree", "add", "-q", str(x), "-b", "x")
    cp = run("ensure", "--slug", "x", cwd=x)
    assert cp.returncode == 0, cp.both
    assert "registered x where it is" in cp.out, cp.out
    (x / ".superlite").mkdir(exist_ok=True)
    (x / ".superlite" / "orchestrator.json").write_text("{}\n")
    assert ".superlite/" in exclude_lines(repo)
    assert status_lines(repo) == [], status_lines(repo)
    assert status_lines(x) == [], status_lines(x)


def test_m2_exclusion_does_not_depend_on_tracked_gitignore(repo):
    """Copilot 4432902405: info/exclude gets .superlite/ even when main's tracked .gitignore already lists it."""
    cp = run("ensure", "--slug", "a", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert ".superlite/" in exclude_lines(repo), exclude_lines(repo)
    # a branch whose .gitignore lacks the entry must still not show .superlite after switching to it
    other = repo.parent / "nogi"
    out(repo, "worktree", "add", "-q", str(other), "-b", "nogi", "main")
    out(other, "rm", "-q", ".gitignore")
    out(other, "commit", "-q", "-m", "drop gitignore")
    out(repo, "worktree", "remove", "--force", str(other))
    out(repo, "switch", "-q", "nogi")
    assert not (repo / ".gitignore").exists()
    assert status_lines(repo) == [], status_lines(repo)


# ---- M3: status shows how far behind base ----------------------------------------------------

def advance_main(repo, n):
    for i in range(n):
        tip = out(repo, "rev-parse", "refs/heads/main").strip()
        new = out(repo, "commit-tree", "refs/heads/main^{tree}", "-p", tip, "-m", f"adv{i}").strip()
        out(repo, "update-ref", "refs/heads/main", new)


def test_m3_status_shows_main_plus_n_when_behind(repo):
    """Copilot 4432902431: `session status` appends ` | main:+N` for a row N commits behind the local base."""
    assert run("ensure", "--slug", "a", cwd=repo).returncode == 0
    cp = run("status", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert "main:+" not in cp.out, cp.out
    advance_main(repo, 2)
    cp = run("status", cwd=repo)
    assert cp.returncode == 0, cp.both
    row_line = next(ln for ln in cp.out.splitlines() if " a | branch" in ln)
    assert row_line.rstrip().endswith("| main:+2") or " | main:+2" in row_line, row_line


# ---- M4 / M5: slug collisions -----------------------------------------------------------------

def finish_active(repo):
    (repo / ".superlite").mkdir(exist_ok=True)
    (repo / ".superlite" / "orchestrator.json").write_text('{"mode":"ai"}\n')
    assert session.mark_run(repo, "finished")


def test_m4_ensure_is_idempotent_after_a_collision(repo):
    """Copilot 4432405046: after demo collided into demo-2, the same ensure --slug demo reports it open, no demo-3."""
    assert run("ensure", "--slug", "demo", cwd=repo).returncode == 0
    finish_active(repo)
    cp = run("ensure", "--slug", "demo", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert "using demo-2" in cp.out, cp.out
    cp = run("ensure", "--slug", "demo", cwd=repo)
    assert cp.returncode == 0, cp.both
    assert "demo-2 already open here" in cp.out, cp.out
    slugs = [s["slug"] for s in reg_of(repo)["sessions"]]
    assert "demo-3" not in slugs, slugs
    assert row_of(repo, "demo-2").get("topic") == "demo", row_of(repo, "demo-2")


def test_m5_collision_on_41_char_slug_yields_valid_slug(repo):
    """Copilot 4432405030: a 41-char slug that collides still gets a generated slug of <= 41 chars that validates."""
    long_slug = "a" * 41
    assert session.SLUG.match(long_slug)
    assert run("ensure", "--slug", long_slug, cwd=repo).returncode == 0
    finish_active(repo)
    cp = run("ensure", "--slug", long_slug, cwd=repo)
    assert cp.returncode == 0, cp.both
    m = re.search(r"slug \S+ taken; using (\S+)", cp.out)
    assert m, cp.out
    new = m.group(1)
    assert new != long_slug and len(new) <= 41 and session.SLUG.match(new), new
    cp = run("close", "--slug", new, cwd=repo)
    assert cp.returncode == 0, cp.both


# ---- M6 / M7: session-start project state -----------------------------------------------------

def start_hook(cwd, tmp_path, **extra):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = _env(HOME=str(home), SUPERLITE_HOME=str(home), SUPERLITE_PYTHON=sys.executable, **extra)
    return subprocess.run([BASH, str(HOOKS / "session-start")], capture_output=True, text=True, env=env,
                          cwd=str(cwd), timeout=120)


def plan_run_files(d: Path):
    (d / ".superlite").mkdir(exist_ok=True)
    (d / ".superlite" / "orchestrator.json").write_text(json.dumps({"mode": "ai"}))
    (d / "docs" / "plans").mkdir(parents=True, exist_ok=True)


def test_m6_finished_session_state_without_hud(repo, tmp_path):
    """Copilot 4432405017: with the HUD off, a run:finished session still gives `existing code` and no HUD line."""
    assert run("ensure", "--slug", "a", cwd=repo).returncode == 0
    plan_run_files(repo)
    assert session.mark_run(repo, "finished") == "a"
    r = start_hook(repo, tmp_path, SUPERLITE_NO_SESSION_HUD="1")
    assert r.returncode == 0, r.stderr
    assert "superlite session:" not in r.stdout, r.stdout
    assert "Project state: existing code" in r.stdout, r.stdout


@pytest.fixture
def nested(repo):
    assert run("ensure", "--slug", "mainsess", cwd=repo).returncode == 0
    n = repo / "nested"
    out(repo, "worktree", "add", "-q", str(n), "-b", "n")
    return repo, n


def test_m7_hud_in_nested_unregistered_worktree_does_not_name_main_session(nested):
    """Copilot 4432644447: session_hud in a worktree nested under main must not report main's active session."""
    repo, n = nested
    cp = subprocess.run([sys.executable, str(HUD), str(n)], capture_output=True, text=True, env=_env(), cwd=str(n),
                        timeout=60)
    assert cp.returncode == 0, cp.stderr
    assert "mainsess" not in cp.stdout, cp.stdout


def test_m7_project_state_in_nested_worktree_follows_its_own_orchestrator(nested, tmp_path):
    """Copilot 4432644447: the nested worktree's own orchestrator.json + docs/plans decide `run in progress`."""
    repo, n = nested
    plan_run_files(repo)
    assert session.mark_run(repo, "finished") == "mainsess"
    plan_run_files(n)
    r = start_hook(n, tmp_path)
    assert r.returncode == 0, r.stderr
    assert "mainsess" not in r.stdout, r.stdout
    assert "Project state: run in progress" in r.stdout, r.stdout


# ---- M8: portable bash in tests ---------------------------------------------------------------

@pytest.mark.parametrize("name", ["test_hooks_cwd_first.py", "test_session_run_state.py"])
def test_m8_no_hardcoded_bin_bash_executable(name):
    """Copilot 4432405069, 4432644458: tests must run bash through a resolved path, not a quoted /bin/bash literal."""
    text = (ROOT / "tests" / "scripts" / name).read_text(encoding="utf-8")
    hits = [ln for ln in text.splitlines() if re.search(r"""["']/bin/bash["']""", ln)]
    assert not hits, hits


# ---- M9 / M10: lifecycle text -----------------------------------------------------------------

@pytest.mark.parametrize("rel", ["commands/small.md", "commands/debug.md"])
def test_m9_command_runs_sl_finish_after_verification(rel):
    """Copilot 4432405002, 4432404971: small and debug end with `sl finish` in the session checkout."""
    text = (ROOT / rel).read_text(encoding="utf-8")
    assert "sl finish" in text, rel
    assert "uncommitted" in text.lower(), rel


def test_m9_inventing_bounded_path_runs_sl_finish():
    """Copilot 4432644430: the bounded paragraph of inventing/SKILL.md ends with `sl finish`."""
    text = (ROOT / "skills" / "inventing" / "SKILL.md").read_text(encoding="utf-8")
    m = re.search(r"^\*\*Bounded:\*\*.*?(?=\n\n|\Z)", text, re.S | re.M)
    assert m, "bounded paragraph not found"
    assert "sl finish" in m.group(0), m.group(0)
    assert "uncommitted" in m.group(0).lower(), m.group(0)


def test_m10_feature_step6_registers_integration_session_and_closes_start_session():
    """Copilot 4432404981: feature.md step 6 runs `sl session ensure` in the integration worktree and `sl session close`."""
    text = (ROOT / "commands" / "feature.md").read_text(encoding="utf-8")
    m = re.search(r"^6\. \*\*Integration build\.\*\*.*?(?=\n\n|\Z)", text, re.S | re.M)
    assert m, "step 6 not found"
    step = m.group(0)
    assert "sl session ensure" in step, step
    assert "sl session close" in step, step
