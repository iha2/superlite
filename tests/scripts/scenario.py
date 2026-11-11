"""Scenario harness: run the repo's own launcher by subprocess in a throwaway git repo and read back what it left.

Tests assert exit codes, files under the temp repo's .superlite/ and git state; wording only for the frozen lines.
"""
from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "swarm" / "scripts"
LAUNCHER = SCRIPTS / "superlite-cli"
BASH = shutil.which("bash") or "bash"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"}
FROZEN = {
    "session": re.compile(r"^SESSION worktree=(?P<path>.+?) slug=(?P<slug>\S+) branch=(?P<branch>\S+)$", re.M),
    "redirect": re.compile(r"^REDIRECT", re.M),
    "ask_untracked": re.compile(r"^ASK untracked=(?P<n>\d+) slug=(?P<slug>[^:]+):", re.M),
    "ask_move": re.compile(r"^ASK move slug=(?P<slug>[^:]+):", re.M),
    "dispatch_now": re.compile(r"^DISPATCH NOW:\s*(?P<ids>.*)$", re.M),
    "stable": re.compile(r"^STABLE:\s*(?P<ids>.*)$", re.M),
    "checkpoint": re.compile(r"^(?P<id>T\d+[a-z]*) (?P<sha>[0-9a-f]{40})$", re.M),
}

_template: Path | None = None


class Result(NamedTuple):
    code: int
    out: str
    err: str


def _run(argv, cwd: Path, env: dict, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", timeout=timeout)


def _make_template() -> Path:
    """One git init for the whole test run; each Repo copies it."""
    global _template
    if _template is not None:
        return _template
    tpl = Path(tempfile.mkdtemp(prefix="sl-scenario-tpl-")).resolve()
    atexit.register(shutil.rmtree, tpl, True)
    env = {**os.environ, **GIT_ENV}
    for argv in (["git", "init", "-q", "-b", "main"], ["git", "config", "user.name", "t"],
                 ["git", "config", "user.email", "t@example.com"], ["git", "config", "commit.gpgsign", "false"],
                 # no background maintenance: it writes lock files while Repo copies the template
                 ["git", "config", "maintenance.auto", "false"], ["git", "config", "gc.auto", "0"]):
        subprocess.run(argv, cwd=tpl, env=env, check=True, capture_output=True)
    (tpl / "README.md").write_text("scenario repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tpl, env=env, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tpl, env=env, check=True, capture_output=True)
    _template = tpl
    return tpl


class Repo:
    """A temp git repo with one commit on main. Everything it runs stays under `root`."""

    def __init__(self, tmp_path: Path):
        self.root = Path(tmp_path).resolve() / "repo"
        self.home = Path(tmp_path).resolve() / "_sl_home"
        shutil.copytree(_make_template(), self.root, ignore=shutil.ignore_patterns("*.lock"))
        self.home.mkdir(exist_ok=True)
        self.base_sha = self.git("rev-parse", "HEAD").strip()

    def env(self, extra: dict | None = None) -> dict:
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("CLAUDE_", "GIT_", "SUPERLITE_", "XDG_"))}
        env.update(GIT_ENV)
        env["SUPERLITE_HOME"] = str(self.home)
        env["HOME"] = env["USERPROFILE"] = str(self.home)
        env["XDG_CONFIG_HOME"] = str(self.home / "xdg-config")
        env["XDG_DATA_HOME"] = str(self.home / "xdg-data")
        env["XDG_CACHE_HOME"] = str(self.home / "xdg-cache")
        env.update(extra or {})
        return env

    def cli(self, *args, env: dict | None = None, cwd: Path | None = None) -> Result:
        """Run `superlite-cli <args>` from the repo's own checkout of the tools; returns code, stdout, stderr."""
        where = Path(cwd) if cwd else self.root
        assert str(where.resolve()).startswith(str(self.root.parent)), f"{where} escapes the temp repo"
        p = _run([BASH, str(LAUNCHER), *map(str, args)], where, self.env(env))
        return Result(p.returncode, p.stdout, p.stderr)

    def git(self, *args, cwd: Path | None = None) -> str:
        p = _run(["git", *map(str, args)], Path(cwd) if cwd else self.root, self.env())
        assert p.returncode == 0, f"git {' '.join(map(str, args))}: {p.stderr}"
        return p.stdout

    def state(self, name: str):
        """`.superlite/<name>`: parsed JSON for *.json, text otherwise, None if missing."""
        f = self.root / ".superlite" / name
        if not f.is_file():
            return None
        text = f.read_text(encoding="utf-8")
        return json.loads(text) if name.endswith(".json") else text

    def write(self, rel: str, text: str) -> Path:
        f = self.root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
        return f

    def branch(self) -> str:
        return self.git("rev-parse", "--abbrev-ref", "HEAD").strip()

    def log(self, ref: str = "main") -> list:
        return self.git("log", "--format=%H", ref).split()

    def session_row(self, slug: str) -> dict | None:
        reg = self.state("sessions.json") or {}
        return next((s for s in reg.get("sessions", []) if s["slug"] == slug), None)

    def human_reply(self, review: Path, text: str = "approve", approves: bool = True) -> None:
        """The line the UserPromptSubmit hook appends when the human answers (5 s after the review file was written)."""
        t = int(Path(review).stat().st_mtime * 1000) + 5000
        f = self.root / ".superlite" / "human-replies.jsonl"
        f.parent.mkdir(exist_ok=True)
        with f.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": t, "approves": approves, "text": text}) + "\n")

    def assert_closed_clean(self, slug: str) -> None:
        """A finished flow: session archived (row gone, manifest says finished), checkout back on main, main has no new commits."""
        assert self.session_row(slug) is None
        row = self.state(f"session-store/{slug}/session.json")
        assert row is not None and row["run"] == "finished" and row.get("closed_at"), row
        assert self.branch() == "main"
        assert self.log("main") == [self.base_sha]


def frozen(name: str, text: str):
    """The first match of a frozen output line, or None."""
    return FROZEN[name].search(text)


def end_flow(repo: Repo, slug: str, plan: str | None = None) -> None:
    """The tail every flow shares: finish (marks the session finished), then session close (back to main, files kept)."""
    args = ["finish", *(["--plan", plan] if plan else [])]
    r = repo.cli(*args)
    assert r.code == 0, r.err
    r = repo.cli("session", "close", "--slug", slug, "--untracked", "keep")
    assert r.code == 0, r.err
