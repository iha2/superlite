import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"))
import cloud_hook  # noqa: E402
from cloud_client import CloudError  # noqa: E402

URL = "https://c.example"
TOKEN = "slc_" + "t" * 24




class FakeClient:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.runs_data = [
            {"name": "login_fix", "owner": "chris", "status": "running", "agent": "claude"},
            {"name": "old_one", "owner": "chris", "status": "done", "agent": "claude"},
        ]

    def _do(self, *a):
        self.calls.append(a)
        if self.fail:
            raise CloudError(f"cloud unreachable: {URL}")

    def me(self):
        return {"handle": "chris", "email": "chris@example.com", "role": "member", "workspace": "acme"}

    def runs(self, all=False):
        return list(self.runs_data)

    def nudge(self, ref, text):
        self._do("nudge", ref, text)

    def respond(self, ref, text):
        self._do("respond", ref, text)

    def cancel(self, ref):
        self._do("cancel", ref)

    def attach(self, ref):
        self.calls.append(("attach", ref))
        name = ref.split("/")[-1]
        for r in self.runs_data:
            if r["name"] == name:
                return {"run": name, "id": "t_1", "status": r["status"]}
        raise CloudError("no such run")

    def give(self, ref, email):
        self.calls.append(("give", ref, email))
        return {"id": "t_1", "name": ref, "owner": "pat", "status": "running",
                "to_handle": "pat", "to_email": email}

    def task(self, ref):
        self.calls.append(("task", ref))
        for r in self.runs_data:
            if r.get("id") == ref:
                return dict(r)
        raise CloudError("not found")


@pytest.fixture
def fc(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERLITE_HOME", str(tmp_path))
    c = FakeClient()
    monkeypatch.setattr(cloud_hook, "_client", lambda: c)
    return c


def attach_state(tmp_path, name="login_fix"):
    (tmp_path / "attach.json").write_text(json.dumps({"run": name}))


@pytest.mark.parametrize("prompt,call", [
    (">>x", ("nudge", "login_fix", "x")),
    (">> run the tests first", ("nudge", "login_fix", "run the tests first")),
    (">>login_fix x", ("nudge", "login_fix", "x")),
    (">>chris/login_fix x", ("nudge", "chris/login_fix", "x")),
    ("/superlite:attach login_fix", ("attach", "login_fix")),
    ("/superlite:attach-login_fix", ("attach", "login_fix")),
    ("/superlite-attach-login_fix", ("attach", "login_fix")),
    ("/superlite:give login_fix pat@example.com", ("give", "login_fix", "pat@example.com")),
])
def test_prefixes(fc, prompt, call):
    r = cloud_hook.route(prompt, current="login_fix")
    assert r and r["block"] is True
    assert call in fc.calls


def test_prefixes_no_client_call(fc, tmp_path):
    attach_state(tmp_path)
    assert "detached" in cloud_hook.route("/superlite:detach")["reason"]
    assert not (tmp_path / "attach.json").exists()
    assert "login_fix" in cloud_hook.route("/superlite:runs")["reason"]


def test_watch(fc, monkeypatch):
    monkeypatch.delenv("HERDR_ENV", raising=False)
    r = cloud_hook.route("/superlite:watch login_fix")
    assert r["block"] and "cloud watch login_fix" in r["reason"]
    seen = []

    class P:
        stdout = "p7\n"
        returncode = 0
    monkeypatch.setenv("HERDR_ENV", "1")
    monkeypatch.setattr(cloud_hook, "_sh", lambda cmd: seen.append(cmd) or P())
    r = cloud_hook.route("/superlite:watch login_fix")
    assert r["block"]
    assert seen[0] == ["herdr", "pane", "split", "--current", "--direction", "right", "--no-focus"]
    assert seen[1][:4] == ["herdr", "pane", "run", "p7"] and "cloud watch login_fix" in seen[1][4]


def test_failed_send_blocks(fc):
    fc.fail = True
    r = cloud_hook.route(">>x", current="login_fix")
    assert r == {"block": True, "reason": f"nudge not sent: cloud unreachable: {URL}"}


def test_plain_prompt_passes(fc):
    assert cloud_hook.route("fix the bug") is None
    assert fc.calls == []


def test_current_defaults_to_newest_running(fc):
    r = cloud_hook.route(">>x")
    assert r["reason"] == "→ login_fix"
    assert ("nudge", "login_fix", "x") in fc.calls


def test_ack_reason(fc):
    assert cloud_hook.route(">> hi", current="login_fix") == {"block": True, "reason": "→ login_fix"}


def test_attached_lines(fc, tmp_path):
    attach_state(tmp_path)
    cloud_hook.route("yes")
    assert fc.calls[-1] == ("nudge", "login_fix", "yes")  # running: nudge
    fc.runs_data[0]["status"] = "waiting"
    cloud_hook.route("yes")
    assert fc.calls[-1] == ("respond", "login_fix", "yes")
    cloud_hook.route("/approve")
    assert fc.calls[-1] == ("respond", "login_fix", "approve")
    cloud_hook.route("/cancel")
    assert fc.calls[-1] == ("cancel", "login_fix")
    assert cloud_hook.route("\\explain this") == {"pass": "explain this"}
    assert cloud_hook.route("/other-command") is None


def test_ended_run(fc, tmp_path):
    r = cloud_hook.route("/superlite:attach old_one")
    assert r == {"block": True, "reason": "old_one has ended"}
    assert not (tmp_path / "attach.json").exists()


def test_attach_writes_state(fc, tmp_path):
    cloud_hook.route("/superlite:attach login_fix")
    assert json.loads((tmp_path / "attach.json").read_text())["run"] == "login_fix"


def run_main(monkeypatch, capsys, stdin):
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    assert cloud_hook.main([]) == 0
    return capsys.readouterr().out


def test_main_block_output(fc, monkeypatch, capsys):
    out = run_main(monkeypatch, capsys, json.dumps({"prompt": ">> hi"}))
    assert json.loads(out) == {"decision": "block", "reason": "→ login_fix"}


def test_main_fails_open(fc, monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("x " + TOKEN)
    monkeypatch.setattr(cloud_hook, "route", boom)
    assert run_main(monkeypatch, capsys, json.dumps({"prompt": ">>x"})) == ""
    assert run_main(monkeypatch, capsys, "not json") == ""


def test_not_logged_in_passes(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPERLITE_HOME", str(tmp_path))
    import cloud_config
    monkeypatch.setattr(cloud_config, "load", lambda: None)
    assert cloud_hook.route(">>x") is None


@pytest.mark.parametrize("prompt", [">> hi", ">>login_fix hi", "/superlite:runs", "/superlite:give login_fix a@b.c",
                                    "/superlite:attach login_fix"])
def test_dead_cloud_blocks_with_error(fc, monkeypatch, prompt):
    def dead(*a, **k):
        raise CloudError(f"cloud unreachable: {URL} {TOKEN}")
    for n in ("me", "runs", "give", "attach", "nudge"):
        monkeypatch.setattr(fc, n, dead)
    fc._token = TOKEN
    out = cloud_hook.route(prompt)
    assert out and out["block"] and "unreachable" in out["reason"] and TOKEN not in out["reason"]


def test_dead_cloud_attached_line_blocks(fc, tmp_path, monkeypatch):
    attach_state(tmp_path)
    monkeypatch.setattr(fc, "runs", lambda all=False: (_ for _ in ()).throw(CloudError("cloud unreachable")))
    out = cloud_hook.route("yes")
    assert out and out["block"] and "unreachable" in out["reason"]


def test_give_line(fc):
    r = cloud_hook.route("/superlite:give gift pat@example.com")
    assert r["reason"] == "gift given to pat (pat@example.com)"
    assert cloud_hook.give_line({"name": "gift"}, "gift", "p@x.io") == "gift given to p@x.io"


def attach_id(tmp_path, name="login_fix", id_="t_9"):
    (tmp_path / "attach.json").write_text(json.dumps({"run": name, "id": id_}))


def test_attached_routes_by_id_and_follows_rename(fc, tmp_path):
    fc.runs_data = [{"id": "t_9", "name": "login_fix_2", "status": "running"},
                    {"id": "t_1", "name": "login_fix", "status": "failed"}]
    attach_id(tmp_path)
    assert cloud_hook.route("yes")["reason"] == "\u2192 login_fix_2"
    assert ("nudge", "t_9", "yes") in fc.calls
    assert json.loads((tmp_path / "attach.json").read_text()) == {"run": "login_fix_2", "id": "t_9"}
    assert cloud_hook.route(">> hi") == {"block": True, "reason": "\u2192 login_fix_2"}
    assert ("nudge", "t_9", "hi") in fc.calls
    cloud_hook.route("/approve")
    assert ("respond", "t_9", "approve") in fc.calls
    cloud_hook.route("/cancel")
    assert ("cancel", "t_9") in fc.calls


def test_attached_run_ended_detaches_and_passes(fc, tmp_path):
    fc.runs_data = [{"id": "t_9", "name": "login_fix", "status": "failed"}]
    attach_id(tmp_path)
    r = cloud_hook.route("next thing")
    assert r == {"context": "login_fix has ended; detached"}
    assert not (tmp_path / "attach.json").exists()
    assert cloud_hook.route("again") is None


def test_main_ended_adds_context(fc, tmp_path, monkeypatch, capsys):
    fc.runs_data = [{"id": "t_9", "name": "login_fix", "status": "done"}]
    attach_id(tmp_path)
    out = json.loads(run_main(monkeypatch, capsys, json.dumps({"prompt": "go on"})))
    assert out["hookSpecificOutput"]["additionalContext"] == "login_fix has ended; detached"
    assert "decision" not in out
