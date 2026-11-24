import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"))
import cloud  # noqa: E402
import cloud_client  # noqa: E402
import cloud_config  # noqa: E402

TOKEN = "slc_" + "s" * 24
ME = {"handle": "chris", "email": "chris@example.com", "role": "member", "workspace": "acme"}


class FakeClient:
    runs_data = [{"name": "login_fix", "owner": "chris", "status": "running", "agent": "claude"}]
    status_data = {"runs": [], "notices": []}
    calls = []

    def __init__(self, url, token, timeout=5.0):
        self.url, self.token = url, token

    def me(self):
        return dict(ME)

    def runs(self, all=False):
        FakeClient.calls.append(("runs", all))
        return list(self.runs_data)

    def status(self):
        return self.status_data

    def give(self, ref, email):
        FakeClient.calls.append(("give", ref, email))
        return {"id": "t_1", "name": ref, "owner": "m_a3374cec", "status": "running",
                "to_handle": "pat", "to_email": email}


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    sh, ch = tmp_path / "shome", tmp_path / "chome"
    sh.mkdir()
    monkeypatch.setenv("SUPERLITE_HOME", str(sh))
    monkeypatch.setenv("CLAUDE_HOME", str(ch))
    monkeypatch.chdir(tmp_path)
    FakeClient.calls = []
    FakeClient.status_data = {"runs": [], "notices": []}
    monkeypatch.setattr(cloud_client, "Client", FakeClient)
    if not hasattr(cloud_client, "CloudError"):  # T3 not built yet
        monkeypatch.setattr(cloud_client, "CloudError", type("CloudError", (Exception,), {}), raising=False)
    log = []
    st = types.SimpleNamespace(
        install=lambda settings, command: log.append(("install", Path(settings))),
        restore=lambda settings: log.append(("restore", Path(settings))),
        main=lambda argv: log.append(("status_main", argv)) or 0)
    en = types.SimpleNamespace(remove_all=lambda d: log.append(("remove_all", Path(d))) or 2,
                               sync=lambda *a, **k: [])
    monkeypatch.setitem(sys.modules, "cloud_status", st)
    monkeypatch.setitem(sys.modules, "cloud_entries", en)
    return types.SimpleNamespace(log=log, sh=sh, ch=ch)


def login(monkeypatch=None):
    return cloud.main(["login", "--url", "https://c.example", "--token", TOKEN])


def test_login_prints_who(capsys):
    assert login() == 0
    out = capsys.readouterr()
    assert "logged in as chris (chris@example.com)" in out.out
    assert TOKEN not in out.out + out.err
    assert cloud_config.load()["token"] == TOKEN


def test_login_warnings_printed(capsys, monkeypatch):
    monkeypatch.setattr(cloud_config, "find", lambda p, u=None, t=None, clipboard=None: {
        "url": "http://10.0.0.5:8787", "token": TOKEN, "source": "project .env",
        "warnings": ["plain http to a non-local address"]})
    assert cloud.main(["login"]) == 0
    out = capsys.readouterr()
    assert "plain http to a non-local address" in out.err
    assert "plain http" not in out.out
    assert TOKEN not in out.out + out.err


def test_login_missing(capsys, monkeypatch):
    monkeypatch.setattr(cloud_config, "find", lambda p, u=None, t=None, clipboard=None: {
        "url": None, "token": None, "source": None, "warnings": []})
    assert cloud.main(["login"]) == 2
    assert cloud_config.load() is None


def test_login_installs_statusline(env):
    login()
    assert ("install", env.ch / "settings.json") in env.log


def test_logout_restores(env, capsys):
    login()
    assert cloud.main(["logout"]) == 0
    assert "logged out" in capsys.readouterr().out
    assert ("restore", env.ch / "settings.json") in env.log
    assert any(e[0] == "remove_all" for e in env.log)
    assert cloud_config.load() is None


def test_runs_plain(capsys):
    login()
    capsys.readouterr()
    assert cloud.main(["runs"]) == 0
    assert capsys.readouterr().out.strip() == "login_fix  running"
    assert ("runs", False) in FakeClient.calls


def test_runs_all_flag(capsys):
    login()
    cloud.main(["runs", "--all"])
    assert ("runs", True) in FakeClient.calls


def test_runs_marks(capsys):
    login()
    capsys.readouterr()
    FakeClient.status_data = {"runs": [], "notices": [
        {"id": 3, "task": "login_fix", "text": "login_fix from chris", "seen": 0}]}
    cloud.main(["runs"])
    assert capsys.readouterr().out.strip() == "login_fix  running  new · given by chris"


def test_give(capsys):
    login()
    capsys.readouterr()
    assert cloud.main(["give", "login_fix", "pat@example.com"]) == 0
    assert ("give", "login_fix", "pat@example.com") in FakeClient.calls
    assert capsys.readouterr().out.strip() == "login_fix given to pat (pat@example.com)"


def test_not_logged_in(capsys):
    for cmd in (["runs"], ["give", "x", "a@b.c"]):
        assert cloud.main(cmd) == 2
        assert "not logged in: run superlite-cli cloud login" in capsys.readouterr().err


def test_cloud_error_hides_token(capsys, monkeypatch):
    login()
    capsys.readouterr()

    def boom(self, all=False):
        raise cloud_client.CloudError(f"401 bad token {TOKEN}")
    monkeypatch.setattr(FakeClient, "runs", boom)
    assert cloud.main(["runs"]) == 1
    out = capsys.readouterr()
    assert "401" in out.err and TOKEN not in out.out + out.err


def test_delegates(env, monkeypatch):
    seen = []
    for name in ("cloud_watch", "factory_hook", "factory_status"):
        monkeypatch.setitem(sys.modules, name, types.SimpleNamespace(
            main=lambda argv, n=name: seen.append((n, argv)) or 0))
    assert cloud.main(["watch", "login_fix"]) == 0
    assert cloud.main(["hook", "x"]) == 0
    assert cloud.main(["statusline"]) == 0
    assert seen == [("cloud_watch", ["login_fix"]), ("factory_hook", ["x"]), ("factory_status", [])]


def test_unknown_and_usage(capsys):
    assert cloud.main([]) == 2
    assert cloud.main(["nope"]) == 2


def test_runs_syncs_entries(monkeypatch, env):
    seen = []
    monkeypatch.setitem(sys.modules, "cloud_entries", types.SimpleNamespace(
        sync=lambda runs, d, me, admin: seen.append((runs, Path(d), me, admin)) or []))
    login()
    assert cloud.main(["runs"]) == 0
    assert seen and seen[0][2] == "chris" and seen[0][3] is False
    assert seen[0][1] == env.ch / "skills"


def test_attach_writes_state_and_detach_clears(monkeypatch, env, capsys):
    monkeypatch.setattr(FakeClient, "attach", lambda self, ref: {"run": "login_fix", "id": "t_1", "status": "running"},
                        raising=False)
    login()
    capsys.readouterr()
    assert cloud.main(["attach", "chris/login_fix"]) == 0
    out = capsys.readouterr()
    assert "attached chris/login_fix" in out.out and TOKEN not in out.out + out.err
    assert (env.sh / "attach.json").exists()
    assert cloud.main(["detach"]) == 0
    assert "detached" in capsys.readouterr().out
    assert not (env.sh / "attach.json").exists()


def test_attach_cloud_error_exits_1(monkeypatch, env, capsys):
    def boom(self, ref):
        raise cloud_client.CloudError(f"nope {TOKEN}")
    monkeypatch.setattr(FakeClient, "attach", boom, raising=False)
    login()
    capsys.readouterr()
    assert cloud.main(["attach", "x"]) == 1
    out = capsys.readouterr()
    assert "nope" in out.err and TOKEN not in out.out + out.err


def test_attach_not_logged_in(capsys):
    assert cloud.main(["attach", "x"]) == 2
    assert "not logged in" in capsys.readouterr().err


def test_help_lists_delegated_and_new_subcommands(capsys):
    assert cloud.main(["--help"]) == 0
    out = " ".join(capsys.readouterr().out.split())
    assert "attach,detach,watch,statusline,hook" in out


def test_logout_clears_attachment(env, capsys):
    login()
    (env.sh / "attach.json").write_text('{"run": "login_fix", "id": "t_1"}')
    assert cloud.main(["logout"]) == 0
    assert not (env.sh / "attach.json").exists()


def test_runs_reads_notices_before_listing(capsys, monkeypatch):
    """The list call marks notices seen on the server, so /api/status must be read first."""
    login()
    capsys.readouterr()
    order = []
    monkeypatch.setattr(FakeClient, "status", lambda self: order.append("status") or {"notices": []})
    monkeypatch.setattr(FakeClient, "runs", lambda self, all=False: order.append("runs") or [])
    cloud.main(["runs"])
    assert order == ["status", "runs"]


def test_give_detaches_given_run(capsys, env):
    login()
    (env.sh / "attach.json").write_text('{"run": "login_fix", "id": "t_1"}')
    assert cloud.main(["give", "login_fix", "pat@example.com"]) == 0
    assert not (env.sh / "attach.json").exists()
    (env.sh / "attach.json").write_text('{"run": "other", "id": "t_7"}')
    cloud.main(["give", "login_fix", "pat@example.com"])
    assert (env.sh / "attach.json").exists()
