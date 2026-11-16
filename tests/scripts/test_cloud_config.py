import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "swarm" / "scripts"))
import cloud_config  # noqa: E402

TOK = "slc_" + "a" * 20


@pytest.fixture(autouse=True)
def homes(tmp_path, monkeypatch):
    sh = tmp_path / "shome"
    sh.mkdir()
    monkeypatch.setenv("SUPERLITE_HOME", str(sh))
    monkeypatch.setenv("CLAUDE_HOME", str(tmp_path / "chome"))
    monkeypatch.delenv("SUPERLITE_CLOUD_URL", raising=False)
    monkeypatch.delenv("SUPERLITE_CLOUD_TOKEN", raising=False)
    return sh


@pytest.fixture
def proj(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    return p


def nocb():
    return ""


def test_args_win(proj):
    r = cloud_config.find(proj, "https://c.example", "slc_abc", clipboard=nocb)
    assert r == {"url": "https://c.example", "token": "slc_abc", "source": "args", "warnings": []}


def test_order(proj, homes):
    (homes / ".env").write_text("SUPERLITE_CLOUD_URL=https://home.example\nSUPERLITE_CLOUD_TOKEN=slc_home\n")
    r = cloud_config.find(proj, clipboard=lambda: TOK)
    assert (r["url"], r["token"], r["source"]) == ("https://home.example", "slc_home", "SUPERLITE_HOME .env")
    (proj / ".env").write_text("SUPERLITE_CLOUD_URL=https://proj.example\nSUPERLITE_CLOUD_TOKEN=slc_proj\n")
    r = cloud_config.find(proj, clipboard=lambda: TOK)
    assert (r["url"], r["token"], r["source"]) == ("https://proj.example", "slc_proj", "project .env")
    r = cloud_config.find(proj, token="slc_arg", clipboard=lambda: TOK)
    assert r["token"] == "slc_arg" and r["url"] == "https://proj.example"


def test_clipboard_only_token(proj):
    r = cloud_config.find(proj, "https://c.example", clipboard=lambda: TOK + "\n")
    assert r["token"] == TOK and r["url"] == "https://c.example"
    r = cloud_config.find(proj, clipboard=lambda: "https://x.example")
    assert r["url"] is None


def test_clipboard_shape(proj):
    r = cloud_config.find(proj, "https://c.example", clipboard=lambda: "hello world")
    assert r["token"] is None
    r = cloud_config.find(proj, "https://c.example", clipboard=lambda: "slc_short")
    assert r["token"] is None
    r = cloud_config.find(proj, "https://c.example", clipboard=lambda: "slc_" + "b" * 25)
    assert r["token"] == "slc_" + "b" * 25


def test_clipboard_failure_ignored(proj):
    def boom():
        raise OSError("no clipboard")
    assert cloud_config.find(proj, "https://c.example", clipboard=boom)["token"] is None


def test_localhost_http_ok(proj):
    r = cloud_config.find(proj, "http://127.0.0.1:8787", "slc_abc", clipboard=nocb)
    assert r["warnings"] == []
    r = cloud_config.find(proj, "http://localhost:8787", "slc_abc", clipboard=nocb)
    assert r["warnings"] == []


def test_http_remote_warns(proj):
    r = cloud_config.find(proj, "http://10.0.0.5:8787", "slc_abc", clipboard=nocb)
    assert r["warnings"] == ["plain http to a non-local address"]


def _git(p, *a):
    subprocess.run(["git", *a], cwd=p, check=True, capture_output=True,
                   env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})


def test_tracked_env_warns(proj):
    _git(proj, "init", "-q")
    (proj / ".env").write_text("SUPERLITE_CLOUD_URL=http://10.0.0.5:8787\nSUPERLITE_CLOUD_TOKEN=slc_abc\n")
    _git(proj, "add", ".env")
    r = cloud_config.find(proj, clipboard=nocb)
    assert r == {"url": "http://10.0.0.5:8787", "token": "slc_abc", "source": "project .env",
                 "warnings": ["project .env holds a token git is not ignoring",
                              "plain http to a non-local address"]}


def test_ignored_env_no_warning(proj):
    _git(proj, "init", "-q")
    (proj / ".gitignore").write_text(".env\n")
    (proj / ".env").write_text("SUPERLITE_CLOUD_URL=https://c.example\nSUPERLITE_CLOUD_TOKEN=slc_abc\n")
    assert cloud_config.find(proj, clipboard=nocb)["warnings"] == []


def test_env_quotes_comments(proj):
    (proj / ".env").write_text('# c\nexport SUPERLITE_CLOUD_URL="https://q.example"\nSUPERLITE_CLOUD_TOKEN=\'slc_q\'\n')
    r = cloud_config.find(proj, clipboard=nocb)
    assert (r["url"], r["token"]) == ("https://q.example", "slc_q")


def test_save_load_clear(homes):
    p = cloud_config.save({"url": "https://c.example", "token": "slc_abc"})
    assert p == homes / "cloud.json"
    assert cloud_config.load() == {"url": "https://c.example", "token": "slc_abc", "statusline_prev": None}
    cloud_config.clear()
    assert cloud_config.load() is None
    cloud_config.clear()


def test_load_corrupt(homes):
    (homes / "cloud.json").write_text("{nope")
    assert cloud_config.load() is None


@pytest.mark.skipif(sys.platform == "win32", reason="posix modes")
def test_mode_600(homes):
    p = cloud_config.save({"url": "https://c.example", "token": "slc_abc"})
    assert p.stat().st_mode & 0o777 == 0o600
    cloud_config.save({"url": "https://c.example", "token": "slc_abc2"})
    assert p.stat().st_mode & 0o777 == 0o600


def test_token_never_printed(proj, capsys):
    cloud_config.find(proj, "http://10.0.0.5", "slc_secret", clipboard=nocb)
    cloud_config.save({"url": "u", "token": "slc_secret"})
    out = capsys.readouterr()
    assert "slc_secret" not in out.out + out.err
