import atexit
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROBES = Path(__file__).resolve().parents[2] / "skills/swarm/probes"
sys.path.insert(0, str(PROBES))
from pack import file as pfile  # noqa: E402


def _need_perms():
    if sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0):
        pytest.skip("permission bits not enforced here")


def test_twice(tmp_path):
    _need_perms()
    ctx = SimpleNamespace(tmp=tmp_path)
    for _ in range(2):
        path = pfile._unreadable(None, ctx)
        assert os.stat(path).st_mode & 0o777 == 0


def test_restored_at_exit(tmp_path, monkeypatch):
    _need_perms()
    registered = []
    monkeypatch.setattr(atexit, "register",
                        lambda fn, *args, **kw: registered.append((fn, args, kw)))
    ctx = SimpleNamespace(tmp=tmp_path)
    path = pfile._unreadable(None, ctx)
    assert os.stat(path).st_mode & 0o777 == 0
    assert registered, "no cleanup was registered"
    for fn, args, kw in registered:
        fn(*args, **kw)
    mode = os.stat(path).st_mode & 0o777
    assert mode & 0o600 == 0o600
