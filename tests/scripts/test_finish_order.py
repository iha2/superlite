"""finish marks the run finished before it releases the suspended peers."""
from __future__ import annotations

import json
import subprocess

from test_plan_tools import SCRIPTS, run

EX = SCRIPTS / "exclusive.py"
PEER = "superpowers@claude-plugins-official"


def test_last_finish_releases_peers(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE", "1")
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    monkeypatch.delenv("SUPERLITE_NO_AUTO_SESSION", raising=False)
    repo = tmp_path / "app"
    repo.mkdir()
    for cmd in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", *cmd], cwd=repo, check=True)
    (repo / ".gitignore").write_text(".superlite/\n.claude/\n")
    (repo / "a.txt").write_text("a\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    session = SCRIPTS / "session.py"
    assert run(session, "ensure", "--slug", "a", cwd=repo).returncode == 0
    assert run(session, "create", "--slug", "b", "--worktree", cwd=repo).returncode == 0
    wt = repo / ".superlite" / "worktrees" / "sessions" / "b"
    run(EX, "on", cwd=repo)
    settings = repo / ".claude" / "settings.local.json"
    assert json.loads(settings.read_text(encoding="utf-8"))["enabledPlugins"][PEER] is False
    outs = []
    for cwd in (wt, repo):
        sl = cwd / ".superlite"
        sl.mkdir(exist_ok=True)
        (sl / "progress.md").write_text("# Progress ledger\n", encoding="utf-8")
        outs.append(run(SCRIPTS / "finish.py", "--ledger", sl / "progress.md", "--log", sl / "i.jsonl", cwd=cwd).out)
    assert "finished" in outs[0] and "suspended" in outs[0]
    assert "finished" in outs[1] and "clear" in outs[1]
    assert outs[1].index("session:") < outs[1].index("peers:")
    assert not (repo / ".superlite" / "peers.json").is_file()
    assert not settings.is_file()
