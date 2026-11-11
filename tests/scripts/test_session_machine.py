"""Random sequences of session commands and file edits over one temp repo; invariants hold after every step.

SUPERLITE_MACHINE_EXAMPLES raises the example count for deeper runs (default 8, about 90 s; 50 takes about 10 min).
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, rule

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import session  # noqa: E402

SLUGS = ["a", "b", "c"]
EXAMPLES = int(os.environ.get("SUPERLITE_MACHINE_EXAMPLES", "8"))
ASK_UNTRACKED = re.compile(r"^ASK untracked=", re.M)
ASK_MOVE = re.compile(r"^ASK move ", re.M)


def git(cwd, *args, check=True):
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    assert p.returncode == 0 or not check, f"git {args}: {p.stderr}"
    return p.stdout


def sess(root, *args):
    """Run a session command in-process; returns (exit code, stdout + stderr)."""
    buf = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    err = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        try:
            rc = session.main([*map(str, args), "--root", str(root)])
        except SystemExit as e:  # argparse rejecting an argument
            rc = int(e.code or 0)
    buf.flush()
    return rc, buf.buffer.getvalue().decode("utf-8") + err.getvalue()


def slug_of(branch: str) -> str | None:
    return branch[len("superlite/sessions/"):] if branch.startswith("superlite/sessions/") else None


class SessionMachine(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.tmp = Path(tempfile.mkdtemp(prefix="sl-machine-")).resolve()
        self.root = self.tmp / "app"
        self.root.mkdir()
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "config", "user.email", "t@example.com")
        git(self.root, "config", "user.name", "t")
        (self.root / ".gitignore").write_text(".superlite/\n")
        for name in ("app.py", "main.txt"):
            (self.root / name).write_text(f"{name}\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "init")
        self.n = 0
        self.tokens: set = set()
        self.log: list = []
        self.stash_text: dict = {}

    def teardown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- helpers ----
    def worktrees(self) -> dict:
        """path -> branch name ('' when detached), from one `git worktree list`."""
        out, cur = {}, None
        for line in git(self.root, "worktree", "list", "--porcelain").splitlines():
            if line.startswith("worktree "):
                cur = Path(line[9:]).as_posix()
                out[cur] = ""
            elif line.startswith("branch ") and cur:
                out[cur] = line[len("branch refs/heads/"):]
        return {k: v for k, v in out.items() if Path(k).is_dir()}

    def checkouts(self) -> list:
        return [Path(p) for p in self.worktrees()]

    def branch_at(self, checkout: Path) -> str:
        return self.worktrees().get(Path(checkout).as_posix(), "")

    def token(self) -> str:
        self.n += 1
        return f"TOKEN{self.n}END"

    def run(self, *args, answers=("stash", "keep")):
        """Run a command, answering ASK move with yes and ASK untracked with the first of `answers`."""
        extra: list = []
        for _ in range(4):
            rc, out = sess(self.root, *args, *extra)
            self.log.append((args, tuple(extra), rc))
            if rc == 4 and ASK_MOVE.search(out) and "--move" not in extra:
                extra += ["--move", "yes"]
            elif rc == 4 and ASK_UNTRACKED.search(out) and "--untracked" not in extra:
                extra += ["--untracked", answers[0]]
            else:
                return rc, out
        return rc, out

    def holdings(self, wts: dict) -> dict:
        """One scan: scope -> all text held there (checkout files, stash patches, WIP refs). Scopes: ("co", path), ("stash", slug), ("wip", slug)."""
        held: dict = {}
        for co in map(Path, wts):
            chunks = []
            for dirpath, dirs, files in os.walk(co):
                rel = Path(dirpath).relative_to(co).as_posix()
                # nested session worktrees (old and new homes) are their own checkouts, scanned on their own
                dirs[:] = [d for d in dirs if d != ".git" and not (rel in (".superlite", ".claude") and d == "worktrees")]
                for name in files:
                    try:
                        chunks.append((Path(dirpath) / name).read_text(encoding="utf-8", errors="ignore"))
                    except OSError:
                        pass
            held[("co", co.as_posix())] = "\n".join(chunks)
        for line in git(self.root, "stash", "list", "--format=%H %gs").splitlines():
            ref, _, msg = line.partition(" ")
            m = re.search(r":(\S+)$", msg)
            if ref not in self.stash_text:  # a stash commit never changes, so read it once
                self.stash_text[ref] = git(self.root, "show", ref) + git(self.root, "show", f"{ref}^3", check=False)
            text = self.stash_text[ref]
            held[("stash", m.group(1) if m else msg)] = held.get(("stash", m.group(1) if m else msg), "") + text
        prefix = "refs/superlite/wip/"  # close parks dirty work on a private ref instead of the stash list
        for line in git(self.root, "for-each-ref", "--format=%(objectname) %(refname)", prefix).splitlines():
            oid, _, ref = line.partition(" ")
            if oid not in self.stash_text:
                self.stash_text[oid] = git(self.root, "show", oid)
            held[("wip", ref[len(prefix):])] = self.stash_text[oid]
        return held

    def where_found(self, token: str, held: dict | None = None) -> set:
        return {scope for scope, text in (held or self.holdings()).items() if token in text}

    def scopes_of(self, slug: str, wts: dict) -> set:
        return {("stash", slug), ("wip", slug)} | {("co", p) for p, b in wts.items() if slug_of(b) == slug}

    def known(self) -> list:
        """Registered slugs first (so most picks hit a real session), then the pool (so some miss)."""
        reg_file = self.root / ".superlite" / "sessions.json"
        have = [r["slug"] for r in json.loads(reg_file.read_text(encoding="utf-8"))["sessions"]] if reg_file.is_file() else []
        return have + have + SLUGS

    @initialize()
    def first_session(self):
        self.run("ensure", "--slug", "a")

    # ---- rules ----
    @rule(slug=st.sampled_from(SLUGS), answer=st.sampled_from(["stash", "keep"]))
    def ensure(self, slug, answer):
        self.run("ensure", "--slug", slug, answers=(answer,))

    @rule(data=st.data(), answer=st.sampled_from(["stash", "keep"]))
    def use(self, data, answer):
        slug = data.draw(st.sampled_from(self.known()))
        self.run("use", "--slug", slug, answers=(answer,))

    @rule(new=st.sampled_from(SLUGS))
    def split(self, new):  # needs an open run: write_run_file makes one
        self.run("split", "--new", new)

    @rule(data=st.data(), answer=st.sampled_from(["stash", "keep"]))
    def sync(self, data, answer):
        slug = data.draw(st.sampled_from(self.known()))
        self.run_sync(slug, answer)

    def run_sync(self, slug, answer):
        rc, out = sess(self.root, "sync", "--slug", slug)
        if rc == 4:
            rc, out = sess(self.root, "sync", "--slug", slug, "--untracked", answer)
        self.log.append((("sync", slug), (), rc))

    @rule(data=st.data(), answer=st.sampled_from(["stash", "keep"]))
    def close(self, data, answer):
        slug = data.draw(st.sampled_from(self.known()))
        self.run("close", "--slug", slug, answers=(answer,))

    @rule(data=st.data())
    def discard(self, data):
        slug = data.draw(st.sampled_from(self.known()))
        wts = self.worktrees()
        held, scopes = self.holdings(wts), self.scopes_of(slug, wts)
        gone = {t for t in self.tokens if (w := self.where_found(t, held)) and w <= scopes}
        rc, _ = sess(self.root, "discard", "--slug", slug)
        self.log.append((("discard", slug), (), rc))
        if rc == 0:
            self.tokens -= gone

    @rule(data=st.data())
    def edit_tracked(self, data):
        co = data.draw(st.sampled_from(self.checkouts()))
        t = self.token()
        with (co / "app.py").open("a", encoding="utf-8") as fh:
            fh.write(t + "\n")
        self.tokens.add(t)

    @rule(data=st.data())
    def add_untracked(self, data):
        co = data.draw(st.sampled_from(self.checkouts()))
        t = self.token()
        (co / f"u_{self.n}.txt").write_text(t + "\n", encoding="utf-8")
        self.tokens.add(t)

    @rule(data=st.data())
    def write_run_file(self, data):
        co = data.draw(st.sampled_from(self.checkouts()))
        owner = slug_of(self.worktrees().get(co.as_posix(), ""))
        run = co / ".superlite"
        run.mkdir(exist_ok=True)
        # a run on main has no owner yet: a split gives it one
        (run / "orchestrator.json").write_text(json.dumps({"owner": owner} if owner else {"mode": "ai"}), encoding="utf-8")

    @rule()
    def commit_on_main(self):
        self.n += 1
        host_main = next((Path(p) for p, b in self.worktrees().items() if b == "main"), None)
        if host_main is None:
            return
        if git(host_main, "status", "--porcelain", "--untracked-files=no").strip():
            return  # keep the commit from dragging a user edit into main
        with (host_main / "main.txt").open("a", encoding="utf-8") as fh:
            fh.write(f"main {self.n}\n")
        git(host_main, "commit", "-qam", f"main {self.n}")

    # ---- invariants (one scan per step) ----
    @invariant()
    def hold(self):
        wts = self.worktrees()
        ctx = f"steps: {self.log}"
        held = self.holdings(wts)
        for t in self.tokens:
            assert self.where_found(t, held), f"{t} lost; {ctx}"
        branches = [b for b in wts.values() if b]
        assert len(branches) == len(set(branches)), f"a branch is checked out twice: {wts}; {ctx}"
        reg_file = self.root / ".superlite" / "sessions.json"
        if reg_file.is_file():
            reg = json.loads(reg_file.read_text(encoding="utf-8"))
            in_reg = {Path(s["worktree"]).resolve().as_posix() for s in reg["sessions"]
                      if s.get("worktree") and Path(s["worktree"]).is_dir()} - {self.root.as_posix()}
            in_git = {Path(p).resolve().as_posix() for p in wts} - {self.root.as_posix()}
            assert in_reg == in_git, f"registry {in_reg} vs git {in_git}; {ctx}"
        for path, branch in wts.items():
            f = Path(path) / ".superlite" / "orchestrator.json"
            owner = json.loads(f.read_text(encoding="utf-8")).get("owner") if f.is_file() else None
            assert owner is None or owner == slug_of(branch), f"run file of {owner} in {path} on {branch}; {ctx}"


class _Pick:
    """Stands in for st.data(): draw returns a fixed choice."""
    def __init__(self, value):
        self.value = value

    def draw(self, _strategy):
        return self.value


def test_discarding_a_parallel_session_drops_only_its_tokens():
    """Pinned F-T9-1: a token in a parallel worktree under <root>/.claude/worktrees/ was also counted as held
    by the main checkout, so discarding that session kept tracking the dropped token and reported it lost."""
    m = SessionMachine()
    try:
        m.first_session()
        m.ensure("b", "stash")
        wt_b = next(Path(p) for p, b in m.worktrees().items() if b == "superlite/sessions/b")
        m.edit_tracked(_Pick(wt_b))
        m.add_untracked(_Pick(m.root))
        m.hold()
        m.discard(_Pick("b"))
        assert m.log[-1] == (("discard", "b"), (), 0)
        assert m.tokens == {"TOKEN2END"}, m.tokens
        m.hold()
    finally:
        m.teardown()


@pytest.mark.slow
def test_session_state_machine():
    SessionMachine.TestCase.settings = settings(
        max_examples=EXAMPLES, stateful_step_count=25, deadline=None, database=None,
        suppress_health_check=list(HealthCheck))
    case = SessionMachine.TestCase("runTest")
    case.runTest()
